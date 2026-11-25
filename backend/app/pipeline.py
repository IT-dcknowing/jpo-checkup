"""Orchestration d'un dépôt : lecture → IA → calcul → rapport → envoi (reprenable à chaque étape)."""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import mailer, report
from .anonymize import anonymize
from .config import Settings
from .db import TERMINAUX, Database, loads, now_iso
from .extraction import Extraction, LectureImpossible, extract
from .llm import LLMClient, LLMError
from .readers import ReaderError, read_file
from .schemas import ND, DepotForm
from .security import Cipher, sign_token

log = logging.getLogger("jpo.pipeline")

# Statut interne -> (étape affichée par le front, progression minimale %)
FRONT_ETAPE = {"EN_ATTENTE": "EN_ATTENTE", "LECTURE": "LECTURE", "MAPPING_IA": "MAPPING_IA", "CALCUL": "CALCUL", "RAPPORT": "PDF",
               "ENVOYE": "ENVOYE", "TERMINE": "ENVOYE", "ERREUR": "ERREUR"}
TYPES_FICHIERS = {"balance": "balance générale", "cr": "compte de résultat", "bilan": "bilan", "autre": "autre document"}
MSG_PUBLIC_LECTURE = "Rapport automatique annulé : vos documents n'ont pas pu être lus de façon fiable. Un conseiller DC-KNOWING vous rappelle pour réaliser votre diagnostic avec vous."
MSG_PUBLIC_TECH = "Votre diagnostic n'a pas pu être produit automatiquement. Un conseiller DC-KNOWING vous rappelle très vite."


def clean_name(s: str) -> str:
    """Une seule ligne, sans caractères ayant un sens dans le Markdown du rapport."""
    return re.sub(r"\s+", " ", re.sub(r"[\r\n\t*`|<>#\[\]]", " ", s)).strip()


def build_data(form: DepotForm, ref: str, ia: dict) -> dict:
    """Assemble l'entrée du moteur : IA (chiffres) + formulaire (conformité)."""
    decl = lambda s: s if s in ("A_JOUR", "EN_RETARD") else ND
    if form.secteur_exempte_fne:
        fne = "NON_APPLICABLE"  # jamais d'exemption sur la seule parole du formulaire : elle dépend du secteur
    else:
        fne = form.fne if form.fne in ("ACTIVE", "EN_COURS_ACTIVATION", "NON_ACTIVE") else ND
    return {
        "entreprise": {"nom_affiche": clean_name(form.raison_sociale), "secteur": form.secteur_label, "effectif": form.effectif,
                       "jour_evenement": form.jour_evenement, "reference_job": ref},
        "formulaire": {"regime_fiscal_declare": form.regime_fiscal, "fne": form.fne, "tva_exoneree": form.tva_exoneree},
        "ia": {
            "meta": ia["meta"], "compte_resultat": ia["compte_resultat"], "bilan": ia["bilan"],
            "conformite": {
                "dgi": {"statut": decl(form.dgi), "penalites_connues": ND},
                "cnps": {"statut": decl(form.cnps)},
                "fne": {"statut": fne},
                "regime_fiscal": {"regime_actuel": form.regime_fiscal if form.regime_fiscal != "NE_SAIT_PAS" else ND},
                "obligations_sociales": {"its": decl(form.its), "cmu": decl(form.cmu), "disa": decl(form.disa)},
            },
        },
    }


def fr_conf(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


class Pipeline:
    def __init__(self, settings: Settings, db: Database, cipher: Cipher, llm: LLMClient) -> None:
        self.s, self.db, self.cipher, self.llm = settings, db, cipher, llm

    # ------------------------------------------------------------------ utilitaires
    def _log(self, job_id: str, statut: str, pct: int, message: str, niveau: str = "", etape: str | None = None) -> None:
        """Change d'étape et ajoute un événement au journal, relu par le flux SSE du front."""
        self.db.log_event(job_id, statut, pct, {"etape": etape or FRONT_ETAPE[statut], "progression": pct, "message": message, "niveau": niveau})

    def _purge_uploads(self, job_id: str) -> None:
        shutil.rmtree(self.s.uploads_dir / job_id, ignore_errors=True)

    def download_token(self, job_id: str) -> str:
        return sign_token(self.s.secret_key, "dl", job_id, self.s.download_ttl)

    def download_link(self, job: dict) -> str:
        kind = "pdf" if job.get("pdf_path") else "docx"
        return f"{self.s.public_base_url}/api/fichiers/{job['id']}/{kind}?token={self.download_token(job['id'])}"

    # ------------------------------------------------------------------ point d'entrée
    def process(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if not job or job["statut"] in TERMINAUX:
            return
        form = DepotForm.model_validate_json(self.cipher.dec(job["form_enc"]))
        try:
            self._run(job, form)
        except LectureImpossible as e:
            self._fail(job, form, str(e), MSG_PUBLIC_LECTURE)
        except ReaderError as e:
            self._fail(job, form, f"fichier illisible : {e}", MSG_PUBLIC_LECTURE)
        except LLMError as e:
            log.error("job %s : IA indisponible : %s", job_id, e)
            self._fail(job, form, f"IA : {e}", MSG_PUBLIC_TECH)
        except report.RenderError as e:
            log.error("job %s : rendu : %s", job_id, e)
            self._fail(job, form, f"rendu : {e}", MSG_PUBLIC_TECH)
        except Exception as e:  # noqa: BLE001 — jamais de job bloqué : on journalise et on marque l'erreur
            log.exception("job %s : erreur inattendue", job_id)
            self._fail(job, form, f"{type(e).__name__}: {e}", MSG_PUBLIC_TECH)

    def _fail(self, job: dict, form: DepotForm, detail: str, public: str) -> None:
        pct = (self.db.get_job(job["id"]) or job)["progression"]
        self.db.update_job(job["id"], erreur=detail[:1500], erreur_publique=public, completed_at=now_iso())
        self._log(job["id"], "ERREUR", pct, public, "erreur")
        if mailer.configured(self.s):
            try:
                mailer.send_apology(self.s, form.contact_email, job["ref"])
            except Exception:  # noqa: BLE001
                log.exception("job %s : e-mail d'excuse non envoyé", job["id"])

    # ------------------------------------------------------------------ étapes
    def _extraction(self, job: dict, form: DepotForm) -> dict:
        job_id = job["id"]
        if job["resultat_ia_json"]:  # reprise : lecture et IA déjà faites
            return json.loads(job["resultat_ia_json"])
        fichiers = loads(job["fichiers_json"], [])
        self._log(job_id, "LECTURE", 3, f"Fichiers reçus : {len(fichiers)} document{'s' if len(fichiers) > 1 else ''}")
        textes, avertissements, genres = [], [], []
        for i, f in enumerate(fichiers):
            path = Path(f["path"])
            if not path.exists():
                raise ReaderError(f"fichier « {f['nom']} » introuvable (supprimé après 24 h)")
            try:
                r = read_file(path)
            except ReaderError as e:
                raise ReaderError(f"« {f['nom']} » : {e}") from e
            avertissements += [f"{f['nom']} : {w}" for w in r.warnings]
            genres.append(r.kind.upper())
            nature = TYPES_FICHIERS.get(f.get("type", ""), "document")
            textes.append(f"=== FICHIER {i + 1} ({r.kind.upper()} · {nature}) ===\n{anonymize(r.text, form)}")
        self._log(job_id, "LECTURE", 17, f"Texte extrait ({', '.join(genres)})")
        self._log(job_id, "MAPPING_IA", 24, "Anonymisation : noms, NCC, RIB et téléphones remplacés")
        ext: Extraction = extract(self.llm, form, "\n\n".join(textes), self.s.context_max_chars)
        ext.ia["meta"]["warnings"] += avertissements
        self._log(job_id, "MAPPING_IA", 40, f"Extraction IA : confiance globale {fr_conf(ext.confiance_globale)}")
        self._log(job_id, "MAPPING_IA", 43, f"Contrôles de cohérence terminés · données exploitées {ext.couverture_pct} %")
        saved = {"ia": ext.ia, "couverture_pct": ext.couverture_pct, "confiance": ext.confiance_globale,
                 "partielle": ext.lecture_partielle, "modele": ext.modele}
        self.db.update_job(job_id, resultat_ia_json=json.dumps(saved, ensure_ascii=False),
                           lecture_partielle=int(ext.lecture_partielle), modele_ia=ext.modele)
        self._purge_uploads(job_id)  # les fichiers bruts ne servent plus
        return saved

    def _run(self, job: dict, form: DepotForm) -> None:
        job_id, ref = job["id"], job["ref"]
        saved = self._extraction(job, form)

        self._log(job_id, "CALCUL", 51, "Indicateurs calculés (SYSCOHADA révisé)")
        data = build_data(form, ref, saved["ia"])
        faits = report.compute(self.s, data)
        self.db.update_job(job_id, score_performance=faits["score_performance"], grade_performance=faits["grade_performance"],
                           score_conformite=faits["score_conformite"], grade_conformite=faits["grade_conformite"],
                           couverture_pct=faits["couverture_pct"], alertes_json=json.dumps(faits["alertes"], ensure_ascii=False),
                           resume_json=json.dumps(faits["resume"], ensure_ascii=False))
        self._log(job_id, "CALCUL", 58, f"Performance {faits['score_performance']}/100 ({faits['grade_performance']}) · "
                                       f"Conformité {faits['score_conformite']}/100 ({faits['grade_conformite']})")

        self._log(job_id, "RAPPORT", 72, "Rapport rédigé en langage simple", etape="INTERPRETATION")
        res = report.build_report(self.s, data, self.s.reports_dir / job_id, f"Diagnostic_DC-KNOWING_{ref}")
        plan = res.resume.get("plan", [])
        self.db.update_job(job_id, docx_path=str(res.docx), md_path=str(res.md), pdf_path=str(res.pdf) if res.pdf else None,
                           pages=res.pages, plan_action_json=json.dumps(plan, ensure_ascii=False))
        self._log(job_id, "RAPPORT", 80, f"Plan d'action : {len(plan)} action{'s' if len(plan) > 1 else ''} priorisée{'s' if len(plan) > 1 else ''}")
        self._log(job_id, "RAPPORT", 85, f"Rapport généré : {res.pages} pages" if res.pages else "Rapport Word généré")
        note = None
        if res.pages is not None and res.pages > 4:
            note = f"rapport de {res.pages} pages même en mode compact : à raccourcir à la main"
        elif res.pages is None:
            note = "nombre de pages non contrôlé (LibreOffice absent)"
        elif not res.pdf:
            note = "PDF non généré"

        job = self.db.get_job(job_id) or job
        if not mailer.configured(self.s):
            self.db.update_job(job_id, completed_at=now_iso(),
                               erreur="; ".join(x for x in (note, "e-mail non envoyé : SMTP non configuré") if x))
            self._log(job_id, "TERMINE", 100, "Rapport prêt · disponible dans Mes diagnostics", "ok")
            return
        try:
            mailer.send_report(self.s, form.contact_email, form.contact_nom, faits, faits["resume"]["verdict"], res.docx, res.pdf, self.download_link(job))
        except Exception as e:  # noqa: BLE001 — le rapport existe : on ne perd pas le travail
            log.exception("job %s : envoi e-mail en échec", job_id)
            self.db.update_job(job_id, completed_at=now_iso(), erreur=f"e-mail non envoyé : {type(e).__name__}")
            self._log(job_id, "TERMINE", 100, "Rapport prêt · disponible dans Mes diagnostics", "ok")
            return
        self.db.update_job(job_id, email_envoye_at=now_iso(), completed_at=now_iso(), erreur=note)
        self._log(job_id, "ENVOYE", 100, "E-mail envoyé · ajouté à Mes diagnostics", "ok")

    def resend(self, job_id: str) -> None:
        """Renvoie l'e-mail du rapport (action conseiller). Lève ValueError si impossible."""
        job = self.db.get_job(job_id)
        if not job or not job["docx_path"] or not Path(job["docx_path"]).exists():
            raise ValueError("Le rapport n'existe plus : relancez le traitement.")
        if not mailer.configured(self.s):
            raise ValueError("L'envoi d'e-mails n'est pas configuré (SMTP).")
        form = DepotForm.model_validate_json(self.cipher.dec(job["form_enc"]))
        faits = {"score_performance": job["score_performance"], "grade_performance": job["grade_performance"],
                 "score_conformite": job["score_conformite"], "grade_conformite": job["grade_conformite"]}
        verdict = (loads(job["resume_json"], {}) or {}).get("verdict", "")
        pdf = Path(job["pdf_path"]) if job["pdf_path"] else None
        mailer.send_report(self.s, form.contact_email, form.contact_nom, faits, verdict, Path(job["docx_path"]), pdf, self.download_link(job))
        self.db.update_job(job_id, email_envoye_at=now_iso(), statut="ENVOYE")


class JobRunner:
    """File d'attente en mémoire : N workers (threads). Les dépôts inachevés sont relancés au démarrage."""

    def __init__(self, pipeline: Pipeline, workers: int) -> None:
        self.pipeline = pipeline
        self.pool = ThreadPoolExecutor(max_workers=max(workers, 1), thread_name_prefix="jpo-worker")
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, job_id: str) -> None:
        with self._lock:  # un même dépôt n'est jamais traité deux fois en parallèle
            if job_id in self._inflight:
                return
            self._inflight.add(job_id)
        self.pool.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        try:
            self.pipeline.process(job_id)
        finally:
            with self._lock:
                self._inflight.discard(job_id)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

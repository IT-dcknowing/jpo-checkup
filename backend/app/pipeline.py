"""Orchestration d'un dépôt : lecture (Supabase Storage) → IA → calcul → rapport (Storage) → envoi (Edge Function).
Reprenable à chaque étape : le résultat de l'IA est enregistré dès qu'il existe."""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from . import report
from .anonymize import anonymize
from .comprehension import comprendre
from .config import Settings
from .db import TERMINAUX, Database, now_iso
from .extraction import DocumentsNonReconnus, Extraction, LectureImpossible, extract
from .llm import LLMClient, LLMError
from .pseudonymise import Pseudonymes, pseudonymiser_tableaux
from .readers import ReaderError, read_file
from .schemas import ND, DepotForm
from .supa import Supabase, SupabaseError

log = logging.getLogger("jpo.pipeline")

# Statut interne -> (étape affichée par le front, progression minimale %)
FRONT_ETAPE = {"EN_ATTENTE": "EN_ATTENTE", "LECTURE": "LECTURE", "MAPPING_IA": "MAPPING_IA", "CALCUL": "CALCUL", "RAPPORT": "PDF",
               "ENVOYE": "ENVOYE", "TERMINE": "ENVOYE", "ERREUR": "ERREUR"}
TYPES_FICHIERS = {"balance": "balance générale", "cr": "compte de résultat", "bilan": "bilan", "autre": "autre document"}
MSG_PUBLIC_LECTURE = "Rapport automatique annulé : vos documents n'ont pas pu être lus de façon fiable. Un conseiller DC-KNOWING vous rappelle pour réaliser votre diagnostic avec vous."
MSG_PUBLIC_NON_RECONNU = ("Aucun montant comptable exploitable n'a été trouvé dans vos fichiers (ventes, achats, salaires, comptes). "
                          "Déposez de préférence votre balance générale, ou un journal de ventes, d'achats ou de paie ; "
                          "un conseiller DC-KNOWING peut aussi vous rappeler pour le faire avec vous.")
MSG_PUBLIC_TECH = "Votre diagnostic n'a pas pu être produit automatiquement. Un conseiller DC-KNOWING vous rappelle très vite."
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXT_LECTURE = {".csv", ".xlsx", ".pdf"}


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
    def __init__(self, settings: Settings, db: Database, sb: Supabase, llm: LLMClient) -> None:
        self.s, self.db, self.sb, self.llm = settings, db, sb, llm

    # ------------------------------------------------------------------ utilitaires
    def _log(self, job_id: str, statut: str, pct: int, message: str, niveau: str = "", etape: str | None = None) -> None:
        """Change d'étape et ajoute un événement au journal, relu par l'écran de suivi du front."""
        self.db.log_event(job_id, statut, pct, {"etape": etape or FRONT_ETAPE[statut], "progression": pct, "message": message, "niveau": niveau,
                                                 "t": now_iso()})

    def _purge_uploads(self, job_id: str) -> None:
        """Les fichiers bruts ne servent plus après l'analyse (le résultat de l'IA est enregistré) : fichiers et lignes
        documents supprimés. Une relance repart du résultat enregistré."""
        try:
            docs = self.db.job_documents(job_id)
            self.sb.remove("documents", [d["chemin"] for d in docs])
            self.db.delete_documents([d["id"] for d in docs])
        except SupabaseError:
            log.exception("job %s : suppression des fichiers bruts en échec (purge quotidienne en filet)", job_id)

    def _email(self, body: dict, user_token: str | None) -> None:
        """Appel de l'Edge Function d'envoi ; désactivé sur les préversions (EMAILS_PREVIEW=1 pour l'autoriser)."""
        if not self.s.emails_actifs:
            raise RuntimeError("e-mails désactivés sur les préversions (EMAILS_PREVIEW)")
        self.sb.invoke("envoyer-rapport", body, user_token)

    # ------------------------------------------------------------------ point d'entrée
    def process(self, job_id: str, user_token: str | None = None) -> None:
        """Traite un dépôt. `user_token` : jeton du visiteur, transmis à l'Edge Function d'envoi (contrôle de propriété)."""
        job = self.db.get_job(job_id)
        if not job or job["statut"] in TERMINAUX:
            return
        form = DepotForm.model_validate(job["form"])
        try:
            self._run(job, form, user_token)
        except DocumentsNonReconnus as e:
            self._fail(job, f"documents non reconnus : {e}", MSG_PUBLIC_NON_RECONNU, user_token, motif="non_reconnu")
        except LectureImpossible as e:
            self._fail(job, str(e), MSG_PUBLIC_LECTURE, user_token, motif="illisible")
        except ReaderError as e:
            self._fail(job, f"fichier illisible : {e}", MSG_PUBLIC_LECTURE, user_token)
        except LLMError as e:
            log.error("job %s : IA indisponible : %s", job_id, e)
            self._fail(job, f"IA : {e}", MSG_PUBLIC_TECH, user_token)
        except report.RenderError as e:
            log.error("job %s : rendu : %s", job_id, e)
            self._fail(job, f"rendu : {e}", MSG_PUBLIC_TECH, user_token)
        except Exception as e:  # noqa: BLE001 — jamais de job bloqué : on journalise et on marque l'erreur
            log.exception("job %s : erreur inattendue", job_id)
            self._fail(job, f"{type(e).__name__}: {e}", MSG_PUBLIC_TECH, user_token)
        finally:
            try:
                self.db.release(job_id)
            except Exception:  # noqa: BLE001
                log.exception("job %s : verrou non libéré", job_id)

    def _fail(self, job: dict, detail: str, public: str, user_token: str | None, motif: str = "technique") -> None:
        pct = (self.db.get_job(job["id"]) or job)["progression"]
        self.db.update_job(job["id"], erreur=detail[:1500], erreur_publique=public, completed_at=now_iso())
        self._log(job["id"], "ERREUR", pct, public, "erreur")
        try:  # e-mail d'excuse (le conseiller est en copie)
            self._email({"rapport_id": job["id"], "type": "excuse", "motif": motif}, user_token)
        except Exception:  # noqa: BLE001
            log.exception("job %s : e-mail d'excuse non envoyé", job["id"])

    # ------------------------------------------------------------------ étapes
    def _extraction(self, job: dict, form: DepotForm) -> dict:
        job_id = job["id"]
        if job["resultat_ia"]:  # reprise : lecture et IA déjà faites
            return job["resultat_ia"]
        fichiers = self.db.job_documents(job_id)
        self._log(job_id, "LECTURE", 3, f"Fichiers reçus : {len(fichiers)} document{'s' if len(fichiers) > 1 else ''}")
        textes, avertissements, genres = [], [], []
        locaux: list[tuple[str, Path]] = []
        pseudo, total = Pseudonymes(), 0
        with tempfile.TemporaryDirectory(prefix="jpo-") as tmp:
            for i, f in enumerate(fichiers):
                ext = Path(f["nom_fichier"]).suffix.lower()
                if ext not in EXT_LECTURE:
                    raise ReaderError(f"« {f['nom_fichier']} » : format non lu automatiquement (CSV, XLSX ou PDF)")
                try:
                    data = self.sb.download("documents", f["chemin"])
                except SupabaseError as e:
                    raise ReaderError(f"fichier « {f['nom_fichier']} » introuvable (supprimé après 24 h)") from e
                total += len(data)
                if len(data) > self.s.max_upload_bytes or total > self.s.max_total_bytes:
                    raise ReaderError(f"fichiers trop volumineux ({total // (1024 * 1024)} Mo au total, "
                                      f"{self.s.max_total_bytes // (1024 * 1024)} Mo maximum)")
                path = Path(tmp) / f"{i}{ext}"
                path.write_bytes(data)
                locaux.append((f["nom_fichier"], path))
                try:
                    r = read_file(path)
                except ReaderError as e:
                    raise ReaderError(f"« {f['nom_fichier']} » : {e}") from e
                avertissements += [f"{f['nom_fichier']} : {w}" for w in r.warnings]
                genres.append(r.kind.upper())
                nature = TYPES_FICHIERS.get(f.get("categorie", ""), "document")
                texte = r.text
                if r.kind in ("csv", "xlsx"):  # tiers (clients, fournisseurs, salariés) pseudonymisés, paie non transmise
                    try:
                        texte = pseudonymiser_tableaux(path, f["nom_fichier"], pseudo) or "[tableau vide]"
                    except Exception:  # noqa: BLE001 — en cas de doute, rien ne part
                        log.exception("job %s : pseudonymisation impossible (%s)", job_id, f["nom_fichier"])
                        texte = "[contenu non transmis : structure du tableau non analysée]"
                        avertissements.append(f"{f['nom_fichier']} : contenu non transmis à l'IA (structure non analysée)")
                textes.append(f"=== FICHIER {i + 1} ({r.kind.upper()} · {nature}) ===\n{anonymize(texte, form)}")
            comp = comprendre(locaux, form.tva_exoneree)  # type de chaque tableau et totaux exacts
        comp.documents = [anonymize(d, form) for d in comp.documents]  # noms de fichiers : peuvent contenir la raison sociale
        comp.avertissements = [anonymize(a, form) for a in comp.avertissements]
        for fait in comp.faits.values():
            fait.source = anonymize(fait.source, form)
        self._log(job_id, "LECTURE", 17, f"Texte extrait ({', '.join(genres)})")
        if comp.documents:
            types = sorted({d.split(" : ", 1)[1].split(" (")[0] for d in comp.documents})
            self._log(job_id, "LECTURE", 20, f"Documents reconnus : {', '.join(types)}")
        if comp.faits:
            self._log(job_id, "LECTURE", 22, f"Totaux calculés sur vos fichiers : {len(comp.faits)} poste{'s' if len(comp.faits) > 1 else ''}")
        self._log(job_id, "MAPPING_IA", 24, "Anonymisation : vos identifiants et les noms des tiers remplacés")
        ext_: Extraction = extract(self.llm, form, "\n\n".join(textes), self.s.context_max_chars, comp)
        ext_.ia["meta"]["warnings"] += avertissements
        self._log(job_id, "MAPPING_IA", 40, f"Extraction IA : confiance globale {fr_conf(ext_.confiance_globale)}")
        self._log(job_id, "MAPPING_IA", 43, f"Contrôles de cohérence terminés · données exploitées {ext_.couverture_pct} %")
        if ext_.couverture_pct < 60:
            self._log(job_id, "MAPPING_IA", 45, "Documents partiels : le rapport porte sur les parties disponibles (note indicative)")
        saved = {"ia": ext_.ia, "couverture_pct": ext_.couverture_pct, "confiance": ext_.confiance_globale,
                 "partielle": ext_.lecture_partielle, "modele": ext_.modele}
        self.db.update_job(job_id, resultat_ia=saved, lecture_partielle=bool(ext_.lecture_partielle), modele_ia=ext_.modele)
        self._purge_uploads(job_id)
        return saved

    def _run(self, job: dict, form: DepotForm, user_token: str | None) -> None:
        job_id, ref = job["id"], job["ref"]
        saved = self._extraction(job, form)

        self._log(job_id, "CALCUL", 51, "Indicateurs calculés (SYSCOHADA révisé)")
        data = build_data(form, ref, saved["ia"])
        faits = report.compute(self.s, data)
        self.db.update_job(job_id, score_performance=faits["score_performance"], grade_performance=faits["grade_performance"],
                           score_conformite=faits["score_conformite"], grade_conformite=faits["grade_conformite"],
                           couverture_pct=faits["couverture_pct"], alertes=faits["alertes"], resume=faits["resume"])
        self._log(job_id, "CALCUL", 58, f"Performance {faits['score_performance']}/100 ({faits['grade_performance']}) · "
                                       f"Conformité {faits['score_conformite']}/100 ({faits['grade_conformite']})")

        self._log(job_id, "RAPPORT", 72, "Rapport rédigé en langage simple", etape="INTERPRETATION")
        with tempfile.TemporaryDirectory(prefix="jpo-rapport-") as tmp:
            res = report.build_report(self.s, data, Path(tmp), f"Diagnostic_DC-KNOWING_{ref}")
            base = f"{job['owner']}/{job_id}"
            self.sb.upload("rapports", f"{base}.docx", res.docx.read_bytes(), DOCX_MIME)
            self.sb.upload("rapports", f"{base}.md", res.md.read_bytes(), "text/markdown")
        plan = res.resume.get("plan", [])
        self.db.update_job(job_id, docx_path=f"{base}.docx", md_path=f"{base}.md", pages=res.pages, plan_action=plan)
        self._log(job_id, "RAPPORT", 80, f"Plan d'action : {len(plan)} action{'s' if len(plan) > 1 else ''} priorisée{'s' if len(plan) > 1 else ''}")
        self._log(job_id, "RAPPORT", 88, "Rapport Word généré · envoi de l'e-mail")
        self.db.update_job(job_id, completed_at=now_iso())

        try:  # l'Edge Function vérifie la propriété, signe un lien de 7 jours, envoie l'e-mail et passe le statut à « envoye »
            self._email({"rapport_id": job_id}, user_token)
        except Exception as e:  # noqa: BLE001 — le rapport existe : on ne perd pas le travail
            log.exception("job %s : envoi e-mail en échec", job_id)
            self.db.update_job(job_id, erreur=f"e-mail non envoyé : {str(e)[:300]}")
            # le visiteur est prévenu : rapport prêt mais pas d'e-mail (niveau « alerte », pas « ok »)
            self._log(job_id, "TERMINE", 100, "Rapport prêt, mais l'e-mail n'a pas pu partir : téléchargez-le ci-dessous "
                                              "ou dans Mes diagnostics", "alerte")
            self.sb.update("rapports", {"id": f"eq.{job_id}"}, {"statut": "erreur"})  # rapport généré, envoi en échec
            return
        self._log(job_id, "ENVOYE", 100, "E-mail envoyé · ajouté à Mes diagnostics", "ok")

    def resend(self, job_id: str) -> None:
        """Renvoie l'e-mail du rapport (action conseiller, avec la clé service_role). Lève ValueError si impossible."""
        job = self.db.get_job(job_id)
        if not job or not job["docx_path"]:
            raise ValueError("Le rapport n'existe plus : relancez le traitement.")
        self._email({"rapport_id": job_id}, None)
        if job["statut"] != "ENVOYE":
            self._log(job_id, "ENVOYE", 100, "E-mail renvoyé par un conseiller", "ok")

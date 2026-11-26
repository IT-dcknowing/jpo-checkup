"""Accès aux données (Supabase Postgres via PostgREST, clé service_role) : rapports, documents, entretiens, audit.

Un « job » est une ligne de public.rapports, renvoyée sous la forme d'un dict avec les clés historiques du pipeline
(id, ref, owner, statut = étape fine, ...) pour garder le reste du code inchangé.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .supa import Supabase, SupabaseError

TERMINAUX = ("ENVOYE", "TERMINE", "ERREUR")

# clé du pipeline -> colonne de public.rapports
COLONNES = {
    "statut": "etape", "progression": "progression", "score_performance": "score_performance",
    "grade_performance": "grade_performance", "score_conformite": "score_conformite", "grade_conformite": "grade_conformite",
    "couverture_pct": "couverture_pct", "docx_path": "chemin_fichier", "md_path": "chemin_markdown", "pages": "pages",
    "lecture_partielle": "lecture_partielle", "modele_ia": "modele_ia", "email_envoye_at": "envoye_le", "erreur": "erreur",
    "erreur_publique": "erreur_publique", "rappel_demande": "rappel_demande", "rappel_effectue": "rappel_effectue",
    "entretien_realise": "entretien_realise", "completed_at": "termine_le", "traitement_depuis": "traitement_depuis",
}
# clés rangées dans la colonne JSON « donnees »
DONNEES = ("resultat_ia", "alertes", "plan_action", "resume")
STATUT_GLOBAL = {"ERREUR": "erreur", "ENVOYE": "envoye", "TERMINE": "genere"}


class QuotaDepasse(Exception):
    """Trop de dépôts sur l'heure écoulée."""


class DocumentsIndisponibles(Exception):
    """Documents introuvables, d'un autre compte, ou déjà rattachés à un dossier (y compris par une requête simultanée)."""


def _ids(values: list[str]) -> str:
    return f"in.({','.join(values)})"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _job(row: dict) -> dict:
    d = row.get("donnees") or {}
    return {
        "id": row["id"], "ref": row["reference"], "owner": row["user_id"], "email": row["email"], "titre": row["titre"],
        "statut": row["etape"], "statut_global": row["statut"], "progression": row["progression"],
        "jour_evenement": str(row["jour_evenement"]), "secteur": row["secteur"], "form": row["formulaire"],
        "resultat_ia": d.get("resultat_ia"), "alertes": d.get("alertes") or [], "plan_action": d.get("plan_action") or [],
        "resume": d.get("resume") or {}, "donnees": d, "journal": row.get("journal") or [],
        "score_performance": row["score_performance"], "grade_performance": row["grade_performance"],
        "score_conformite": row["score_conformite"], "grade_conformite": row["grade_conformite"],
        "couverture_pct": row["couverture_pct"], "docx_path": row["chemin_fichier"], "md_path": row["chemin_markdown"],
        "pdf_path": None, "pages": row["pages"], "lecture_partielle": row["lecture_partielle"], "modele_ia": row["modele_ia"],
        "email_envoye_at": row["envoye_le"], "erreur": row["erreur"], "erreur_publique": row["erreur_publique"],
        "rappel_demande": row["rappel_demande"], "rappel_effectue": row["rappel_effectue"],
        "entretien_realise": row["entretien_realise"], "created_at": row["created_at"], "completed_at": row["termine_le"],
        "traitement_depuis": row.get("traitement_depuis"),
    }


class Database:
    def __init__(self, sb: Supabase) -> None:
        self.sb = sb

    def init(self) -> None:  # le schéma est géré par les migrations (supabase/migrations)
        return None

    # ------------------------------------------------------------------ rapports
    def create_job(self, user_id: str, email: str, form: dict, jour: str, secteur: str, titre_entreprise: str,
                   documents: list[str], categories: dict[str, str], limite_heure: int) -> dict:
        """Crée le dossier et y rattache les documents en une seule transaction (fonction SQL creer_rapport) :
        quota horaire vérifié sous verrou, documents rattachés seulement s'ils sont libres. Lève QuotaDepasse ou
        DocumentsIndisponibles."""
        try:
            rows = self.sb.rpc("creer_rapport", {
                "p_user": user_id, "p_email": email, "p_jour": jour, "p_secteur": secteur, "p_formulaire": form,
                "p_titre_entreprise": titre_entreprise[:150], "p_documents": documents, "p_categories": categories,
                "p_limite_heure": limite_heure})
        except SupabaseError as e:
            if "QUOTA_DEPOTS" in str(e):
                raise QuotaDepasse() from e
            if "DOCUMENTS_INDISPONIBLES" in str(e):
                raise DocumentsIndisponibles() from e
            raise
        row = rows[0] if isinstance(rows, list) else rows
        return _job(row)

    def get_job(self, job_id: str) -> dict | None:
        row = self.sb.select_one("rapports", {"id": f"eq.{job_id}", "select": "*"})
        return _job(row) if row else None

    def update_job(self, job_id: str, **fields) -> None:
        values, donnees = {}, {}
        for k, v in fields.items():
            if k in DONNEES:
                donnees[k] = v
            elif k in COLONNES:
                values[COLONNES[k]] = v
                if k == "statut":
                    values["statut"] = STATUT_GLOBAL.get(v, "en_attente")
            else:
                raise ValueError(f"colonne inconnue : {k}")
        if values:
            self.sb.update("rapports", {"id": f"eq.{job_id}"}, values)
        if donnees:  # fusion atomique côté base (donnees = donnees || patch) : aucun écrivain ne peut effacer les clés d'un autre
            self.sb.rpc("fusionner_donnees", {"p_id": job_id, "p_patch": donnees})

    def list_jobs(self, jour: str | None = None, statut: str | None = None, owner: str | None = None, limit: int = 500) -> list[dict]:
        params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
        if jour:
            params["jour_evenement"] = f"eq.{jour}"
        if statut:
            params["etape"] = f"eq.{statut}"
        if owner:
            params["user_id"] = f"eq.{owner}"
        return [_job(r) for r in self.sb.select("rapports", params)]

    def count_recent(self, owner: str, hours: int = 1) -> int:
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        return len(self.sb.select("rapports", {"select": "id", "user_id": f"eq.{owner}", "created_at": f"gte.{since}"}))

    def old_jobs_batch(self, delta: timedelta, batch: int = 200) -> list[dict]:
        """Lot de dossiers plus anciens que `delta` (colonnes utiles à la purge seulement), du plus ancien au plus récent.
        Appelé en boucle par la purge, qui supprime chaque lot : aucune dépendance à la limite max_rows de PostgREST."""
        limite = (datetime.now(UTC) - delta).isoformat()
        return self.sb.select("rapports", {"select": "id,chemin_fichier,chemin_markdown", "created_at": f"lt.{limite}",
                                           "order": "created_at.asc", "limit": str(batch)})

    def delete_jobs(self, ids: list[str]) -> None:
        if ids:
            self.sb.delete("rapports", {"id": _ids(ids)})

    def is_stale(self, job: dict, bail_seconds: int = 360, attente_minutes: int = 10) -> bool:
        """Traitement interrompu : verrou expiré sans fin de traitement, ou dossier jamais lancé depuis `attente_minutes`."""
        if job["statut"] in TERMINAUX:
            return False
        now = datetime.now(UTC)
        depuis = job.get("traitement_depuis")
        if depuis:
            return datetime.fromisoformat(str(depuis).replace("Z", "+00:00")) < now - timedelta(seconds=bail_seconds)
        return datetime.fromisoformat(str(job["created_at"]).replace("Z", "+00:00")) < now - timedelta(minutes=attente_minutes)

    def stale_jobs(self, minutes: int) -> list[dict]:
        """Traitements interrompus (fonction arrêtée) : non terminés et sans activité depuis `minutes`."""
        limite = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
        rows = self.sb.select("rapports", {"select": "*", "etape": "not.in.(TERMINE,ENVOYE,ERREUR)", "created_at": f"lt.{limite}"})
        return [_job(r) for r in rows if not r.get("traitement_depuis") or r["traitement_depuis"] < limite]

    def log_event(self, job_id: str, statut: str, progression: int, event: dict) -> None:
        self.sb.rpc("journaliser", {"p_id": job_id, "p_etape": statut, "p_progression": progression, "p_evenement": event})

    def claim(self, job_id: str, bail_seconds: int = 330) -> bool:
        return bool(self.sb.rpc("prendre_traitement", {"p_id": job_id, "p_bail_secondes": bail_seconds}))

    def release(self, job_id: str) -> None:
        self.sb.update("rapports", {"id": f"eq.{job_id}"}, {"traitement_depuis": None})

    # ------------------------------------------------------------------ documents
    def documents_for(self, user_id: str, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        rows = self.sb.select("documents", {"select": "*", "user_id": f"eq.{user_id}", "id": f"in.({','.join(ids)})"})
        order = {i: n for n, i in enumerate(ids)}
        return sorted(rows, key=lambda r: order.get(r["id"], 99))

    def create_documents(self, rows: list[dict]) -> list[dict]:
        return self.sb.insert("documents", rows) if rows else []

    def job_documents(self, job_id: str) -> list[dict]:
        return self.sb.select("documents", {"select": "*", "rapport_id": f"eq.{job_id}", "order": "created_at.asc"})

    def old_documents_batch(self, delta: timedelta, batch: int = 500) -> list[dict]:
        """Lot de documents plus anciens que `delta`, du plus ancien au plus récent (la purge supprime chaque lot)."""
        limite = (datetime.now(UTC) - delta).isoformat()
        return self.sb.select("documents", {"select": "id,chemin", "created_at": f"lt.{limite}", "order": "created_at.asc",
                                            "limit": str(batch)})

    def delete_documents(self, ids: list[str]) -> None:
        for i in range(0, len(ids), 100):
            self.sb.delete("documents", {"id": _ids(ids[i:i + 100])})

    def owner_documents(self, owner: str) -> list[dict]:
        return self.sb.select("documents", {"select": "id,chemin", "user_id": f"eq.{owner}"})

    # ------------------------------------------------------------------ profils
    def update_profile(self, user_id: str, **values) -> None:
        self.sb.update("profiles", {"id": f"eq.{user_id}"}, values)

    # ------------------------------------------------------------------ entretiens
    def taken_slots(self, jour: str, capacity: int, exclude_owner: str | None = None) -> list[str]:
        rows = self.sb.rpc("creneaux_pris", {"p_jour": jour, "p_capacite": capacity, "p_exclure": exclude_owner})
        return sorted(r if isinstance(r, str) else next(iter(r.values())) for r in rows or [])

    def book_slot(self, job_id: str, owner: str, jour: str, heure: str, capacity: int) -> bool:
        return bool(self.sb.rpc("reserver_entretien", {"p_rapport": job_id, "p_user": owner, "p_jour": jour, "p_heure": heure,
                                                       "p_capacite": capacity}))

    def get_booking(self, job_id: str) -> dict | None:
        row = self.sb.select_one("entretiens", {"select": "jour,heure", "rapport_id": f"eq.{job_id}"})
        return {"jour": str(row["jour"]), "heure": row["heure"]} if row else None

    def bookings_for(self, job_ids: list[str]) -> dict[str, dict]:
        """Par lots de 100 identifiants : l'URL reste courte (≈ 4 Ko) quel que soit le nombre de dossiers."""
        out: dict[str, dict] = {}
        for i in range(0, len(job_ids), 100):
            rows = self.sb.select("entretiens", {"select": "rapport_id,jour,heure", "rapport_id": _ids(job_ids[i:i + 100])})
            out |= {r["rapport_id"]: {"jour": str(r["jour"]), "heure": r["heure"]} for r in rows}
        return out

    def latest_booking(self, owner: str) -> dict | None:
        row = self.sb.select_one("entretiens", {"select": "jour,heure", "user_id": f"eq.{owner}", "order": "created_at.desc"})
        return {"jour": str(row["jour"]), "heure": row["heure"]} if row else None

    # ------------------------------------------------------------------ effacement
    def delete_owner(self, owner: str) -> list[dict]:
        """Droit à l'effacement : supprime les rapports (documents et entretiens en cascade) ; retourne les rapports supprimés."""
        return [_job(r) for r in self.sb.delete("rapports", {"user_id": f"eq.{owner}"})]

    # ------------------------------------------------------------------ audit
    def audit(self, job_id: str | None, acteur: str, action: str, ip: str | None = None) -> None:
        self.sb.insert("audit_logs", {"rapport_id": job_id, "acteur": acteur[:120], "action": action[:120], "ip": ip})

    def purge_audit(self, days: int) -> None:
        limite = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        self.sb.delete("audit_logs", {"horodatage": f"lt.{limite}"})

    # ------------------------------------------------------------------ quotas partagés entre instances (fonction SQL)
    def consume(self, key: str, weight: int, maximum: int, window_s: int) -> bool:
        """Consomme `weight` sur le quota `key` (fenêtre glissante) ; False si le maximum serait dépassé. Atomique en base."""
        return bool(self.sb.rpc("consommer_quota", {"p_cle": key[:200], "p_poids": weight, "p_max": maximum, "p_fenetre_s": window_s}))

    def purge_quotas(self) -> None:
        self.sb.rpc("purger_quotas", {})

    def audit_for(self, job_id: str) -> list[dict]:
        return self.sb.select("audit_logs", {"select": "acteur,action,horodatage,ip", "rapport_id": f"eq.{job_id}", "order": "id.asc"})

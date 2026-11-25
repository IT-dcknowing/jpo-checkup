"""Stockage SQLite (WAL) : dépôts, résultats, codes de connexion, entretiens, journal d'audit."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

TERMINAUX = ("ENVOYE", "TERMINE", "ERREUR")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, ref TEXT NOT NULL, owner TEXT, statut TEXT NOT NULL, progression INTEGER DEFAULT 0,
    jour_evenement TEXT, secteur TEXT,
    form_enc TEXT NOT NULL,
    fichiers_json TEXT DEFAULT '[]',
    resultat_ia_json TEXT,
    score_performance INTEGER, grade_performance TEXT,
    score_conformite INTEGER, grade_conformite TEXT, couverture_pct INTEGER,
    alertes_json TEXT, plan_action_json TEXT, resume_json TEXT, journal_json TEXT DEFAULT '[]',
    docx_path TEXT, pdf_path TEXT, md_path TEXT, pages INTEGER,
    lecture_partielle INTEGER DEFAULT 0, modele_ia TEXT,
    email_envoye_at TEXT, erreur TEXT, erreur_publique TEXT,
    rappel_demande INTEGER DEFAULT 0, rappel_effectue INTEGER DEFAULT 0, entretien_realise INTEGER DEFAULT 0,
    created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, acteur TEXT, action TEXT, horodatage TEXT, ip TEXT
);
CREATE TABLE IF NOT EXISTS compteurs (annee TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS auth_codes (
    owner TEXT PRIMARY KEY, code_hash TEXT NOT NULL, expires_at REAL NOT NULL, tries INTEGER NOT NULL DEFAULT 0, sent_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS entretiens (
    job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, jour TEXT NOT NULL, heure TEXT NOT NULL, created_at TEXT NOT NULL
);
"""
# Créés après la migration des colonnes (owner peut manquer sur une base plus ancienne)
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_jobs_statut ON jobs(statut);
CREATE INDEX IF NOT EXISTS idx_jobs_jour ON jobs(jour_evenement);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner);
CREATE INDEX IF NOT EXISTS idx_ent_jour ON entretiens(jour, heure);
"""
MIGRATIONS = {  # colonnes ajoutées depuis la première version du schéma
    "owner": "TEXT", "resume_json": "TEXT", "journal_json": "TEXT DEFAULT '[]'", "rappel_demande": "INTEGER DEFAULT 0",
    "rappel_effectue": "INTEGER DEFAULT 0", "entretien_realise": "INTEGER DEFAULT 0",
}
COLUMNS = {
    "statut", "progression", "fichiers_json", "resultat_ia_json", "score_performance", "grade_performance",
    "score_conformite", "grade_conformite", "couverture_pct", "alertes_json", "plan_action_json", "resume_json",
    "docx_path", "pdf_path", "md_path", "pages", "lecture_partielle", "modele_ia", "email_envoye_at",
    "erreur", "erreur_publique", "rappel_demande", "rappel_effectue", "entretien_realise", "completed_at",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
            for col, typ in MIGRATIONS.items():
                if col not in have:
                    c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
            c.executescript(INDEXES)

    # ------------------------------------------------------------------ jobs
    def create_job(self, job_id: str, form_enc: str, jour: str, secteur: str, owner: str) -> str:
        """Crée le dépôt et retourne sa référence (JPO-2026-0001, séquentielle par année)."""
        annee = jour[:4]
        with self.conn() as c:
            row = c.execute(
                "INSERT INTO compteurs(annee, n) VALUES(?, 1) ON CONFLICT(annee) DO UPDATE SET n = n + 1 RETURNING n", (annee,)
            ).fetchone()
            ref = f"JPO-{annee}-{row['n']:04d}"
            c.execute(
                "INSERT INTO jobs(id, ref, owner, statut, progression, jour_evenement, secteur, form_enc, created_at) "
                "VALUES(?, ?, ?, 'EN_ATTENTE', 5, ?, ?, ?, ?)",
                (job_id, ref, owner, jour, secteur, form_enc, now_iso()),
            )
        return ref

    def get_job(self, job_id: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def update_job(self, job_id: str, **fields) -> None:
        bad = set(fields) - COLUMNS
        if bad:
            raise ValueError(f"colonnes inconnues : {bad}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))

    def list_jobs(self, jour: str | None = None, statut: str | None = None, owner: str | None = None, limit: int = 500) -> list[dict]:
        where, args = [], []
        for col, val in (("jour_evenement", jour), ("statut", statut), ("owner", owner)):
            if val:
                where.append(f"{col} = ?"); args.append(val)
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC LIMIT ?"
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, (*args, limit)).fetchall()]

    def unfinished_jobs(self) -> list[str]:
        marks = ",".join("?" * len(TERMINAUX))
        with self.conn() as c:
            return [r["id"] for r in c.execute(f"SELECT id FROM jobs WHERE statut NOT IN ({marks}) ORDER BY created_at", TERMINAUX)]

    def jobs_older_than(self, delta: timedelta) -> list[dict]:
        limite = (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds")
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM jobs WHERE created_at < ?", (limite,)).fetchall()]

    def log_event(self, job_id: str, statut: str, progression: int, event: dict) -> None:
        """Change d'étape ET ajoute l'événement au journal en une seule transaction : le flux SSE (qui relit le journal) et
        GET /diagnostics/{id} (que le front appelle dès l'événement final) voient toujours un état cohérent."""
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT journal_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return
            journal = json.loads(row["journal_json"] or "[]")
            journal.append(event)
            c.execute("UPDATE jobs SET statut = ?, progression = ?, journal_json = ? WHERE id = ?",
                      (statut, progression, json.dumps(journal, ensure_ascii=False), job_id))

    def delete_owner(self, owner: str) -> list[str]:
        """Droit à l'effacement : supprime dépôts, entretiens et code d'un visiteur ; retourne les identifiants supprimés."""
        with self.conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM jobs WHERE owner = ?", (owner,))]
            c.execute("DELETE FROM jobs WHERE owner = ?", (owner,))
            c.execute("DELETE FROM entretiens WHERE owner = ?", (owner,))
            c.execute("DELETE FROM auth_codes WHERE owner = ?", (owner,))
        return ids

    # ------------------------------------------------------------------ codes de connexion
    def save_code(self, owner: str, code_hash: str, ttl: int) -> None:
        now = time.time()
        with self.conn() as c:
            c.execute("INSERT INTO auth_codes(owner, code_hash, expires_at, tries, sent_at) VALUES(?, ?, ?, 0, ?) "
                      "ON CONFLICT(owner) DO UPDATE SET code_hash = excluded.code_hash, expires_at = excluded.expires_at, "
                      "tries = 0, sent_at = excluded.sent_at", (owner, code_hash, now + ttl, now))

    def get_code(self, owner: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM auth_codes WHERE owner = ?", (owner,)).fetchone()
        return dict(row) if row else None

    def bump_tries(self, owner: str) -> int:
        with self.conn() as c:
            row = c.execute("UPDATE auth_codes SET tries = tries + 1 WHERE owner = ? RETURNING tries", (owner,)).fetchone()
        return row["tries"] if row else 0

    def delete_code(self, owner: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM auth_codes WHERE owner = ?", (owner,))

    # ------------------------------------------------------------------ entretiens
    def taken_slots(self, jour: str, capacity: int, exclude_owner: str | None = None) -> list[str]:
        with self.conn() as c:
            rows = c.execute("SELECT heure, COUNT(*) AS n FROM entretiens WHERE jour = ? AND (? IS NULL OR owner != ?) GROUP BY heure",
                             (jour, exclude_owner, exclude_owner)).fetchall()
        return sorted(r["heure"] for r in rows if r["n"] >= capacity)

    def book_slot(self, job_id: str, owner: str, jour: str, heure: str, capacity: int) -> bool:
        """Réserve (ou déplace) l'entretien d'un dépôt ; False si le créneau est complet."""
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            n = c.execute("SELECT COUNT(*) AS n FROM entretiens WHERE jour = ? AND heure = ? AND job_id != ?", (jour, heure, job_id)).fetchone()["n"]
            if n >= capacity:
                return False
            c.execute("INSERT INTO entretiens(job_id, owner, jour, heure, created_at) VALUES(?, ?, ?, ?, ?) "
                      "ON CONFLICT(job_id) DO UPDATE SET jour = excluded.jour, heure = excluded.heure", (job_id, owner, jour, heure, now_iso()))
        return True

    def get_booking(self, job_id: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT jour, heure FROM entretiens WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def latest_booking(self, owner: str) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT jour, heure FROM entretiens WHERE owner = ? ORDER BY created_at DESC LIMIT 1", (owner,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ audit
    def audit(self, job_id: str | None, acteur: str, action: str, ip: str | None = None) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO audit_logs(job_id, acteur, action, horodatage, ip) VALUES(?, ?, ?, ?, ?)",
                      (job_id, acteur, action, now_iso(), ip))

    def audit_for(self, job_id: str) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT acteur, action, horodatage, ip FROM audit_logs WHERE job_id = ? ORDER BY id", (job_id,))]


def loads(text: str | None, default=None):
    return json.loads(text) if text else default

"""Configuration : tout vient de l'environnement (fichier backend/.env)."""
from __future__ import annotations

import os
import secrets
import shutil
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
WORD_DIR = PROJECT_DIR / "word"
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

# Ordre de repli : si le premier modèle échoue, on passe au suivant.
# Modèles gratuits (« :free ») : développement uniquement. OpenRouter n'y accepte que la collecte des données
# (« data_collection=allow ») ; ils sont donc refusés en production (voir Settings.problemes_ia).
DEFAULT_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "baidu/ernie-5.5-flash-preview:free",
    "particle-ai/particle-1:free",
]
DEFAULT_EVENT_DAYS = ["2026-12-16", "2026-12-17", "2026-12-18"]

_SOFFICE_WINDOWS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def _csv(name: str, default: list[str]) -> list[str]:
    items = [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]
    return items or list(default)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _persisted(path: Path, factory) -> str:
    """Secret généré une fois puis conservé sur disque (survit aux redémarrages)."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = factory()
    path.write_text(value, encoding="utf-8")
    return value


def _find_front(explicit: str) -> Path | None:
    """Dossier du front statique (index.html) : variable FRONT_DIR, sinon emplacements habituels du projet."""
    candidates = [Path(explicit)] if explicit else []
    candidates += [PROJECT_DIR / "checkup360-front" / "checkup360-front", PROJECT_DIR / "checkup360-front", PROJECT_DIR / "front"]
    return next((p.resolve() for p in candidates if (p / "index.html").exists()), None)


def _find_soffice() -> str | None:
    explicit = os.getenv("SOFFICE_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    return next((p for p in _SOFFICE_WINDOWS if Path(p).exists()), None)


class Settings:
    def __init__(self) -> None:
        e = os.getenv
        # Sur Vercel, seul /tmp est inscriptible (fichiers temporaires uniquement : tout le reste est dans Supabase)
        default_data = Path("/tmp/jpo-data") if e("VERCEL") else BACKEND_DIR / "data"
        self.data_dir = Path(e("DATA_DIR") or default_data).resolve()

        # Supabase : base, stockage, comptes. La clé service_role ne sert que côté serveur.
        self.supabase_url = e("SUPABASE_URL", "").strip().rstrip("/")
        self.supabase_anon_key = (e("SUPABASE_PUBLISHABLE_KEY") or e("SUPABASE_ANON_KEY") or "").strip()
        self.supabase_service_key = (e("SUPABASE_SERVICE_ROLE_KEY") or e("SUPABASE_SECRET_KEY") or "").strip()
        # Rendu Word par la fonction Node (Vercel) ; vide = Node local (node word/md_vers_word.js)
        self.docx_render_url = e("DOCX_RENDER_URL", "").strip()
        if not self.docx_render_url and e("VERCEL"):
            # Production : domaine du projet ; préversions : URL du déploiement (contournement de protection si fourni)
            host = e("VERCEL_PROJECT_PRODUCTION_URL") if e("VERCEL_ENV") == "production" else e("VERCEL_URL")
            if host:
                self.docx_render_url = f"https://{host}/api/docx"
        self.vercel_bypass = e("VERCEL_AUTOMATION_BYPASS_SECRET", "").strip()
        self.cron_secret = e("CRON_SECRET", "").strip()
        self.uploads_dir = self.data_dir / "uploads"
        self.reports_dir = self.data_dir / "reports"
        self.db_path = self.data_dir / "diagnostics.sqlite3"
        self.project_dir = PROJECT_DIR
        self.word_dir = WORD_DIR

        # IA (OpenRouter)
        self.openrouter_api_key = e("OPENROUTER_API_KEY", "").strip()
        self.openrouter_url = e("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
        self.llm_models = _csv("OPENROUTER_MODELS", DEFAULT_MODELS)
        self.llm_timeout = _float("LLM_TIMEOUT", 75)
        # Budget total de l'étape IA (s) : la fonction Vercel s'arrête à 300 s, on garde de la marge pour le rendu.
        # 0 = illimité (déconseillé : la fonction serait coupée sans message clair).
        self.llm_budget = _float("LLM_BUDGET", 170)
        self.llm_backoff = [float(x) for x in _csv("LLM_BACKOFF", ["2", "6", "18"])]
        self.llm_max_tokens = _int("LLM_MAX_TOKENS", 8000)
        # « deny » : OpenRouter n'utilise que des fournisseurs qui ne conservent pas les données (notice ARTCI).
        self.llm_data_collection = e("OPENROUTER_DATA_COLLECTION", "deny").strip().lower()
        self.context_max_chars = _int("CONTEXT_MAX_CHARS", 60_000)

        # Dépôts
        self.max_upload_bytes = _int("MAX_UPLOAD_MB", 20) * 1024 * 1024
        self.max_files = _int("MAX_FILES", 10)
        self.max_total_bytes = _int("MAX_TOTAL_MB", 50) * 1024 * 1024        # par dépôt (tous fichiers)
        self.max_user_bytes_day = _int("MAX_USER_MB_JOUR", 200) * 1024 * 1024  # par visiteur et par 24 h
        self.max_files_hour = _int("MAX_FICHIERS_HEURE", 30)                   # autorisations d'envoi par visiteur et par heure
        self.rate_limit = _int("RATE_LIMIT_PER_HOUR", 5)
        self.event_days = _csv("EVENT_DAYS", DEFAULT_EVENT_DAYS)
        self.workers = _int("WORKERS", 4)

        # Environnement Vercel : production | preview | development (vide en local)
        self.vercel_env = e("VERCEL_ENV", "").strip().lower()
        self.production = self.vercel_env == "production"
        # Préversions : pas d'e-mail réel tant que EMAILS_PREVIEW n'est pas à 1 (les préversions doivent viser un Supabase de test)
        self.emails_actifs = self.vercel_env != "preview" or e("EMAILS_PREVIEW", "0") == "1"
        self.app_env = e("APP_ENV", "production").lower()
        self.front_dir = _find_front(e("FRONT_DIR", "").strip())

        # Connexion visiteur par code e-mail (6 chiffres, 10 min, 3 essais, renvoi après 30 s)
        self.code_ttl, self.code_resend, self.code_max_tries = 600, 30, 3
        self.visitor_token_ttl = _int("VISITOR_TOKEN_HOURS", 12) * 3600

        # Entretiens individuels : créneaux du front (K.SLOTS) ; le jeudi commence à 10h30
        self.slots = _csv("ENTRETIEN_SLOTS", ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "14:00", "14:30",
                                              "15:00", "15:30", "16:00", "16:30"])
        self.slot_min_by_day = {"2026-12-17": "10:30"}
        self.slot_capacity = _int("ENTRETIEN_CAPACITE", 1)
        self.slot_minutes = 30
        self.event_hours = {"2026-12-16": ("08:30", "17:30"), "2026-12-17": ("10:30", "17:30"), "2026-12-18": ("08:30", "17:00")}
        self.event_location = e("EVENT_LOCATION", "Siège du Cabinet DC KNOWING, Cocody Riviera 2, Collège André Malraux, Abidjan")

        # Sécurité
        # SECRET_KEY : signe les jetons conseillers et protège le service de rendu Word (obligatoire en production :
        # pas de secret généré en repli, il serait différent d'une instance Vercel à l'autre)
        self.secret_key = e("SECRET_KEY", "").strip()
        if not self.secret_key and not self.production:
            self.secret_key = _persisted(self.data_dir / ".secret_key", lambda: secrets.token_urlsafe(48))
        # Comptes conseillers : ADMIN_ACCOUNTS="a@x.ci=<empreinte>,totp=<clé base32>,role=lecture;b@x.ci=<empreinte>"
        # <empreinte> : python -m app.security hash (scrypt). Les mots de passe en clair ne sont acceptés qu'hors production.
        # Rôles : « conseiller » (défaut, toutes les actions) ou « lecture » (consultation seule, ni export ni envoi).
        self.admin_accounts: dict[str, dict] = {}
        if e("ADMIN_PASSWORD"):
            self.admin_accounts[e("ADMIN_USER", "conseiller").strip().lower()] = {"secret": e("ADMIN_PASSWORD"), "totp": "", "role": "conseiller"}
        for pair in e("ADMIN_ACCOUNTS", "").split(";"):
            user, sep, rest = pair.partition("=")
            if not (sep and user.strip() and rest):
                continue
            secret, *opts = rest.split(",")
            o = dict(x.split("=", 1) for x in opts if "=" in x)
            role = o.get("role", "conseiller").strip().lower()
            self.admin_accounts[user.strip().lower()] = {"secret": secret.strip(), "totp": o.get("totp", "").strip().replace(" ", "").upper(),
                                                         "role": role if role in ("conseiller", "lecture") else "lecture"}
        self.admin_token_ttl = _int("ADMIN_TOKEN_HOURS", 4) * 3600
        # Changer ADMIN_TOKEN_VERSION révoque d'un coup tous les jetons conseillers en cours
        self.admin_token_version = e("ADMIN_TOKEN_VERSION", "1").strip()
        # Secret partagé API -> Edge Function envoyer-rapport (identifie le serveur sans tester la clé service_role)
        self.internal_secret = e("EDGE_INTERNAL_SECRET", "").strip()
        self.sentry_dsn = e("SENTRY_DSN", "").strip()
        self.audit_retention_days = _int("RETENTION_AUDIT_DAYS", 365)
        self.download_ttl = _int("DOWNLOAD_TTL_DAYS", 7) * 86400
        self.cors_origins = _csv("CORS_ORIGINS", ["http://localhost:5173", "http://localhost:3000"])
        self.on_vercel = bool(e("VERCEL"))
        self.trust_proxy = e("TRUST_PROXY", "1" if self.on_vercel else "0") == "1"  # Vercel : x-forwarded-for fiable
        self.public_base_url = e("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

        # Rétention
        self.retention_raw_hours = _int("RETENTION_RAW_HOURS", 24)
        self.retention_report_days = _int("RETENTION_REPORT_DAYS", 30)

        # E-mails : envoyés par Supabase (Auth + Edge Function envoyer-rapport, via Resend) ; aucun secret d'envoi ici

        # Rendu Word / PDF
        self.node_path = e("NODE_PATH_BIN", "").strip() or shutil.which("node")
        self.soffice_path = _find_soffice()
        self.render_timeout = _int("RENDER_TIMEOUT", 180)
        # Un profil LibreOffice par worker, réutilisé : 1re conversion ~45 s, les suivantes ~7 s.
        self.lo_profile_dir = Path(e("LO_PROFILE_DIR") or self.data_dir / "lo_profiles").resolve()
        self.lo_warmup = e("LO_WARMUP", "1") == "1"

    def problemes_ia(self) -> list[str]:
        """Réglages IA incompatibles avec la notice (« sans collecte ») : bloquants en production."""
        out = []
        if self.llm_data_collection != "deny":
            out.append("OPENROUTER_DATA_COLLECTION doit valoir « deny »")
        gratuits = [m for m in self.llm_models if m.endswith(":free")]
        if gratuits:
            out.append("modèles gratuits (collecte imposée) : " + ", ".join(gratuits))
        return out

    def problemes_config(self) -> list[str]:
        """Secrets et réglages indispensables en production (l'API refuse de démarrer sans eux)."""
        out = []
        if len(self.secret_key) < 48:
            out.append("SECRET_KEY absente ou trop courte (48 caractères minimum)")
        if len(self.cron_secret) < 16:
            out.append("CRON_SECRET absent ou trop court (16 caractères minimum) : la purge quotidienne serait refusée")
        if not (self.supabase_url and self.supabase_service_key):
            out.append("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY absentes")
        if not self.openrouter_api_key:
            out.append("OPENROUTER_API_KEY absente")
        return out

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.uploads_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

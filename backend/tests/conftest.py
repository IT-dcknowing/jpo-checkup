import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402

EXEMPLE = json.loads((PROJECT / "exemple_entree.json").read_text(encoding="utf-8"))

# Champs tels que le front les envoie (checkup360-front/README.md)
FORM = {
    "raison_sociale": "Société Kouassi & Fils SARL", "ncc": "1234567A", "secteur": "Commerce et distribution", "effectif": "15",
    "contact_nom": "Awa Kouassi", "contact_email": "ignoré@example.ci", "contact_telephone": "0767131993",
    "type_client": "PROSPECT", "jour_evenement": "2026-12-16", "regime_fiscal": "REEL_SIMPLIFIE", "fne": "NON_ACTIVE",
    "statut_dgi": "A_JOUR", "statut_cnps": "A_JOUR", "statut_its": "A_JOUR", "statut_cmu": "EN_RETARD", "statut_disa": "A_JOUR",
    "tva_exoneree": "false", "consentement_artci": "true",
}


class FakeLLM:
    """Remplace OpenRouter : renvoie la sortie IA de l'exemple de référence et mémorise les prompts."""

    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else EXEMPLE["ia"]
        self.error = error
        self.prompts = []

    def complete_validated(self, system, user, validate):
        self.prompts.append(user)
        if self.error:
            raise self.error
        return validate(self.payload), "fake/model:free"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for k in ("OPENROUTER_API_KEY", "ADMIN_PASSWORD", "ADMIN_ACCOUNTS", "SECRET_KEY", "FRONT_DIR", "DOCX_RENDER_URL", "VERCEL",
              "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "CRON_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_USER", "Conseiller@dc-knowing.ci")
    monkeypatch.setenv("ADMIN_PASSWORD", "mot-de-passe-test")
    monkeypatch.setenv("CRON_SECRET", "secret-cron-test")
    s = Settings()
    s.ensure_dirs()
    s.real_soffice = s.soffice_path   # utilisé par le test PDF uniquement
    s.soffice_path = None             # les autres tests évitent les conversions LibreOffice (lentes)
    return s

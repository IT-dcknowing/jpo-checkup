"""Point d'entrée Vercel (Python) : toute l'API /api/* est servie par l'application FastAPI de backend/app."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.main import create_app  # noqa: E402

app = create_app()

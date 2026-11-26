"""Point d'entrée Vercel (Python) : toute l'API /api/* est servie par l'application FastAPI de backend/app."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import json  # noqa: E402
import logging  # noqa: E402


class _Json(logging.Formatter):
    """Journaux en JSON (une ligne par événement) : filtrables par niveau, module et job dans les logs Vercel."""

    def format(self, r: logging.LogRecord) -> str:
        d = {"t": self.formatTime(r), "niveau": r.levelname, "module": r.name, "message": r.getMessage()}
        if r.exc_info:
            d["exception"] = self.formatException(r.exc_info)
        return json.dumps(d, ensure_ascii=False)


_h = logging.StreamHandler()
_h.setFormatter(_Json())
logging.basicConfig(level=logging.INFO, handlers=[_h], force=True)

from app.main import create_app  # noqa: E402

app = create_app()

"""Jetons signés (espace conseiller) et limitation de débit."""
from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque



def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _mac(secret: str, payload: bytes) -> bytes:
    return hmac.new(secret.encode(), payload, hashlib.sha256).digest()


def sign_token(secret: str, kind: str, subject: str, ttl: int) -> str:
    """Jeton HMAC `kind|sujet|expiration`. `sujet` ne doit pas contenir « | »."""
    payload = f"{kind}|{subject}|{int(time.time()) + ttl}".encode()
    return f"{_b64(payload)}.{_b64(_mac(secret, payload))}"


def verify_token(secret: str, token: str, kind: str) -> str | None:
    """Retourne le sujet si le jeton est valide, du bon type et non expiré."""
    try:
        p, s = token.split(".", 1)
        payload = _unb64(p)
        if not hmac.compare_digest(_mac(secret, payload), _unb64(s)):
            return None
        k, rest = payload.decode().split("|", 1)
        subject, exp = rest.rsplit("|", 1)
        if k != kind or int(exp) < time.time():
            return None
        return subject
    except Exception:
        return None


class RateLimiter:
    """N requêtes par fenêtre glissante et par clé (adresse IP)."""

    def __init__(self, limit: int, window: float = 3600.0) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

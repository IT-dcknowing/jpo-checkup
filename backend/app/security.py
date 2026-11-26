"""Jetons signés (espace conseiller), mots de passe hachés, codes à usage unique (TOTP) et limitation de débit.

Générer l'empreinte d'un mot de passe conseiller (à mettre dans ADMIN_ACCOUNTS) :
    python -m app.security hash
Générer une clé TOTP (application d'authentification : Google Authenticator, Microsoft Authenticator...) :
    python -m app.security totp prenom.nom@dc-knowing.com
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from collections import defaultdict, deque
from urllib.parse import quote

# scrypt (bibliothèque standard) : n=2^14, r=8, p=1 ≈ 16 Mo et ~50 ms par vérification
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
HASH_PREFIX = "scrypt$"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _mac(secret: str, payload: bytes) -> bytes:
    return hmac.new(secret.encode(), payload, hashlib.sha256).digest()


# ------------------------------------------------------------------ jetons
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


def account_fingerprint(secret: str, account_secret: str, version: str) -> str:
    """Empreinte courte du mot de passe (haché) d'un compte + version globale : la changer révoque les jetons émis."""
    return _b64(_mac(secret, f"{version}:{account_secret}".encode()))[:16]


# ------------------------------------------------------------------ mots de passe
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"{HASH_PREFIX}{_b64(salt)}${_b64(dk)}"


def is_hashed(stored: str) -> bool:
    return stored.startswith(HASH_PREFIX)


def verify_password(password: str, stored: str, allow_plain: bool = False) -> bool:
    """Compare en temps constant. Un mot de passe stocké en clair n'est accepté que si `allow_plain` (hors production)."""
    if is_hashed(stored):
        try:
            salt_b64, dk_b64 = stored[len(HASH_PREFIX):].split("$", 1)
            dk = hashlib.scrypt(password.encode(), salt=_unb64(salt_b64), dklen=32, **_SCRYPT)
            return hmac.compare_digest(dk, _unb64(dk_b64))
        except (ValueError, TypeError):
            return False
    if not allow_plain or not stored:
        return False
    return hmac.compare_digest(password.encode(), stored.encode())


def dummy_password_check(password: str) -> None:
    """Même coût de calcul quand le compte n'existe pas (pas d'énumération des comptes par le temps de réponse)."""
    hashlib.scrypt(password.encode(), salt=b"0" * 16, dklen=32, **_SCRYPT)


# ------------------------------------------------------------------ TOTP (RFC 6238, 6 chiffres, 30 s)
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret_b32: str, t: float | None = None, step: int = 30) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    counter = int((time.time() if t is None else t) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def verify_totp(secret_b32: str, code: str, window: int = 1) -> bool:
    code = "".join(c for c in str(code or "") if c.isdigit())
    if len(code) != 6:
        return False
    now = time.time()
    try:
        return any(hmac.compare_digest(totp_code(secret_b32, now + i * 30), code) for i in range(-window, window + 1))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------ limitation de débit (repli en mémoire)
class RateLimiter:
    """N requêtes par fenêtre glissante et par clé. En mémoire : repli local quand la base est indisponible
    (la limitation de référence est en base, partagée entre les instances : fonction SQL limiter_tentative)."""

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
            if len(self._hits) > 10_000:  # borne mémoire
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True


def _main(argv: list[str]) -> int:
    import getpass
    if argv[:1] == ["hash"]:
        pwd = getpass.getpass("Mot de passe du conseiller : ")
        if len(pwd) < 12:
            print("12 caractères minimum.")
            return 1
        if pwd != getpass.getpass("Confirmez : "):
            print("Les deux saisies diffèrent.")
            return 1
        print(hash_password(pwd))
        return 0
    if argv[:1] == ["totp"] and len(argv) == 2:
        s = new_totp_secret()
        print(f"Clé TOTP : {s}")
        print(f"Lien à scanner (QR code) : otpauth://totp/DC-KNOWING%20Check-up%20360:{quote(argv[1])}?secret={s}&issuer=DC-KNOWING")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))

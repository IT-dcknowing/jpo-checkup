"""Client OpenRouter avec repli automatique entre modèles gratuits."""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import ValidationError

from .config import Settings

log = logging.getLogger("jpo.llm")
T = TypeVar("T")


class LLMError(Exception):
    """Aucun modèle n'a fourni de réponse exploitable."""


class LLMAuthError(LLMError):
    """Clé absente ou refusée : inutile d'essayer les autres modèles."""


class _Transient(Exception):
    pass


class LLMClient(Protocol):
    def complete_validated(self, system: str, user: str, validate: Callable[[dict], T]) -> tuple[T, str]: ...


def parse_json_text(text: str) -> dict:
    """Extrait l'objet JSON d'une réponse (balises ```json, texte autour, etc.)."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        out = json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("aucun objet JSON dans la réponse") from None
        out = json.loads(t[i : j + 1])
    if not isinstance(out, dict):
        raise ValueError("la réponse n'est pas un objet JSON")
    return out


class OpenRouterClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.s = settings
        self.http = http or httpx.Client(timeout=settings.llm_timeout)
        self.sleep = sleep
        self._no_optional: set[str] = set()  # modèles qui refusent response_format / reasoning
        self._deadline: float | None = None  # fin de la part de temps du modèle en cours

    # ------------------------------------------------------------------ un appel HTTP
    def _payload(self, model: str, messages: list[dict]) -> dict:
        p: dict[str, Any] = {
            "model": model, "messages": messages, "temperature": 0, "max_tokens": self.s.llm_max_tokens,
            "provider": {"data_collection": self.s.llm_data_collection, "allow_fallbacks": True},
        }
        if model not in self._no_optional:
            p["response_format"] = {"type": "json_object"}
            p["reasoning"] = {"effort": "low", "exclude": True}
        return p

    def _call(self, model: str, messages: list[dict]) -> str:
        headers = {"Authorization": f"Bearer {self.s.openrouter_api_key}", "Content-Type": "application/json",
                   "X-Title": "DC-KNOWING JPO Diagnostic"}
        tries = len(self.s.llm_backoff) + 1
        for i in range(tries):
            try:
                timeout = self.s.llm_timeout
                if self._deadline:
                    reste = self._deadline - time.monotonic()
                    if reste < 5:
                        raise _Transient("part de temps du modèle épuisée")
                    timeout = min(timeout, reste)
                r = self.http.post(self.s.openrouter_url, headers=headers, json=self._payload(model, messages), timeout=timeout)
            except httpx.HTTPError as e:
                err = f"réseau ({type(e).__name__})"
            else:
                code = r.status_code
                if code == 200:
                    text = self._content(r)
                    if text:
                        return text
                    # réponse vide (souvent : tout le quota consommé en raisonnement) : inutile d'insister sur ce modèle
                    raise _Transient(f"réponse vide ({self._finish(r)})")
                elif code in (401, 403):
                    raise LLMAuthError(f"clé OpenRouter refusée (HTTP {code})")
                elif code in (400, 404, 422) and model not in self._no_optional:
                    self._no_optional.add(model)  # on réessaie sans response_format ni reasoning
                    log.info("%s : paramètres optionnels refusés, nouvel essai sans eux", model)
                    continue
                elif code == 402:
                    raise _Transient("crédits insuffisants (HTTP 402)")
                elif code in (408, 409, 425, 429) or code >= 500:
                    err = f"HTTP {code}"
                else:
                    raise _Transient(f"HTTP {code} {r.text[:200]}")
            if i < tries - 1:
                self.sleep(self.s.llm_backoff[i])
            else:
                raise _Transient(err)
        raise _Transient("échec")

    @staticmethod
    def _finish(r: httpx.Response) -> str:
        try:
            return str(r.json()["choices"][0].get("finish_reason") or "?")
        except (ValueError, KeyError, IndexError, TypeError):
            return "?"

    @staticmethod
    def _content(r: httpx.Response) -> str:
        try:
            msg = r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            return ""
        content = msg.get("content")
        if isinstance(content, list):  # certains fournisseurs renvoient des blocs
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        return (content or "").strip()

    # ------------------------------------------------------------------ chaîne de repli
    def complete_validated(self, system: str, user: str, validate: Callable[[dict], T]) -> tuple[T, str]:
        if not self.s.openrouter_api_key:
            raise LLMAuthError("OPENROUTER_API_KEY n'est pas renseignée (fichier backend/.env)")
        if self.s.production and (pb := self.s.problemes_ia()):
            # notice ARTCI : « prestataire IA configuré sans collecte » ; aucun envoi tant que ce n'est pas vrai
            raise LLMAuthError("configuration IA refusée en production : " + " ; ".join(pb))
        errors: list[str] = []
        debut = time.monotonic()
        for model in self.s.llm_models:
            if self.s.llm_budget:  # chaque modèle restant reçoit une part égale du budget restant
                reste = self.s.llm_budget - (time.monotonic() - debut)
                restants = len(self.s.llm_models) - self.s.llm_models.index(model)
                if reste < 10:
                    errors.append("budget de temps de l'analyse dépassé")
                    break
                self._deadline = time.monotonic() + reste / restants
            else:
                self._deadline = None
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            for _essai in range(2):  # essai initial + une relance avec le message d'erreur (spec §4.3)
                try:
                    text = self._call(model, messages)
                except _Transient as e:
                    errors.append(f"{model}: {e}")
                    break
                try:
                    return validate(parse_json_text(text)), model
                except (ValueError, ValidationError) as e:
                    detail = str(e)[:600]
                    errors.append(f"{model}: JSON invalide ({detail[:120]})")
                    messages = messages + [
                        {"role": "assistant", "content": text[:4000]},
                        {"role": "user", "content": f"Ta réponse est invalide : {detail}\nRéponds de nouveau uniquement par le JSON corrigé."},
                    ]
            log.warning("modèle %s abandonné, repli sur le suivant", model)
        raise LLMError("Tous les modèles ont échoué : " + " | ".join(errors))

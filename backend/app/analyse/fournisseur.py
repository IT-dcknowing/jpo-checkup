"""Accès au modèle : Anthropic (sortie structurée), OpenRouter (repli), simulé (tests) ; coût réel et plafond.

Le fournisseur renvoie un objet JSON conforme au schéma demandé et le `Cout` de l'appel. La validation métier
(ids, colonnes, rôles…) reste dans carte.py : ici on vérifie seulement la forme (JSON, schéma).
Le budget est tenu par l'appelant via `appeler(fournisseur, budget, …)`, qui refuse l'appel si le plafond est
atteint et compte aussi le coût des appels échoués (refus, réponse tronquée, JSON invalide).
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Callable, Protocol, runtime_checkable

from .modele import Cout

log = logging.getLogger("jpo.analyse.fournisseur")

MODELE_DEFAUT = "claude-sonnet-5-5"
EFFORT_DEFAUT = "medium"
BUDGET_DEFAUT_USD = 3.0
BETA_REPLI = "server-side-fallback-2026-07-01"
MODELES_AVEC_REPLI = {"claude-sonnet-5-5", "claude-opus-5-5"}

# USD par million de tokens : entrée, sortie, lecture de cache. Écriture de cache = 1,25 × entrée.
TARIFS: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"entree": 4.0, "sortie": 20.0, "cache_lu": 0.20},
    "claude-sonnet-5-5": {"entree": 2.0, "sortie": 10.0, "cache_lu": 0.10},
    "claude-haiku-5-5": {"entree": 0.10, "sortie": 0.50, "cache_lu": 0.01},
}
FACTEUR_ECRITURE_CACHE = 1.25


# ====================================================================== erreurs
class ErreurFournisseur(Exception):
    """Réponse du modèle inexploitable. `cout` : coût de l'appel (déjà facturé) ; `brut` : texte reçu."""

    def __init__(self, message: str, cout: Cout | None = None, brut: str = "") -> None:
        super().__init__(message)
        self.cout = cout
        self.brut = brut


class RefusModele(ErreurFournisseur):
    """Le modèle a refusé de répondre (stop_reason == "refusal"), repli compris."""


class ReponseTronquee(ErreurFournisseur):
    """Réponse coupée par max_tokens : JSON incomplet."""


class JsonInvalide(ErreurFournisseur):
    """Texte non JSON, pas un objet, ou non conforme au schéma."""


class BudgetAtteint(Exception):
    """Plafond de dépense du dossier atteint : aucun nouvel appel au modèle."""


# ====================================================================== coût et budget
def _tarif(modele: str) -> dict[str, float]:
    """Tarif du modèle ; modèle inconnu (ex. modèle de repli) : tarif de sa famille, sinon le plus cher (prudence)."""
    if modele in TARIFS:
        return TARIFS[modele]
    m = (modele or "").lower()
    for famille in ("opus", "sonnet", "haiku"):
        if famille in m:
            return TARIFS[f"claude-{famille}-5-5"]
    return TARIFS["claude-opus-5-5"]


def cout_usd(modele: str, entree: int, sortie: int, cache_lus: int = 0, cache_ecrits: int = 0) -> float:
    """Coût en USD d'un appel selon `TARIFS` (tokens d'entrée hors cache, sortie, cache lu, cache écrit)."""
    t = _tarif(modele)
    return (entree * t["entree"] + sortie * t["sortie"] + cache_lus * t["cache_lu"]
            + cache_ecrits * t["entree"] * FACTEUR_ECRITURE_CACHE) / 1_000_000


class Budget:
    """Cumul des coûts d'un dossier, borné par `plafond_usd`."""

    def __init__(self, plafond_usd: float = BUDGET_DEFAUT_USD) -> None:
        self.plafond_usd = float(plafond_usd)
        self.couts: list[Cout] = []

    @property
    def total_usd(self) -> float:
        """Total dépensé (USD)."""
        return sum(c.usd for c in self.couts)

    @property
    def reste_usd(self) -> float:
        """Reste disponible (USD, jamais négatif)."""
        return max(0.0, self.plafond_usd - self.total_usd)

    def ajouter(self, cout: Cout | None) -> None:
        """Enregistre le coût d'un appel (réussi ou non)."""
        if cout is not None:
            self.couts.append(cout)

    def depasse(self) -> bool:
        """Vrai si le plafond est atteint ou dépassé."""
        return self.total_usd >= self.plafond_usd - 1e-9   # tolérance d'arrondi des flottants

    def verifier(self) -> None:
        """Lève `BudgetAtteint` si le plafond est atteint."""
        if self.depasse():
            raise BudgetAtteint(f"budget du dossier atteint : {self.total_usd:.4f} $ / {self.plafond_usd:.2f} $")


# ====================================================================== protocole
@runtime_checkable
class Fournisseur(Protocol):
    """Accès au modèle : un objet JSON conforme à `schema` et le coût de l'appel."""

    def json(self, etape: str, systeme: str, utilisateur: str, schema: dict, max_tokens: int) -> tuple[dict, Cout]: ...


def appeler(fournisseur: Fournisseur, budget: Budget | None, etape: str, systeme: str, utilisateur: str,
            schema: dict, max_tokens: int) -> tuple[dict, Cout]:
    """Appel budgété : refuse si le plafond est atteint (`BudgetAtteint`), cumule le coût même en cas d'échec."""
    if budget is not None:
        budget.verifier()
    try:
        donnees, cout = fournisseur.json(etape, systeme, utilisateur, schema, max_tokens)
    except ErreurFournisseur as e:
        if budget is not None:
            budget.ajouter(e.cout)
        raise
    if budget is not None:
        budget.ajouter(cout)
    return donnees, cout


# ====================================================================== validation de forme
def lire_json(texte: str) -> dict:
    """Analyse le texte en objet JSON (dict) ; lève `JsonInvalide` sinon."""
    try:
        donnees = json.loads(texte)
    except (TypeError, ValueError) as e:
        raise JsonInvalide(f"réponse non JSON : {e}", brut=texte or "") from e
    if not isinstance(donnees, dict):
        raise JsonInvalide("la réponse n'est pas un objet JSON", brut=texte)
    return donnees


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(v: Any, t: str) -> bool:
    if t == "integer":
        # JSON Schema : 2.0 est un entier valide
        return (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer())
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    attendu = _TYPES.get(t)
    return attendu is None or isinstance(v, attendu)


def verifier_schema(donnees: Any, schema: dict, chemin: str = "$", racine: dict | None = None,
                    limite: int = 20) -> list[str]:
    """Contrôle minimal JSON Schema (type, enum, const, required, properties, additionalProperties, items,
    anyOf/oneOf, $ref locaux, minimum/maximum). Renvoie les erreurs (20 au plus)."""
    racine = racine if racine is not None else schema
    erreurs: list[str] = []

    def ajout(msg: str) -> None:
        if len(erreurs) < limite:
            erreurs.append(msg)

    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cible: Any = racine
        for part in ref[2:].split("/"):
            cible = cible.get(part, {}) if isinstance(cible, dict) else {}
        return verifier_schema(donnees, cible, chemin, racine, limite)
    for cle in ("anyOf", "oneOf"):
        if isinstance(schema.get(cle), list):
            variantes = [verifier_schema(donnees, s, chemin, racine, limite) for s in schema[cle]]
            if not any(not v for v in variantes):
                ajout(f"{chemin} : aucune variante acceptée ({min(variantes, key=len)[0] if variantes else '?'})")
            return erreurs
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(donnees, x) for x in types):
            ajout(f"{chemin} : type {type(donnees).__name__}, attendu {'|'.join(types)}")
            return erreurs
    if "enum" in schema and donnees not in schema["enum"]:
        ajout(f"{chemin} : valeur {donnees!r} hors liste {schema['enum'][:10]}")
    if "const" in schema and donnees != schema["const"]:
        ajout(f"{chemin} : valeur {donnees!r}, attendu {schema['const']!r}")
    if isinstance(donnees, (int, float)) and not isinstance(donnees, bool):
        if "minimum" in schema and donnees < schema["minimum"]:
            ajout(f"{chemin} : {donnees} < minimum {schema['minimum']}")
        if "maximum" in schema and donnees > schema["maximum"]:
            ajout(f"{chemin} : {donnees} > maximum {schema['maximum']}")
    if isinstance(donnees, dict):
        props = schema.get("properties") or {}
        for cle in schema.get("required") or []:
            if cle not in donnees:
                ajout(f"{chemin} : clé obligatoire « {cle} » absente")
        for cle, val in donnees.items():
            if cle in props:
                erreurs += verifier_schema(val, props[cle], f"{chemin}.{cle}", racine, limite - len(erreurs))
            elif schema.get("additionalProperties") is False:
                ajout(f"{chemin} : clé inattendue « {cle} »")
            elif isinstance(schema.get("additionalProperties"), dict):
                erreurs += verifier_schema(val, schema["additionalProperties"], f"{chemin}.{cle}", racine,
                                           limite - len(erreurs))
    if isinstance(donnees, list) and isinstance(schema.get("items"), dict):
        for i, val in enumerate(donnees):
            if len(erreurs) >= limite:
                break
            erreurs += verifier_schema(val, schema["items"], f"{chemin}[{i}]", racine, limite - len(erreurs))
    return erreurs[:limite]


def _valider(texte: str, schema: dict, cout: Cout | None) -> dict:
    """JSON + schéma ; lève `JsonInvalide` (avec coût et texte brut) en cas d'échec."""
    try:
        donnees = lire_json(texte)
    except JsonInvalide as e:
        e.cout = cout
        raise
    erreurs = verifier_schema(donnees, schema) if schema else []
    if erreurs:
        raise JsonInvalide("réponse non conforme au schéma : " + " ; ".join(erreurs[:5]), cout=cout, brut=texte)
    return donnees


def _attr(obj: Any, nom: str, defaut: Any = None) -> Any:
    """Lecture tolérante (objet SDK ou dict)."""
    if obj is None:
        return defaut
    if isinstance(obj, dict):
        return obj.get(nom, defaut)
    return getattr(obj, nom, defaut)


# ====================================================================== Anthropic
class FournisseurAnthropic:
    """Claude via le SDK `anthropic` : sortie structurée (json_schema), cache du prompt système, effort,
    repli serveur (`fallbacks="default"`) pour les modèles qui l'acceptent ; coût lu dans `response.usage`."""

    def __init__(self, modele: str = MODELE_DEFAUT, effort: str | None = EFFORT_DEFAUT, client: Any = None,
                 api_key: str | None = None, timeout: float = 300.0, max_retries: int = 2) -> None:
        self.modele = modele
        self.effort = effort
        if client is None:
            import anthropic  # import tardif : les tests injectent un faux client
            client = anthropic.Anthropic(api_key=api_key or None, timeout=timeout, max_retries=max_retries)
        self.client = client
        self.dernier_echange: dict = {}

    def _parametres(self, systeme: str, utilisateur: str, schema: dict, max_tokens: int) -> dict:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        return {
            "model": self.modele,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": systeme, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": utilisateur}],
            "output_config": output_config,
        }

    def _cout(self, etape: str, reponse: Any) -> Cout:
        """Coût réel : par tentative (`usage.iterations`, repli compris) si présent, sinon `usage` global."""
        usage = _attr(reponse, "usage")
        servi = _attr(reponse, "model") or self.modele
        iterations = _attr(usage, "iterations") or []
        lignes = [it for it in iterations if _attr(it, "input_tokens") is not None] or [usage]
        cout = Cout(modele=servi, etape=etape)
        for it in lignes:
            e = int(_attr(it, "input_tokens", 0) or 0)
            s = int(_attr(it, "output_tokens", 0) or 0)
            lu = int(_attr(it, "cache_read_input_tokens", 0) or 0)
            ec = int(_attr(it, "cache_creation_input_tokens", 0) or 0)
            modele_it = _attr(it, "model") if it is not usage else None
            cout.tokens_entree += e
            cout.tokens_sortie += s
            cout.tokens_cache_lus += lu
            cout.tokens_cache_ecrits += ec
            cout.usd += cout_usd(modele_it or (servi if it is usage else self.modele), e, s, lu, ec)
        cout.usd = round(cout.usd, 6)
        return cout

    def json(self, etape: str, systeme: str, utilisateur: str, schema: dict, max_tokens: int) -> tuple[dict, Cout]:
        """Appel structuré ; lève `RefusModele`, `ReponseTronquee` ou `JsonInvalide` (avec coût) si inexploitable."""
        params = self._parametres(systeme, utilisateur, schema, max_tokens)
        if self.modele in MODELES_AVEC_REPLI:
            reponse = self.client.beta.messages.create(betas=[BETA_REPLI], fallbacks="default", **params)
        else:
            reponse = self.client.messages.create(**params)
        cout = self._cout(etape, reponse)
        texte = "".join(_attr(b, "text", "") or "" for b in (_attr(reponse, "content") or [])
                        if _attr(b, "type") == "text")
        stop = _attr(reponse, "stop_reason")
        self.dernier_echange = {"etape": etape, "modele": cout.modele, "stop_reason": stop, "brut": texte}
        if stop == "refusal":
            details = _attr(reponse, "stop_details")
            categorie = _attr(details, "category")
            raise RefusModele(f"refus du modèle {cout.modele}" + (f" (catégorie {categorie})" if categorie else ""),
                              cout=cout, brut=texte)
        if stop == "max_tokens":
            raise ReponseTronquee(f"réponse tronquée à {max_tokens} tokens (augmenter max_tokens)", cout=cout,
                                  brut=texte)
        return _valider(texte, schema, cout), cout


# ====================================================================== OpenRouter (repli)
class FournisseurOpenRouter:
    """Repli sur `app/llm.py` (modèles gratuits OpenRouter) : JSON demandé dans le prompt, coût nul."""

    def __init__(self, settings: Any, client: Any = None) -> None:
        if client is None:
            from ..llm import OpenRouterClient
            client = OpenRouterClient(settings)
        self.client = client
        self.dernier_echange: dict = {}

    def json(self, etape: str, systeme: str, utilisateur: str, schema: dict, max_tokens: int) -> tuple[dict, Cout]:
        """Appel avec validation de forme dans la boucle de relance d'`OpenRouterClient`."""
        from ..llm import LLMError

        consigne = ("\n\nRéponds uniquement par un objet JSON conforme à ce schéma :\n"
                    + json.dumps(schema, ensure_ascii=False, sort_keys=True))

        def valider(d: dict) -> dict:
            erreurs = verifier_schema(d, schema) if schema else []
            if erreurs:
                raise ValueError("non conforme au schéma : " + " ; ".join(erreurs[:5]))
            return d

        try:
            donnees, modele = self.client.complete_validated(systeme + consigne, utilisateur, valider)
        except LLMError as e:
            raise JsonInvalide(f"OpenRouter : {e}", cout=Cout(modele="openrouter", etape=etape)) from e
        cout = Cout(modele=modele, etape=etape)
        self.dernier_echange = {"etape": etape, "modele": modele, "stop_reason": None,
                                "brut": json.dumps(donnees, ensure_ascii=False)}
        return donnees, cout


# ====================================================================== simulé (tests)
class FournisseurSimule:
    """Fournisseur de test : `reponses` (liste consommée dans l'ordre, ou dict par étape) ou `fonction`.

    Une réponse peut être un dict, un texte JSON (validé comme une vraie réponse) ou une exception (levée).
    Chaque appel est noté dans `appels` ; le coût est fixe (`usd_par_appel`).
    """

    def __init__(self, reponses: list | dict | None = None,
                 fonction: Callable[[str, str, str, dict], Any] | None = None,
                 usd_par_appel: float = 0.0, modele: str = "simule", valider_schema: bool = True) -> None:
        self.reponses = reponses if reponses is not None else []
        self.fonction = fonction
        self.usd_par_appel = usd_par_appel
        self.modele = modele
        self.valider_schema = valider_schema
        self.appels: list[dict] = []
        self.dernier_echange: dict = {}

    def _suivante(self, etape: str, systeme: str, utilisateur: str, schema: dict) -> Any:
        if self.fonction is not None:
            return self.fonction(etape, systeme, utilisateur, schema)
        if isinstance(self.reponses, dict):
            r = self.reponses.get(etape)
            if isinstance(r, list):
                if not r:
                    raise ErreurFournisseur(f"plus de réponse simulée pour l'étape « {etape} »")
                return r.pop(0)
            if r is None:
                raise ErreurFournisseur(f"aucune réponse simulée pour l'étape « {etape} »")
            return r
        if not self.reponses:
            raise ErreurFournisseur("plus de réponse simulée")
        return self.reponses.pop(0)

    def json(self, etape: str, systeme: str, utilisateur: str, schema: dict, max_tokens: int) -> tuple[dict, Cout]:
        """Renvoie la réponse simulée suivante (validée comme une vraie réponse)."""
        self.appels.append({"etape": etape, "systeme": systeme, "utilisateur": utilisateur, "schema": schema,
                            "max_tokens": max_tokens})
        cout = Cout(modele=self.modele, etape=etape, usd=self.usd_par_appel)
        r = self._suivante(etape, systeme, utilisateur, schema)
        if isinstance(r, BaseException):
            if isinstance(r, ErreurFournisseur) and r.cout is None:
                r.cout = cout
            raise r
        texte = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        self.dernier_echange = {"etape": etape, "modele": self.modele, "stop_reason": "end_turn", "brut": texte}
        donnees = _valider(texte, schema if self.valider_schema else {}, cout)
        return donnees, cout


# ====================================================================== configuration
def _reglage(settings: Any, attribut: str, variable: str, defaut: str = "") -> str:
    """Attribut de `settings` s'il existe, sinon variable d'environnement, sinon défaut."""
    v = getattr(settings, attribut, None) if settings is not None else None
    if v in (None, ""):
        v = os.getenv(variable, "")
    return str(v).strip() or defaut


def budget_depuis_config(settings: Any = None) -> Budget:
    """`Budget` au plafond `BUDGET_DOSSIER_USD` (défaut 3 $)."""
    brut = _reglage(settings, "budget_dossier_usd", "BUDGET_DOSSIER_USD", str(BUDGET_DEFAUT_USD))
    try:
        # Accepte la virgule décimale (« 2,5 ») ; refuse nan/inf (plafond inopérant).
        valeur = float(str(brut).strip().replace(",", "."))
    except ValueError:
        valeur = None
    if valeur is None or not math.isfinite(valeur) or valeur < 0:
        if str(brut).strip() not in ("", str(BUDGET_DEFAUT_USD)):
            log.warning("BUDGET_DOSSIER_USD invalide (%r) : défaut %s $ appliqué", brut, BUDGET_DEFAUT_USD)
        return Budget(BUDGET_DEFAUT_USD)
    return Budget(valeur)


def fournisseur_depuis_config(settings: Any = None, modele: str | None = None,
                              variable_modele: str = "MODELE_CARTE") -> Fournisseur | None:
    """Fournisseur selon `LLM_FOURNISSEUR` (anthropic par défaut | openrouter) ; `None` si aucune clé
    (la carte passe alors en mode « règles »). `variable_modele` : MODELE_CARTE, MODELE_AGENT, MODELE_REDACTION…"""
    choix = _reglage(settings, "llm_fournisseur", "LLM_FOURNISSEUR", "anthropic").lower()
    if choix == "openrouter":
        if not _reglage(settings, "openrouter_api_key", "OPENROUTER_API_KEY"):
            return None
        if settings is None:
            from ..config import get_settings
            settings = get_settings()
        return FournisseurOpenRouter(settings)
    cle = _reglage(settings, "anthropic_api_key", "ANTHROPIC_API_KEY")
    if not cle:
        return None
    modele = modele or _reglage(settings, variable_modele.lower(), variable_modele, MODELE_DEFAUT)
    effort = _reglage(settings, "modele_effort", "MODELE_EFFORT", EFFORT_DEFAUT)
    return FournisseurAnthropic(modele=modele, effort=effort, api_key=cle)

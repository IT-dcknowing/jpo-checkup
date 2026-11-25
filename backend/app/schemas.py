"""Modèles Pydantic : formulaire de dépôt et contrat JSON de l'extraction IA."""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ND = "NON_DISPONIBLE"

# --------------------------------------------------------------------------- secteurs
# Libellés identiques à ceux du formulaire (index.html) ; les 6 derniers sont exemptés de FNE (spec §4.4).
_SECTEURS = [
    ("Commerce et distribution", False), ("BTP et immobilier", False), ("Industrie et transformation", False),
    ("Services aux entreprises", False), ("Transport et logistique", False), ("Agriculture et agro-industrie", False),
    ("Hôtellerie et restauration", False), ("Santé (hors pharmacie)", False), ("Éducation et formation", False),
    ("Télécoms et TIC", False), ("Banque", True), ("Assurance", True), ("Pharmacie", True),
    ("Station-service (carburant)", True), ("Compagnie aérienne", True),
    ("Concessionnaire eau, électricité ou téléphone", True), ("Autre", False),
]


def _slug(label: str) -> str:
    base = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", base).strip("_")


SECTEURS = [{"code": _slug(label), "label": label, "fne_exempt": ex} for label, ex in _SECTEURS]
SECTEUR_PAR_LABEL = {s["label"]: s for s in SECTEURS}
SECTEUR_PAR_CODE = {s["code"]: s for s in SECTEURS}

# « Je ne sais pas » arrive du front sous le nom NON_DISPONIBLE ; en interne on garde NE_SAIT_PAS.
StatutDecl = Literal["A_JOUR", "EN_RETARD", "NE_SAIT_PAS"]
EMAIL_RE = re.compile(r"^[^@\s|]+@[^@\s|]+\.[^@\s|]{2,}$")


def _nsp(v):
    return "NE_SAIT_PAS" if v == "NON_DISPONIBLE" else v


class DepotForm(BaseModel):
    raison_sociale: str = Field(min_length=2, max_length=120)
    ncc: str | None = Field(default=None, max_length=30)
    secteur: str
    effectif: int = Field(ge=0, le=100_000)
    contact_nom: str = Field(min_length=2, max_length=120)
    contact_email: str = Field(max_length=200)
    contact_telephone: str = Field(min_length=6, max_length=30)
    type_client: Literal["ACTUEL", "REACTIVER", "PROSPECT"]
    jour_evenement: str
    regime_fiscal: Literal["TEE", "RME", "REEL_SIMPLIFIE", "REEL_NORMAL", "NE_SAIT_PAS"]
    fne: Literal["ACTIVE", "EN_COURS_ACTIVATION", "NON_ACTIVE", "NON_APPLICABLE", "NE_SAIT_PAS"]
    dgi: StatutDecl
    cnps: StatutDecl
    its: StatutDecl
    cmu: StatutDecl
    disa: StatutDecl
    tva_exoneree: bool = False
    consentement: bool

    @field_validator("raison_sociale", "contact_nom", "ncc", "contact_telephone", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("contact_email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not EMAIL_RE.match(v):
            raise ValueError("adresse e-mail invalide")
        return v

    @field_validator("fne", "dgi", "cnps", "its", "cmu", "disa", mode="before")
    @classmethod
    def _non_dispo(cls, v):
        return _nsp(v)

    @field_validator("secteur")
    @classmethod
    def _secteur(cls, v: str) -> str:
        found = SECTEUR_PAR_LABEL.get(v) or SECTEUR_PAR_CODE.get(v)
        if not found:
            raise ValueError("secteur inconnu")
        return found["label"]  # forme canonique : le libellé

    @field_validator("consentement")
    @classmethod
    def _consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("le consentement au traitement des données est obligatoire")
        return v

    @property
    def secteur_label(self) -> str:
        return self.secteur

    @property
    def secteur_exempte_fne(self) -> bool:
        return SECTEUR_PAR_LABEL[self.secteur]["fne_exempt"]


# --------------------------------------------------------------------------- extraction IA
def parse_number(raw: Any) -> float | str:
    """Nombre tolérant (« 125 000 000 », « 1.250.000,50 », « (1 000) », « 12 M ») ou ND."""
    if raw is None or isinstance(raw, bool):
        return ND
    if isinstance(raw, (int, float)):
        x = float(raw)
        return x if math.isfinite(x) else ND
    s = str(raw).strip()
    if not s or s.upper() == ND:
        return ND
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.startswith("−")
    s = re.sub(r"(?i)f?cfa|xof|[\s  €$]", "", s).strip("()-−+")
    if not re.fullmatch(r"[\d.,]+", s):
        return ND
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        tail = s.rsplit(sep, 1)[1]
        s = s.replace(sep, "") if (s.count(sep) > 1 or len(tail) == 3) else s.replace(sep, ".")
    try:
        x = float(s)
    except ValueError:
        return ND
    return -x if neg else x


class Champ(BaseModel):
    valeur: float | Literal["NON_DISPONIBLE"] = ND
    confiance: float = 0.0
    sources: str = ""

    @model_validator(mode="before")
    @classmethod
    def _bare_value(cls, data):
        if not isinstance(data, dict):  # l'IA a renvoyé un nombre nu : confiance moyenne par défaut
            return {"valeur": data, "confiance": 0.7 if parse_number(data) != ND else 0.0}
        return data

    @field_validator("valeur", mode="before")
    @classmethod
    def _num(cls, v):
        return parse_number(v)

    @field_validator("confiance", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return 0.0
        if x > 1:  # confiance exprimée en pourcentage
            x = x / 100
        return min(max(x, 0.0), 1.0) if math.isfinite(x) else 0.0

    @field_validator("sources", mode="before")
    @classmethod
    def _src(cls, v):
        return "" if v is None else str(v)[:300]


CR_FIELDS = ["chiffre_affaires_ht", "chiffre_affaires_n1", "cout_achats_marchandises", "cout_achats_matieres", "charges_personnel",
             "charges_exploitation_autres", "dotations_amortissements", "charges_financieres", "produits_financiers", "impot_benefices"]
ACTIF_FIELDS = ["immobilisations_nettes", "stocks", "creances_clients", "autres_creances", "tresorerie_actif"]
PASSIF_FIELDS = ["capitaux_propres", "dettes_financieres", "dettes_fournisseurs", "dettes_fiscales_sociales", "tresorerie_passif"]


def _group(name: str, fields: list[str]):
    from pydantic import create_model
    return create_model(name, **{f: (Champ, Field(default_factory=Champ)) for f in fields})


CompteResultat = _group("CompteResultat", CR_FIELDS)
Actif = _group("Actif", ACTIF_FIELDS)
Passif = _group("Passif", PASSIF_FIELDS)


class Bilan(BaseModel):
    actif: Actif = Field(default_factory=Actif)  # type: ignore[valid-type]
    passif: Passif = Field(default_factory=Passif)  # type: ignore[valid-type]


class Meta(BaseModel):
    document_type: str = "AUTRE"
    period_start: str = ND
    period_end: str = ND
    currency: str = "XOF"
    accounting_standard: str = "SYSCOHADA_REVISE"
    confidence_global: float | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("document_type", "period_start", "period_end", "currency", "accounting_standard", mode="before")
    @classmethod
    def _text(cls, v):
        return ND if v is None else str(v)[:60]

    @field_validator("confidence_global", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        x = x / 100 if x > 1 else x
        return min(max(x, 0.0), 1.0) if math.isfinite(x) else None

    @field_validator("warnings", mode="before")
    @classmethod
    def _warn(cls, v):
        if isinstance(v, str):
            return [v]
        return [str(x)[:300] for x in v][:30] if isinstance(v, list) else []


class IAOutput(BaseModel):
    meta: Meta = Field(default_factory=Meta)
    compte_resultat: CompteResultat = Field(default_factory=CompteResultat)  # type: ignore[valid-type]
    bilan: Bilan = Field(default_factory=Bilan)

    @model_validator(mode="after")
    def _non_vide(self):
        if not ({"compte_resultat", "bilan"} & self.model_fields_set):
            raise ValueError("structure attendue absente : compte_resultat et bilan manquent")
        return self

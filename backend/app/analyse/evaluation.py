"""Mode évaluation : export JSON complet d'un dossier analysé.

Rassemble profils (échantillons pseudonymisés), carte, échanges IA, contrôles, requêtes, faits, parties non
évaluables, coûts et rapport, sous une forme sérialisable par `json.dumps` (dates en ISO, NaN -> null).
Aucune donnée personnelle en clair dans les échantillons exportés : les colonnes de personnes (tiers, salarié,
vendeur, matricule) passent par la pseudonymisation de l'entrepôt, ou à défaut par une pseudonymisation locale
stable.
"""
from __future__ import annotations

import dataclasses
import inspect
import math
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import Any

from .lecture import _ligne_mot_total, normaliser_texte
from .modele import Carte, Cout, Dossier, ProfilFeuille

VERSION_EXPORT = 1
MAX_LIGNES_REQUETE = 50
ROLES_PERSONNELS = ("tiers", "salarie", "vendeur", "matricule")
# Indices de secours (sans carte) : mots d'en-tête désignant une personne ou un tiers (pluriels tolérés)
_INDICES_ENTETE = {
    "matricule": ("matricule", "n matricule", "id salarie", "employee id"),
    "salarie": ("salarie", "employe", "agent", "nom et prenom", "nom prenom", "nom & prenom", "employee"),
    "vendeur": ("vendeur", "commercial", "caissier", "caissiere", "seller", "salesman"),
    "tiers": ("client", "fournisseur", "tiers", "beneficiaire", "donneur d'ordre", "customer", "supplier",
              "payee", "contrepartie"),
}
_PREFIXES = {"tiers": "Tiers", "salarie": "Salarie", "vendeur": "Vendeur", "matricule": "Matricule"}
_PSEUDONYME = re.compile(r"^[A-Z][A-Za-z]+_\d{3,}$")
_LONGUEUR_MIN_REMPLACEMENT = 3
# Libellé de total « pur » (« TOTAL », « Sous-total : », « Total général 2025 ») : jamais une personne
_LIBELLE_TOTAL = re.compile(r"^[\W_]*(sous[- ]?totaux|sous[- ]?total|totaux|total|cumuls?|cumule|report|a nouveau|"
                            r"solde initial|solde de depart)[\W\d_]*((general|generale|generaux|ht|ttc|net|brut)"
                            r"[\W\d_]*)?$")
_SEP_CHIFFRES = " .,  '"                       # séparateurs de milliers / décimales


def _motif_entete(mot: str) -> re.Pattern:
    """Mot d'indice -> motif « mot entier », chaque mot pouvant porter une marque de pluriel (s/x)."""
    parties = [re.escape(m) + "[sx]?" for m in mot.split(" ")]
    return re.compile(r"(^|\W)" + r"\s+".join(parties) + r"($|\W)")


_MOTIFS_ENTETE = {role: [_motif_entete(m) for m in mots] for role, mots in _INDICES_ENTETE.items()}


def est_libelle_total(valeur: Any) -> bool:
    """Vrai si la valeur est un simple libellé de total (« TOTAL », « Sous-total », « Total général »…)."""
    return isinstance(valeur, str) and bool(_LIBELLE_TOTAL.match(normaliser_texte(valeur)))


# ------------------------------------------------------------------ sérialisation
def _numpy_temps(obj: Any) -> tuple[bool, Any]:
    """datetime64 / timedelta64 numpy -> (True, ISO ou secondes ou None) ; (False, None) sinon."""
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        return False, None
    if isinstance(obj, np.datetime64):
        if np.isnat(obj):
            return True, None
        v = obj.astype("datetime64[us]").item()
        return True, v.isoformat() if isinstance(v, (datetime, date)) else str(np.datetime_as_string(obj))
    if isinstance(obj, np.timedelta64):
        if np.isnat(obj):
            return True, None
        return True, float(obj / np.timedelta64(1, "s"))
    return False, None


def json_sur(obj: Any) -> Any:
    """Convertit récursivement un objet en structure JSON : dataclass, pydantic, dates ISO, Decimal,
    NaN / NaT / NA -> None, scalaires numpy et pandas, UUID et autres objets -> texte."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if type(obj).__name__ in ("NaTType", "NAType"):     # pandas.NaT, pandas.NA
        return None
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, Decimal):
        return json_sur(float(obj))
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, timedelta):
        return obj.total_seconds()
    if isinstance(obj, Enum):
        return json_sur(obj.value)
    if isinstance(obj, PurePath):
        return str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    traite, v = _numpy_temps(obj)
    if traite:
        return v
    if hasattr(obj, "model_dump"):                       # pydantic
        return json_sur(obj.model_dump(mode="json"))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: json_sur(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): json_sur(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [json_sur(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):      # scalaires numpy / pandas
        try:
            return json_sur(obj.item())
        except (TypeError, ValueError):
            pass
    if hasattr(obj, "isoformat"):                        # pandas.Timestamp et apparentés
        try:
            return obj.isoformat()
        except (TypeError, ValueError):
            pass
    return str(obj)


# ------------------------------------------------------------------ pseudonymisation des profils
def _regex_trie(mots: list[str]) -> str:
    """Alternative regex compacte (arbre de préfixes) : rapide même pour des dizaines de milliers de mots."""
    arbre: dict = {}
    for m in mots:
        noeud = arbre
        for ch in m:
            noeud = noeud.setdefault(ch, {})
        noeud[""] = True

    def rendu(noeud: dict) -> str:
        fin = "" in noeud
        branches = [re.escape(ch) + rendu(sous) for ch, sous in sorted(noeud.items()) if ch != ""]
        if not branches:
            return ""
        corps = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        if fin:
            corps = "(?:" + corps + ")?"
        return corps

    import sys
    limite = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limite, 10_000))
    try:
        return rendu(arbre)
    finally:
        sys.setrecursionlimit(limite)


class _Pseudonymiseur:
    """Pseudonymise via l'entrepôt si possible (avec le rôle), sinon localement (« Tiers_001 »…), et mémorise les
    correspondances pour les remplacer dans les textes libres."""

    def __init__(self, entrepot: Any):
        self.entrepot = entrepot
        self.correspondances: dict[str, str] = _correspondances_entrepot(entrepot)
        self._locaux: dict[tuple[str, str], str] = {}
        self._compteurs: dict[str, int] = {}
        self._regex: re.Pattern | None = None
        self._par_cle: dict[str, str] = {}
        self._a_jour = False
        self._fonction = getattr(entrepot, "pseudonymiser", None) if entrepot is not None else None
        self._avec_role = _accepte_role(self._fonction)

    def __call__(self, valeur: Any, role: str) -> Any:
        if valeur is None or str(valeur).strip() == "":
            return valeur
        valeur = str(valeur)
        if valeur in self.correspondances:
            return self.correspondances[valeur]
        pseudo = None
        if callable(self._fonction):
            try:
                pseudo = self._fonction(valeur, role) if self._avec_role else self._fonction(valeur)
            except Exception:  # noqa: BLE001 — une erreur de l'entrepôt ne doit pas faire fuiter la valeur
                pseudo = None
        if not pseudo or pseudo == valeur:
            pseudo = self._local(valeur, role)
        pseudo = str(pseudo)
        self.correspondances[valeur] = pseudo
        self._a_jour = False
        return pseudo

    def _local(self, valeur: str, role: str) -> str:
        cle = (role, normaliser_texte(valeur))
        if cle not in self._locaux:
            n = self._compteurs.get(role, 0) + 1
            self._compteurs[role] = n
            self._locaux[cle] = f"{_PREFIXES.get(role, 'Tiers')}_{n:03d}"
        return self._locaux[cle]

    def _compiler(self) -> None:
        """Expression unique (insensible à la casse) de toutes les valeurs d'origine remplaçables."""
        self._par_cle = {}
        for orig, pseudo in self.correspondances.items():
            o = orig.strip()
            if len(o) >= _LONGUEUR_MIN_REMPLACEMENT and not est_libelle_total(o) and o != pseudo:
                self._par_cle.setdefault(o.lower(), pseudo)
        self._regex = (re.compile(r"(?<!\w)(?:" + _regex_trie(sorted(self._par_cle)) + r")(?!\w)", re.IGNORECASE)
                       if self._par_cle else None)
        self._a_jour = True

    def texte(self, s: Any) -> Any:
        """Remplace dans un texte libre les valeurs d'origine connues : mots entiers, casse ignorée, en une passe
        (pas de remplacement dans un pseudonyme inséré) et sans toucher aux nombres (« 2 100 000 »)."""
        if not isinstance(s, str) or not s:
            return s
        if not self._a_jour:
            self._compiler()
        if self._regex is None:
            return s

        def remplacer(m: re.Match) -> str:
            trouve = m.group(0)
            debut, fin = m.start(), m.end()
            # code numérique collé à un groupe de chiffres : partie d'un montant, pas un tiers
            if trouve[0].isdigit() and debut >= 2 and s[debut - 1] in _SEP_CHIFFRES and s[debut - 2].isdigit():
                return trouve
            if trouve[-1].isdigit() and fin + 1 < len(s) and s[fin] in _SEP_CHIFFRES and s[fin + 1].isdigit():
                return trouve
            return self._par_cle.get(trouve.lower(), trouve)

        return self._regex.sub(remplacer, s)


def _accepte_role(fonction: Any) -> bool:
    """Vrai si `fonction(valeur, role)` est appelable (contrat `Entrepot.pseudonymiser(valeur, role)`)."""
    if not callable(fonction):
        return False
    try:
        sig = inspect.signature(fonction)
        sig.bind("x", "tiers")
        return True
    except (TypeError, ValueError):
        return False


def _correspondances_entrepot(entrepot: Any) -> dict[str, str]:
    """Table origine -> pseudonyme lue dans `entrepot.pseudonymes` (plate ou par rôle ; sens détecté)."""
    brut = getattr(entrepot, "pseudonymes", None) if entrepot is not None else None
    if not isinstance(brut, dict):
        return {}
    plat: dict[str, str] = {}
    for k, v in brut.items():
        if isinstance(v, dict):
            for a, b in v.items():
                if a is not None and b is not None:
                    plat.setdefault(str(a), str(b))
        elif k is not None and v is not None:
            plat.setdefault(str(k), str(v))
    if plat and sum(bool(_PSEUDONYME.match(k)) for k in plat) > sum(bool(_PSEUDONYME.match(v)) for v in plat.values()):
        plat = {v: k for k, v in plat.items()}           # table stockée pseudonyme -> origine
    return {k: v for k, v in plat.items() if k and k != v and not est_libelle_total(k)}


def _role_par_entete(entete: str) -> str | None:
    """Rôle personnel deviné d'après l'en-tête (indice de secours quand la carte ne couvre pas le tableau)."""
    e = normaliser_texte(entete)
    if not e:
        return None
    for role, motifs in _MOTIFS_ENTETE.items():
        if any(m.search(e) for m in motifs):
            return role
    return None


def colonnes_personnelles(profil: ProfilFeuille, carte: Carte | None) -> dict[int, str]:
    """Index de colonne -> rôle personnel (tiers/salarie/vendeur/matricule) : carte d'abord, en-têtes sinon.

    Indices d'en-tête : colonnes texte (ou mixtes) ; un matricule est masqué même s'il est numérique."""
    roles: dict[int, str] = {}
    tc = None
    if carte is not None:
        tc = next((t for t in carte.tableaux if t.id == profil.id), None)
    if tc is not None:
        roles = {c.index: c.role for c in tc.colonnes if c.role in ROLES_PERSONNELS}
    # Indices de secours : complètent la carte (prudence : mieux vaut trop masquer que pas assez)
    ignorees = {c.index for c in tc.colonnes if c.role not in ROLES_PERSONNELS} if tc is not None else set()
    for col in profil.colonnes:
        if col.index in roles or col.index in ignorees or col.type_dominant in ("date", "vide"):
            continue
        role = _role_par_entete(col.entete)
        if role and (col.type_dominant != "nombre" or role == "matricule"):
            roles[col.index] = role
    return roles


def _profil_exporte(profil: ProfilFeuille, carte: Carte | None, pseudo: _Pseudonymiseur) -> dict:
    """Profil sérialisé, colonnes de personnes pseudonymisées (libellés de total laissés lisibles)."""
    d = json_sur(profil)
    roles = colonnes_personnelles(profil, carte)
    suspectes = set(profil.lignes_suspectes_total or [])

    def masquer(v: Any, role: str, ligne_total: bool = False) -> Any:
        if est_libelle_total(v) or (ligne_total and isinstance(v, str) and _ligne_mot_total([v])):
            return v                                     # preuve d'une ligne de total : pas une personne
        return pseudo(v, role)

    for col in d.get("colonnes", []):
        role = roles.get(col.get("index"))
        if role:
            col["exemples"] = [masquer(v, role) for v in col.get("exemples") or []]
            col["pseudonymisee"] = role
    for ligne in d.get("echantillon", []):
        valeurs = ligne.get("valeurs") or []
        total = ligne.get("ligne") in suspectes
        ligne["valeurs"] = [masquer(v, roles[j], total) if j in roles else v for j, v in enumerate(valeurs)]
    return d


# ------------------------------------------------------------------ requêtes, coûts
def _requetes(entrepot: Any) -> list[dict]:
    """Requêtes stockées par l'entrepôt (liste ou dict), résultat tronqué à 50 lignes."""
    brut = getattr(entrepot, "requetes", None) if entrepot is not None else None
    if brut is None:
        return []
    liste = list(brut.values()) if isinstance(brut, dict) else list(brut)
    sortie = []
    for r in liste:
        d = json_sur(r)
        if not isinstance(d, dict):
            continue
        lignes = d.get("lignes") or []
        d["lignes"] = lignes[:MAX_LIGNES_REQUETE]
        d.setdefault("nb_lignes", len(lignes))
        d["lignes_tronquees"] = (d.get("nb_lignes") or 0) > len(d["lignes"])
        sortie.append(d)
    return sortie


def _couts(couts: list[Cout] | None) -> dict:
    """Détail des coûts + total USD, par étape et par modèle."""
    detail = [json_sur(c) for c in (couts or [])]
    detail = [c for c in detail if isinstance(c, dict)]
    par_etape: dict[str, float] = {}
    par_modele: dict[str, float] = {}
    for c in detail:
        usd = float(c.get("usd") or 0.0)
        par_etape[c.get("etape", "")] = par_etape.get(c.get("etape", ""), 0.0) + usd
        par_modele[c.get("modele", "")] = par_modele.get(c.get("modele", ""), 0.0) + usd
    total = sum(float(c.get("usd") or 0.0) for c in detail)
    return {"detail": detail, "total_usd": round(total, 6),
            "par_etape": {k: round(v, 6) for k, v in par_etape.items()},
            "par_modele": {k: round(v, 6) for k, v in par_modele.items()},
            "tokens": {k: sum(int(c.get(k) or 0) for c in detail)
                       for k in ("tokens_entree", "tokens_cache_lus", "tokens_cache_ecrits", "tokens_sortie")}}


def _duree(meta: dict) -> float | None:
    """Durée en secondes : meta['duree_s'], sinon fin - debut (datetime ou ISO)."""
    if meta.get("duree_s") is not None:
        try:
            return round(float(meta["duree_s"]), 3)
        except (TypeError, ValueError):
            return None
    debut, fin = meta.get("debut"), meta.get("fin")
    try:
        debut = datetime.fromisoformat(debut) if isinstance(debut, str) else debut
        fin = datetime.fromisoformat(fin) if isinstance(fin, str) else fin
        if isinstance(debut, datetime) and isinstance(fin, datetime):
            return round((fin - debut).total_seconds(), 3)
    except (TypeError, ValueError):
        pass
    return None


def _anonymiser(obj: Any, pseudo: _Pseudonymiseur) -> Any:
    """Applique les correspondances connues à toutes les chaînes d'une structure JSON."""
    if isinstance(obj, str):
        return pseudo.texte(obj)
    if isinstance(obj, dict):
        return {k: _anonymiser(v, pseudo) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_anonymiser(v, pseudo) for v in obj]
    return obj


# ------------------------------------------------------------------ export
def exporter(dossier: Dossier | None, carte: Carte | dict | None, echanges: list[dict] | None,
             controles: list | None, entrepot: Any, faits: list | None, non_evaluables: list[dict] | None,
             couts: list[Cout] | None, rapport_md: str | None, meta: dict | None) -> dict:
    """Export JSON (sérialisable par json.dumps) d'un dossier pour le mode évaluation.

    `meta` : rapport_id, reference, moteur (défaut « analyse »), modeles, duree_s (ou debut/fin).
    Les échantillons et exemples des colonnes de personnes sont pseudonymisés (entrepôt, avec le rôle, sinon
    local) ; les valeurs connues sont aussi remplacées (mots entiers, casse ignorée, montants préservés) dans les
    titres, noms d'onglet, textes PDF, la carte, les échanges IA et les lignes de requêtes.
    """
    meta = dict(meta or {})
    carte_obj = carte
    if isinstance(carte, dict):
        try:
            carte_obj = Carte.model_validate(carte)
        except Exception:  # noqa: BLE001 — carte invalide : exportée telle quelle, sans rôles exploitables
            carte_obj = None
    pseudo = _Pseudonymiseur(entrepot)

    profils = [_profil_exporte(p, carte_obj, pseudo) for p in (dossier.profils if dossier else [])]
    # Toutes les correspondances sont connues : remplacement dans les textes libres des profils
    for p in profils:
        for cle in ("titre", "feuille"):
            if isinstance(p.get(cle), str):
                p[cle] = pseudo.texte(p[cle])
        if p.get("texte_pages"):
            p["texte_pages"] = [pseudo.texte(t) for t in p["texte_pages"]]

    cout_bloc = _couts(couts)
    modeles = meta.get("modeles")
    if not modeles:
        modeles = sorted({c["modele"] for c in cout_bloc["detail"] if c.get("modele")})
    requetes = _requetes(entrepot)
    for r in requetes:                                   # valeurs non personnelles citant un tiers (libellés)
        r["lignes"] = _anonymiser(r.get("lignes") or [], pseudo)

    export = {
        "version": VERSION_EXPORT,
        "rapport_id": json_sur(meta.get("rapport_id")),
        "reference": json_sur(meta.get("reference")),
        "moteur": json_sur(meta.get("moteur") or "analyse"),
        "modeles": json_sur(modeles),
        "origine_carte": getattr(carte_obj, "origine", None) if carte_obj is not None else None,
        "profils": profils,
        "fichiers_ignores": json_sur(dossier.fichiers_ignores if dossier else []),
        "carte": _anonymiser(json_sur(carte), pseudo),    # réponse validée du modèle : anonymisée comme les échanges
        "echanges_ia": _anonymiser(json_sur(echanges or []), pseudo),
        "controles": json_sur(controles or []),
        "requetes": requetes,
        "faits": json_sur([f.to_dict() if hasattr(f, "to_dict") else f for f in (faits or [])]),
        "non_evaluables": json_sur(non_evaluables or []),
        "couts": cout_bloc,
        "rapport_md": rapport_md or "",
        "duree_s": _duree(meta),
    }
    return export

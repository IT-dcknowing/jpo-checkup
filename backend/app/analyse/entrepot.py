"""Entrepôt DuckDB du dossier : tables brutes, vues normalisées, requêtes bridées et tracées.

- Une table brute `brut_<id>` par tableau : colonnes `c0`, `c1`… en VARCHAR (index de colonne de la carte),
  `_ligne` (n° Excel) et `_retrait` (NULL si la ligne est gardée, sinon « exclue_carte » ou « total_detecte »).
  Les colonnes de personnes (tiers, salarie, vendeur, matricule) y sont déjà pseudonymisées.
- Des vues normalisées (`ventes`, `achats`, `depenses`, `tresorerie`, `paie`, `stocks`, `emprunts`, `balance`,
  `etats_financiers`) construites en Python (conversions `lecture.num` / `lecture.to_date`) puis chargées dans des
  tables typées `n_<vue>` via pyarrow. Lignes exclues par la carte et lignes de total détectées retirées.
- `requete()` : SELECT/WITH uniquement, une instruction, 10 s, 200 lignes retournées, tables relevées.
Aucune règle propre à un client : seulement les rôles de la carte et des indices génériques.
"""
from __future__ import annotations

import re
import threading
from bisect import bisect_left
from dataclasses import replace
from functools import lru_cache
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

import duckdb
import pyarrow as pa

from .lecture import num, to_date
from .lecture import normaliser_texte as _normaliser
from .modele import Carte, Dossier, Requete, Tableau, TableauCarte

DELAI_S = 10.0                 # délai maximal d'une requête
MAX_LIGNES_RESULTAT = 200      # lignes retournées par requête
TOLERANCE_VIREMENT = 1.0       # F
FENETRE_VIREMENT = 3           # jours
TAUX_TVA = 0.18                # taux normal (conversion TTC -> HT, assujettis seulement)

ROLES_PERSONNELS = {"tiers": "Tiers", "salarie": "Salarie", "vendeur": "Vendeur", "matricule": "Matricule"}
NATURES_TRESORERIE = ("banque", "caisse", "mobile_money")
_RETOUR = re.compile(r"\b(retours?|avoirs?)\b")
_MOIS = {"janv": 1, "jan": 1, "fevr": 2, "fev": 2, "feb": 2, "mars": 3, "mar": 3, "avr": 4, "apr": 4, "mai": 5,
         "may": 5, "juin": 6, "jun": 6, "juil": 7, "jul": 7, "aout": 8, "aug": 8, "sept": 9, "sep": 9, "oct": 10,
         "nov": 11, "dec": 12}

_V, _I, _D, _F, _B = pa.string(), pa.int32(), pa.date32(), pa.float64(), pa.bool_()
_SQL = {_V: "VARCHAR", _I: "INTEGER", _D: "DATE", _F: "DOUBLE", _B: "BOOLEAN"}

# Vue -> [(colonne, type, description)] : schéma unique (tables typées, vues, schema_vues)
VUES: dict[str, list[tuple[str, pa.DataType, str]]] = {
    "ventes": [
        ("src", _V, "id du tableau source"), ("ligne", _I, "n° de ligne Excel"), ("date", _D, ""),
        ("piece", _V, "n° de facture / ticket"), ("tiers", _V, "client (pseudonyme)"), ("site", _V, ""),
        ("canal", _V, ""), ("vendeur", _V, "pseudonyme"), ("designation", _V, ""), ("reference", _V, ""),
        ("famille", _V, ""), ("qte", _F, "positive"), ("pu", _F, ""),
        ("remise", _F, "montant de remise, ou taux 0..1 si la carte donne remise_taux"),
        ("montant_brut", _F, "avant remise, positif"),
        ("montant", _F, "net HT si connu, sinon tel que fourni ; positif (voir est_retour)"),
        ("mode_paiement", _V, ""), ("est_retour", _B, "retour / avoir : à déduire"),
        ("doublon", _B, "répétition d'une ligne précédente : à exclure")],
    "achats": [
        ("src", _V, ""), ("ligne", _I, ""), ("date", _D, ""), ("piece", _V, ""), ("tiers", _V, "fournisseur (pseudonyme)"),
        ("designation", _V, ""), ("reference", _V, ""), ("famille", _V, ""), ("qte", _F, ""), ("pu", _F, ""),
        ("montant", _F, "HT si connu, sinon tel que fourni"), ("categorie", _V, ""), ("doublon", _B, "à exclure")],
    "depenses": [
        ("src", _V, ""), ("ligne", _I, ""), ("date", _D, ""), ("categorie", _V, "catégorie du fichier"),
        ("libelle", _V, ""), ("tiers", _V, "pseudonyme"), ("site", _V, ""), ("montant", _F, ""),
        ("mode_paiement", _V, ""), ("categorie_libelle", _V, "catégorie de la carte (loyer, salaire, impot…)"),
        ("doublon", _B, "à exclure")],
    "tresorerie": [
        ("src", _V, ""), ("ligne", _I, ""), ("compte", _V, "compte de trésorerie"),
        ("nature_compte", _V, "banque | caisse | mobile_money"), ("date", _D, ""), ("libelle", _V, ""),
        ("tiers", _V, "pseudonyme"), ("entree", _F, "≥ 0"), ("sortie", _F, "≥ 0"), ("solde", _F, "solde après opération"),
        ("frais", _F, ""), ("type_operation", _V, ""), ("categorie_libelle", _V, "catégorie de la carte"),
        ("virement_interne", _B, "virement entre comptes du dossier : à exclure des flux")],
    "paie": [
        ("src", _V, ""), ("ligne", _I, ""), ("periode", _D, "1er jour du mois de paie"), ("matricule", _V, "pseudonyme"),
        ("salarie", _V, "pseudonyme"), ("site", _V, ""), ("brut", _F, ""), ("net", _F, ""), ("cnps_salarie", _F, ""),
        ("cnps_employeur", _F, ""), ("its", _F, ""), ("charges_patronales", _F, ""), ("statut", _V, "")],
    "stocks": [
        ("src", _V, ""), ("ligne", _I, ""), ("date_inventaire", _D, ""), ("reference", _V, ""), ("designation", _V, ""),
        ("famille", _V, ""), ("emplacement", _V, ""), ("qte", _F, ""), ("pu", _F, ""), ("valeur", _F, "")],
    "emprunts": [
        ("src", _V, ""), ("ligne", _I, ""), ("date", _D, ""), ("capital", _F, ""), ("capital_restant", _F, ""),
        ("interets", _F, ""), ("mensualite", _F, "")],
    "balance": [
        ("src", _V, ""), ("ligne", _I, ""), ("exercice", _V, "« 2025 »"), ("compte", _V, "n° de compte (texte)"),
        ("intitule", _V, ""), ("debit", _F, ""), ("credit", _F, ""), ("solde", _F, "débit − crédit si absent")],
    "etats_financiers": [
        ("src", _V, ""), ("ligne", _I, "n° Excel, ou n° de page pour un PDF"), ("exercice", _V, ""),
        ("poste", _V, "libellé du poste (identifiant normalisé pour un PDF)"), ("valeur_n", _F, ""), ("valeur_n1", _F, "")],
}
_NATURE_VUE = {"ventes": "ventes", "achats": "achats", "depenses": "depenses", "banque": "tresorerie",
               "caisse": "tresorerie", "mobile_money": "tresorerie", "paie": "paie", "stock": "stocks",
               "emprunt": "emprunts", "balance": "balance", "bilan": "etats_financiers",
               "compte_resultat": "etats_financiers"}
_ROLES_MONTANT = ("montant", "montant_ht", "montant_ttc", "debit", "entree", "credit", "sortie", "brut", "net",
                  "valeur_stock", "mensualite", "valeur_n")
_ROLES_TEXTE = ("piece", "tiers", "libelle", "designation", "reference", "intitule", "poste", "salarie", "matricule")
# Natures « comptables » : « report à nouveau », « solde initial »… y sont des comptes / postes, pas des totaux
NATURES_ETATS = ("balance", "grand_livre", "bilan", "compte_resultat")
_MOT_TOTAL = re.compile(r"^[\W_]*(sous[- ]?totaux|sous[- ]?total|totaux|total|cumuls?|cumule)\b")
_MOT_REPORT = re.compile(r"^[\W_]*(report|a nouveau|solde initial|solde de depart)\b")
_TOTAL_FIN = re.compile(r"\b(sous[- ]?total|total|totaux)\s*:?\s*$")


# ====================================================================== utilitaires
@lru_cache(maxsize=200_000)
def normaliser_texte(s) -> str:
    """`lecture.normaliser_texte` mise en cache (libellés et tiers très répétés)."""
    return _normaliser(s)


def _texte(v) -> str | None:
    """Cellule -> texte (None si vide) ; 1023.0 -> « 1023 », dates ISO."""
    if v is None:
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    return s or None


def _periode_mois(v) -> date | None:
    """Mois de paie : date complète, « 03/2025 », « 2025-03 », « mars 2025 » -> 1er jour du mois."""
    d = to_date(v, serie=True)
    if d is not None:
        return d.replace(day=1)
    s = normaliser_texte(_texte(v))
    annee = mois = None
    if m := re.fullmatch(r"(\d{1,2})[/.\- ](\d{4})", s):
        mois, annee = int(m[1]), int(m[2])
    elif m := re.fullmatch(r"(\d{4})[/.\- ](\d{1,2})", s):
        annee, mois = int(m[1]), int(m[2])
    elif m := re.fullmatch(r"([a-z]+)\.?[\s\-/]+(\d{2}|\d{4})", s):
        mois = next((n for k, n in _MOIS.items() if m[1].startswith(k)), None)
        annee = int(m[2]) + (2000 if len(m[2]) == 2 else 0)
    if mois is None or not 1 <= mois <= 12:
        return None
    return date(annee, mois, 1)


def _iso(d: str | None) -> date | None:
    return to_date(d) if d else None


def _valeur_sortie(v):
    """Valeur de résultat sérialisable (dates ISO, décimaux en float)."""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, timedelta):
        return v.total_seconds()
    return v


class _Lecteur:
    """Accès par rôle aux cellules d'une ligne, selon la carte d'un tableau."""

    def __init__(self, tc: TableauCarte):
        self.tc = tc
        self.col: dict[str, int] = {}
        self.colonne: dict[str, object] = {}
        for c in tc.colonnes:
            if c.role != "ignorer" and c.role not in self.col:
                self.col[c.role] = c.index
                self.colonne[c.role] = c

    def a(self, *roles: str) -> bool:
        return any(r in self.col for r in roles)

    def brut(self, row: list, role: str):
        j = self.col.get(role)
        return row[j] if j is not None and j < len(row) else None

    def txt(self, row: list, *roles: str) -> str | None:
        for r in roles:
            v = _texte(self.brut(row, r))
            if v is not None:
                return v
        return None

    def nb(self, row: list, *roles: str) -> float | None:
        for r in roles:
            v = num(self.brut(row, r))
            if v is not None:
                return v
        return None

    def date(self, row: list, *roles: str) -> date | None:
        for r in roles:
            v = to_date(self.brut(row, r), serie=True)
            if v is not None:
                return v
        return None


# ====================================================================== entrepôt
class Entrepot:
    """Base DuckDB en mémoire d'un dossier (tables brutes + vues normalisées) et journal des requêtes."""

    def __init__(self, dossier: Dossier | None, tableaux: dict[str, Tableau], carte: Carte | None):
        self.dossier = dossier
        self.carte = carte
        self.formulaire = (dossier.formulaire if dossier is not None else None) or {}
        self.requetes: dict[str, Requete] = {}
        self.pseudonymes: dict[str, dict[str, str]] = {r: {} for r in ROLES_PERSONNELS}
        self._pseudo_norm: dict[tuple[str, str], str] = {}
        self._compteurs: dict[str, int] = {}
        self._notes_ttc: set[str] = set()
        self._motifs = sorted(((normaliser_texte(c.motif), c) for c in (carte.categories_libelles if carte else [])
                               if normaliser_texte(c.motif)), key=lambda x: -len(x[0]))
        self.lignes_retirees: dict[str, dict[str, list[int]]] = {}
        self.anomalies: list[dict] = []
        self.notes: list[str] = []
        self._cartes = {t.id: t for t in (carte.tableaux if carte is not None else [])}
        # tableau « sans en-tête » selon la carte : la ligne prise pour en-tête par la lecture redevient une donnée
        self.tableaux = {tid: self._entete_en_donnee(tid, t) if getattr(self._cartes.get(tid), "sans_entete", False)
                         else t for tid, t in tableaux.items()}
        tableaux = self.tableaux
        self._verrou = threading.Lock()
        self.con = duckdb.connect(":memory:", config={"enable_external_access": False})
        lignes_vues: dict[str, list[dict]] = {v: [] for v in VUES}
        for tid, t in tableaux.items():
            tc = self._cartes.get(tid)
            gardees = self._charger_brut(tid, t, tc)
            vue = _NATURE_VUE.get(tc.nature) if tc is not None else None
            if vue:
                lignes_vues[vue].extend(getattr(self, f"_lignes_{vue}")(tid, t, tc, gardees))
        _marquer_doublons(lignes_vues["ventes"], ("date", "piece", "tiers", "designation", "montant", "site"))
        _marquer_doublons(lignes_vues["achats"], ("date", "piece", "tiers", "designation", "montant"))
        _marquer_doublons(lignes_vues["depenses"], ("date", "tiers", "libelle", "montant", "site"), cle_id=("tiers", "libelle"))
        self._virements(lignes_vues["tresorerie"])
        for vue, lignes in lignes_vues.items():
            self._charger(f"n_{vue}", VUES[vue], lignes)
            self.con.execute(f"CREATE VIEW {vue} AS SELECT * FROM n_{vue}")
        self.con.execute("SET lock_configuration=true")

    def _entete_en_donnee(self, tid: str, t: Tableau) -> Tableau:
        """Copie de `t` dont l'en-tête lu (s'il y en a un) est réinséré comme 1re ligne de données.
        N° Excel : `ligne_entete` du profil si connu, sinon la ligne précédant la 1re donnée."""
        bruts = t.entetes_bruts or []
        largeur = max([len(bruts)] + [len(r) for r in t.lignes])
        generiques = [f"col_{j + 1}" for j in range(largeur)]
        if not any(b and b != f"col_{j + 1}" for j, b in enumerate(bruts)):
            return replace(t, entetes=generiques, entetes_bruts=list(generiques))
        ligne = [None if not b or b == f"col_{j + 1}" else b for j, b in enumerate(bruts)]
        profil = next((p for p in (self.dossier.profils if self.dossier else []) if p.id == tid), None)
        n = profil.ligne_entete if profil is not None and profil.ligne_entete else None
        if n is None or n in t.numeros:
            n = max(1, (t.numeros[0] - 1) if t.numeros else 1)
            while n in t.numeros and n > 1:
                n -= 1
        self.notes.append(f"{tid} : sans en-tête selon la carte, ligne {n} réintégrée comme donnée")
        return replace(t, entetes=generiques, entetes_bruts=list(generiques), lignes=[ligne] + list(t.lignes),
                       numeros=[n] + list(t.numeros))

    # ------------------------------------------------------------------ pseudonymisation
    def pseudonymiser(self, valeur, role: str = "tiers") -> str | None:
        """Pseudonyme stable (« Tiers_001 », « Salarie_003 »…) ; même valeur normalisée -> même pseudonyme.
        Sans rôle explicite, une valeur déjà connue sous un autre rôle garde son pseudonyme."""
        s = _texte(valeur)
        if s is None:
            return None
        role = role if role in ROLES_PERSONNELS else "tiers"
        connu = self.pseudonymes.get(role, {}).get(s)
        if connu is not None:
            return connu
        cle = normaliser_texte(s)
        pseudo = self._pseudo_norm.get((role, cle))
        if pseudo is None and role == "tiers":
            pseudo = next((p for (r, k), p in self._pseudo_norm.items() if k == cle), None)
        if pseudo is None:
            self._compteurs[role] = self._compteurs.get(role, 0) + 1
            pseudo = f"{ROLES_PERSONNELS[role]}_{self._compteurs[role]:03d}"
            self._pseudo_norm[(role, cle)] = pseudo
        self.pseudonymes.setdefault(role, {})[s] = pseudo
        return pseudo

    # ------------------------------------------------------------------ chargement
    def _charger(self, nom: str, schema: list[tuple[str, pa.DataType, str]], lignes: list[dict]) -> None:
        """Crée la table typée `nom` à partir de dicts (colonnes absentes = NULL), via pyarrow."""
        cols = {c: pa.array([l.get(c) for l in lignes], type=t) for c, t, _ in schema}
        table = pa.table(cols)
        nom_tmp = f"_arrow_{nom}"
        self.con.register(nom_tmp, table)
        try:
            self.con.execute(f'CREATE TABLE "{nom}" AS SELECT * FROM "{nom_tmp}"')
        finally:
            self.con.unregister(nom_tmp)

    def _charger_brut(self, tid: str, t: Tableau, tc: TableauCarte | None) -> list[tuple[int, list]]:
        """Table `brut_<id>` (toutes lignes, VARCHAR) ; retourne les lignes gardées [(n° Excel, cellules)]."""
        exclues = set()
        if tc is not None:
            ex = tc.lignes_a_exclure
            exclues = set(ex.totaux) | set(ex.titres) | set(ex.autres)
        restantes = [(n, r) for n, r in zip(t.numeros, t.lignes) if n not in exclues]
        totaux = set(_detecter_totaux(restantes, tc)) if tc is not None else set()
        self.lignes_retirees[tid] = {"carte": sorted(exclues & set(t.numeros)), "totaux": sorted(totaux)}
        roles_perso = {c.index: c.role for c in (tc.colonnes if tc else []) if c.role in ROLES_PERSONNELS}
        largeur = max([len(t.entetes)] + [len(r) for r in t.lignes]) if t.lignes or t.entetes else 0
        colonnes: dict[str, list] = {"_ligne": [], "_retrait": []}
        for j in range(largeur):
            colonnes[f"c{j}"] = []
        for n, r in zip(t.numeros, t.lignes):
            colonnes["_ligne"].append(n)
            colonnes["_retrait"].append("exclue_carte" if n in exclues else "total_detecte" if n in totaux else None)
            for j in range(largeur):
                v = r[j] if j < len(r) else None
                colonnes[f"c{j}"].append(self.pseudonymiser(v, roles_perso[j]) if j in roles_perso else _texte(v))
        schema = [("_ligne", _I, "")] + [(f"c{j}", _V, "") for j in range(largeur)] + [("_retrait", _V, "")]
        lignes = [dict(zip(colonnes, vals)) for vals in zip(*colonnes.values())]
        self._charger(_nom_brut(tid), schema, lignes)
        return [(n, r) for n, r in restantes if n not in totaux]

    # ------------------------------------------------------------------ catégories de libellés
    def _categorie(self, tid: str, *textes) -> str | None:
        """Catégorie de la carte dont le motif normalisé figure dans un des textes (motif le plus long d'abord)."""
        if not self._motifs:
            return None
        cible = " | ".join(normaliser_texte(x) for x in textes if x)
        if not cible:
            return None
        for motif, c in self._motifs:
            if (not c.tableaux or tid in c.tableaux) and motif in cible:
                return c.categorie
        return None

    def _assujetti(self) -> bool:
        """TTC -> HT seulement pour une entreprise assujettie : formulaire non exonéré et (champ explicite
        `tva_assujetti`, sinon régime réel REEL_SIMPLIFIE / REEL_NORMAL) ; à défaut d'indication du formulaire,
        `carte.entreprise.tva_assujetti` si la carte le tient de la fiche ou du formulaire."""
        f = self.formulaire

        def bool_(x) -> bool | None:
            s = str(x).strip().lower() if x is not None else ""
            return True if x is True or s in ("true", "1", "oui", "yes") else \
                False if x is False or s in ("false", "0", "non", "no") else None
        if bool_(f.get("tva_exoneree")):
            return False
        explicite = bool_(f.get("tva_assujetti"))
        if explicite is not None:
            return explicite
        regime = str(f.get("regime_fiscal") or f.get("regime_fiscal_declare") or "").strip().upper()
        if regime.startswith("REEL"):
            return True
        if regime in ("TEE", "RME"):
            return False
        ent = self.carte.entreprise if self.carte is not None else None
        return bool(ent is not None and ent.tva_assujetti and ent.source in ("fiche", "formulaire"))

    def _montant_ht(self, tid: str, lc: _Lecteur, row: list) -> float | None:
        """Montant HT si connu ; sinon montant tel que fourni (TTC converti seulement pour un assujetti)."""
        m = lc.nb(row, "montant_ht")
        if m is not None:
            return m
        ttc = None
        m = lc.nb(row, "montant")
        if m is not None:
            if getattr(lc.colonne.get("montant"), "ht_ttc", "") != "TTC":
                return m
            ttc = m
        else:
            ttc = lc.nb(row, "montant_ttc")
        if ttc is None:
            return None
        if self._assujetti():
            tva = lc.nb(row, "tva")
            if tid not in self._notes_ttc:
                self._notes_ttc.add(tid)
                self.notes.append(f"{tid} : montants TTC convertis en HT ("
                                  + ("colonne TVA déduite" if tva is not None else f"÷ {1 + TAUX_TVA:.2f}") + ")")
            return ttc - tva if tva is not None else ttc / (1 + TAUX_TVA)
        return ttc

    # ------------------------------------------------------------------ vues
    def _base(self, tid: str, n: int) -> dict:
        return {"src": tid, "ligne": n}

    def _perso(self, lc: _Lecteur, row: list, role: str) -> str | None:
        return self.pseudonymiser(lc.brut(row, role), role) if lc.a(role) else None

    def _lignes_ventes(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        taux = lc.a("remise_taux") and not lc.a("remise")
        out, date_prec, piece_prec = [], None, None
        for n, row in gardees:
            d = lc.date(row, "date")
            piece = lc.txt(row, "piece")
            if d is None and piece and piece == piece_prec:
                d = date_prec          # ligne de détail d'une même pièce
            if d is not None:
                date_prec, piece_prec = d, piece
            qte, pu = lc.nb(row, "qte"), lc.nb(row, "pu")
            brut = lc.nb(row, "montant_brut")
            if brut is None and qte is not None and pu is not None:
                brut = qte * pu
            # remise toujours positive (un export peut l'afficher en négatif) ; « 10 % » = taux
            if taux:
                remise = _taux(lc.brut(row, "remise_taux"))
            else:
                v = lc.brut(row, "remise")
                if isinstance(v, str) and "%" in v:      # taux saisi dans une colonne de remise en montant
                    t_ = _taux(v)
                    remise = abs(brut) * t_ if t_ is not None and brut is not None else None
                else:
                    remise = abs(r) if (r := num(v)) is not None else None
            montant = self._montant_ht(tid, lc, row)
            if montant is None and qte is not None and pu is not None:
                montant = qte * pu * (1 - (remise or 0)) if taux else qte * pu - (remise or 0)
            designation = lc.txt(row, "designation", "libelle")
            texte_retour = normaliser_texte(" ".join(x for x in (designation, lc.txt(row, "type_operation"),
                                                                 lc.txt(row, "libelle"), lc.txt(row, "statut")) if x))
            est_retour = (montant is not None and montant < 0) or bool(_RETOUR.search(texte_retour))
            l = self._base(tid, n) | {
                "date": d, "piece": piece, "tiers": self._perso(lc, row, "tiers"),
                "site": lc.txt(row, "site") or tc.perimetre.site, "canal": lc.txt(row, "canal") or tc.perimetre.canal,
                "vendeur": self._perso(lc, row, "vendeur"), "designation": designation,
                "reference": lc.txt(row, "reference"), "famille": lc.txt(row, "famille"),
                "qte": abs(qte) if qte is not None else None, "pu": abs(pu) if pu is not None else None,
                "remise": remise, "montant_brut": abs(brut) if brut is not None else None,
                "montant": abs(montant) if montant is not None else None,
                "mode_paiement": lc.txt(row, "mode_paiement"), "est_retour": est_retour}
            out.append(l)
        return out

    def _lignes_achats(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        out = []
        for n, row in gardees:
            qte, pu = lc.nb(row, "qte"), lc.nb(row, "pu")
            montant = self._montant_ht(tid, lc, row)
            if montant is None:
                montant = lc.nb(row, "debit", "sortie")
            if montant is None and qte is not None and pu is not None:
                montant = qte * pu
            out.append(self._base(tid, n) | {
                "date": lc.date(row, "date"), "piece": lc.txt(row, "piece"), "tiers": self._perso(lc, row, "tiers"),
                "designation": lc.txt(row, "designation", "libelle"), "reference": lc.txt(row, "reference"),
                "famille": lc.txt(row, "famille"), "qte": qte, "pu": pu, "montant": montant,
                "categorie": lc.txt(row, "categorie")})
        return out

    def _lignes_depenses(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        out = []
        for n, row in gardees:
            montant = self._montant_ht(tid, lc, row)
            if montant is None:
                montant = lc.nb(row, "debit", "sortie")
            libelle = lc.txt(row, "libelle", "designation")
            categorie = lc.txt(row, "categorie")
            out.append(self._base(tid, n) | {
                "date": lc.date(row, "date"), "categorie": categorie, "libelle": libelle,
                "tiers": self._perso(lc, row, "tiers"), "site": lc.txt(row, "site") or tc.perimetre.site,
                "montant": montant, "mode_paiement": lc.txt(row, "mode_paiement"),
                "categorie_libelle": self._categorie(tid, libelle, lc.txt(row, "tiers"), categorie)})
        return out

    def _lignes_tresorerie(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        compte_defaut = tc.perimetre.compte or f"{tc.nature}:{t.fichier}›{t.feuille}"
        # Sens débit/crédit : relevé bancaire / mobile money (débit = sortie) ; journal de caisse (débit = entrée) ;
        # la carte tranche via `signe` (positif_entree / positif_sortie).
        debit_entree = tc.nature == "caisse"
        c_debit = lc.colonne.get("debit")
        if c_debit is not None and c_debit.signe in ("positif_entree", "positif_sortie"):
            debit_entree = c_debit.signe == "positif_entree"
        c_credit = lc.colonne.get("credit")
        if c_debit is None and c_credit is not None and c_credit.signe in ("positif_entree", "positif_sortie"):
            debit_entree = c_credit.signe == "positif_sortie"
        signe_montant = getattr(lc.colonne.get("montant"), "signe", "sans_objet")
        out = []
        for n, row in gardees:
            entree = sortie = None
            if lc.a("entree", "sortie"):
                entree, sortie = _abs(lc.nb(row, "entree")), _abs(lc.nb(row, "sortie"))
            elif lc.a("debit", "credit"):
                # deux colonnes : le signe n'est qu'un affichage (débit « -200 000 ») -> valeur absolue
                deb, cre = _abs(lc.nb(row, "debit")), _abs(lc.nb(row, "credit"))
                entree, sortie = (deb, cre) if debit_entree else (cre, deb)
            elif lc.a("montant", "montant_ttc", "montant_ht"):
                m = lc.nb(row, "montant", "montant_ttc", "montant_ht")
                if m is not None:
                    if signe_montant == "positif_sortie":
                        m = -m
                    entree, sortie = (m, None) if m >= 0 else (None, -m)
            libelle = lc.txt(row, "libelle", "designation")
            type_op = lc.txt(row, "type_operation")
            out.append(self._base(tid, n) | {
                "compte": lc.txt(row, "compte_tresorerie") or compte_defaut, "nature_compte": tc.nature,
                "date": lc.date(row, "date", "date_valeur"), "libelle": libelle,
                "tiers": self._perso(lc, row, "tiers"), "entree": entree, "sortie": sortie,
                "solde": lc.nb(row, "solde"), "frais": lc.nb(row, "frais"), "type_operation": type_op,
                "categorie_libelle": self._categorie(tid, libelle, lc.txt(row, "tiers"), type_op)})
        return out

    def _lignes_paie(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        defaut = _periode_mois(tc.periode.debut) if tc.periode.debut else None
        out = []
        for n, row in gardees:
            periode = None
            for r in ("periode", "date"):
                periode = periode or _periode_mois(lc.brut(row, r))
            out.append(self._base(tid, n) | {
                "periode": periode or defaut, "matricule": self._perso(lc, row, "matricule"),
                "salarie": self._perso(lc, row, "salarie"), "site": lc.txt(row, "site") or tc.perimetre.site,
                "brut": lc.nb(row, "brut"), "net": lc.nb(row, "net"), "cnps_salarie": lc.nb(row, "cnps_salarie"),
                "cnps_employeur": lc.nb(row, "cnps_employeur"), "its": lc.nb(row, "its"),
                "charges_patronales": lc.nb(row, "charges_patronales"), "statut": lc.txt(row, "statut")})
        return out

    def _lignes_stocks(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        defaut = _iso(tc.date_inventaire) or _iso(tc.periode.fin)
        out = []
        for n, row in gardees:
            qte, pu = lc.nb(row, "qte_comptee", "qte", "qte_theorique"), lc.nb(row, "pu")
            valeur = lc.nb(row, "valeur_stock", "montant")
            if valeur is None and qte is not None and pu is not None:
                valeur = qte * pu
            out.append(self._base(tid, n) | {
                "date_inventaire": lc.date(row, "date") or defaut, "reference": lc.txt(row, "reference"),
                "designation": lc.txt(row, "designation", "libelle"), "famille": lc.txt(row, "famille"),
                "emplacement": lc.txt(row, "emplacement") or tc.perimetre.site, "qte": qte, "pu": pu, "valeur": valeur})
        return out

    def _lignes_emprunts(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        return [self._base(tid, n) | {
            "date": lc.date(row, "date", "echeance"), "capital": lc.nb(row, "capital"),
            "capital_restant": lc.nb(row, "capital_restant", "reste_a_payer"), "interets": lc.nb(row, "interets"),
            "mensualite": lc.nb(row, "mensualite", "montant")} for n, row in gardees]

    def _exercice(self, tc: TableauCarte) -> str | None:
        if tc.exercice:
            return str(tc.exercice).strip()
        d = _iso(tc.periode.fin) or _iso(tc.periode.debut)
        return str(d.year) if d else None

    def _lignes_balance(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        exercice = self._exercice(tc)
        out = []
        for n, row in gardees:
            deb, cre, solde = lc.nb(row, "debit"), lc.nb(row, "credit"), lc.nb(row, "solde")
            if solde is None and (deb is not None or cre is not None):
                solde = (deb or 0) - (cre or 0)
            compte = lc.txt(row, "compte")
            out.append(self._base(tid, n) | {
                "exercice": exercice, "compte": re.sub(r"\s+", "", compte) if compte else None,
                "intitule": lc.txt(row, "intitule", "libelle"), "debit": deb, "credit": cre, "solde": solde})
        return out

    def _lignes_etats_financiers(self, tid, t, tc, gardees) -> list[dict]:
        lc = _Lecteur(tc)
        exercice = self._exercice(tc)
        return [self._base(tid, n) | {
            "exercice": exercice, "poste": lc.txt(row, "poste", "intitule", "libelle"),
            "valeur_n": lc.nb(row, "valeur_n", "montant", "solde"), "valeur_n1": lc.nb(row, "valeur_n1")}
            for n, row in gardees]

    # ------------------------------------------------------------------ virements internes (P0-4)
    def _virements(self, lignes: list[dict]) -> None:
        """Marque `virement_interne` : candidat (catégorie ou relation de la carte) apparié à une contrepartie
        (sortie d'un compte A, entrée d'un compte B ≠ A, même montant à 1 F près, ±3 jours). Un candidat par
        catégorie sans contrepartie reste marqué et est signalé dans `anomalies`."""
        groupes: list[set[str]] = [set(r.tableaux) for r in (self.carte.relations if self.carte else [])
                                   if r.type == "virement_interne"]
        for l in lignes:
            l["virement_interne"] = False

        def lies(a: dict, b: dict) -> bool:
            if a["categorie_libelle"] == "virement_interne" or b["categorie_libelle"] == "virement_interne":
                return True
            return any(a["src"] in g and b["src"] in g for g in groupes)

        # contreparties possibles : toute entrée datée (la contrepartie peut ne pas être catégorisée)
        entrees: dict[int, list[dict]] = {}
        for l in lignes:
            if l["entree"] and l["date"] is not None:
                entrees.setdefault(round(l["entree"]), []).append(l)
        apparies: set[int] = set()
        sorties = sorted((l for l in lignes if l["sortie"] and l["date"] is not None), key=lambda l: (l["date"], l["src"], l["ligne"]))
        for s in sorties:
            cible = round(s["sortie"])
            meilleur = None
            for k in (cible - 1, cible, cible + 1):
                for e in entrees.get(k, []):
                    if id(e) in apparies or e["compte"] == s["compte"] or not lies(s, e):
                        continue
                    if abs(e["entree"] - s["sortie"]) > TOLERANCE_VIREMENT:
                        continue
                    ecart = abs((e["date"] - s["date"]).days)
                    if ecart <= FENETRE_VIREMENT and (meilleur is None or ecart < meilleur[0]):
                        meilleur = (ecart, e)
            if meilleur is not None:
                e = meilleur[1]
                apparies.update((id(e), id(s)))
                e["virement_interne"] = s["virement_interne"] = True
        for l in lignes:
            if l["categorie_libelle"] == "virement_interne" and not l["virement_interne"]:
                l["virement_interne"] = True
                self.anomalies.append({"type": "virement_sans_contrepartie", "src": l["src"], "ligne": l["ligne"],
                                       "montant": l["entree"] or l["sortie"], "compte": l["compte"],
                                       "message": "virement interne sans contrepartie trouvée (±3 jours, ±1 F)"})

    # ------------------------------------------------------------------ états financiers PDF
    def ajouter_etats_financiers(self, src: str, lignes: list[dict], exercice: str | None = None) -> int:
        """Insère dans `etats_financiers` des lignes vérifiées (sortie d'`etats_pdf.extraire_etats`) :
        {"exercice", "poste", "valeur_n", "valeur_n1", "page"} ; `ligne` = n° de page. Retourne le nombre inséré."""
        valeurs = [(src, l.get("page"), l.get("exercice") or exercice, l.get("poste"),
                    l.get("valeur_n"), l.get("valeur_n1")) for l in lignes if l.get("poste")]
        if valeurs:
            with self._verrou:
                self.con.executemany("INSERT INTO n_etats_financiers VALUES (?, ?, ?, ?, ?, ?)", valeurs)
        return len(valeurs)

    # ------------------------------------------------------------------ requêtes tracées
    def requete(self, sql: str, objectif: str, auteur: str = "code") -> Requete:
        """Exécute une requête de lecture (SELECT/WITH, une seule instruction, 10 s au plus) et la stocke
        dans `self.requetes` (id « q1 », « q2 »…). 200 lignes retournées, `nb_lignes` = total.
        Une erreur (SQL refusé ou invalide, délai dépassé) est stockée dans `erreur`, sans exception."""
        rid = f"q{len(self.requetes) + 1}"
        req = Requete(id=rid, sql=sql, objectif=objectif, tables=[], colonnes=[], lignes=[], nb_lignes=0,
                      duree_ms=0, auteur="ia" if auteur == "ia" else "code")
        self.requetes[rid] = req
        debut = time.perf_counter()
        with self._verrou:
            try:
                instructions = self.con.extract_statements(sql)
                if len(instructions) != 1:
                    raise ValueError("une seule instruction SQL est autorisée")
                if instructions[0].type != duckdb.StatementType.SELECT or not re.match(
                        r"^\s*(\(\s*)*(select|with)\b", _sans_commentaires(sql), re.I):
                    raise ValueError("seules les requêtes SELECT / WITH sont autorisées")
                # les vues sont résolues en tables `n_<vue>` : on rapporte le nom de la vue
                req.tables = sorted({t[2:] if t.startswith("n_") and t[2:] in VUES else t
                                     for t in self.con.get_table_names(sql, qualified=False)})
            except (duckdb.Error, ValueError) as e:
                req.erreur = str(e)
                req.duree_ms = int((time.perf_counter() - debut) * 1000)
                return req
            minuteur = threading.Timer(DELAI_S, self.con.interrupt)
            minuteur.start()
            try:
                res = self.con.execute(sql)
                req.colonnes = [d[0] for d in res.description or []]
                premieres = res.fetchmany(MAX_LIGNES_RESULTAT)
                total = len(premieres)
                while True:
                    bloc = res.fetchmany(10_000)
                    if not bloc:
                        break
                    total += len(bloc)
                req.lignes = [[_valeur_sortie(v) for v in r] for r in premieres]
                req.nb_lignes = total
            except duckdb.InterruptException:
                req.erreur = f"délai dépassé ({DELAI_S:g} s)"
            except duckdb.Error as e:
                req.erreur = str(e)
            finally:
                minuteur.cancel()
        req.duree_ms = int((time.perf_counter() - debut) * 1000)
        return req

    # ------------------------------------------------------------------ description
    def schema_vues(self) -> str:
        """Description texte des vues normalisées (colonnes, types, nombre de lignes) et des tables brutes."""
        out = ["Vues normalisées (DuckDB ; montants en FCFA ; `src` = id du tableau, `ligne` = n° Excel ;"
               " personnes pseudonymisées) :"]
        for vue, cols in VUES.items():
            n = self.con.execute(f"SELECT count(*) FROM {vue}").fetchone()[0]
            out.append(f"- {vue} ({n} lignes)")
            for c, t, desc in cols:
                out.append(f"    {c} {_SQL[t]}" + (f" — {desc}" if desc else ""))
        out.append("Tables brutes (VARCHAR, cN = colonne d'index N ; _ligne = n° Excel ;"
                   " _retrait NULL = ligne gardée, sinon exclue_carte | total_detecte) :")
        for tid, t in self.tableaux.items():
            tc = self._cartes.get(tid)
            nature = tc.nature if tc else "non cartographié"
            if tc is not None and tc.sans_entete:
                # pas d'en-tête : les rôles de la carte (jamais les cellules, qui sont des données)
                roles = {c.index: c.role for c in tc.colonnes}
                largeur = max([len(t.entetes)] + [len(r) for r in t.lignes[:1]])
                entetes = ", ".join(f"c{j}={roles.get(j, f'col_{j + 1}')}" for j in range(largeur))
            else:
                entetes = ", ".join(f"c{j}={e}" for j, e in enumerate(t.entetes_bruts or t.entetes))
            out.append(f"- {_nom_brut(tid)} [{nature}] {t.fichier} › {t.feuille} : {entetes}")
        return "\n".join(out)

    def fermer(self) -> None:
        """Ferme la connexion DuckDB."""
        self.con.close()


# ====================================================================== fonctions internes
def _nom_brut(tid: str) -> str:
    return "brut_" + re.sub(r"[^a-z0-9_]", "_", tid.lower())


def _sans_commentaires(sql: str) -> str:
    return re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S)


def _taux(v) -> float | None:
    """Taux de remise 0..1 depuis 0,1 · 10 · « 10 % » · « 5% » ; toujours positif."""
    if isinstance(v, str) and "%" in v:
        x = num(v.replace("%", ""))
        return abs(x) / 100.0 if x is not None else None
    x = num(v)
    if x is None:
        return None
    x = abs(x)
    return x / 100.0 if x > 1 else x


def _abs(x: float | None) -> float | None:
    return abs(x) if x is not None else None


def _mot_total(row: list, reports: bool) -> bool:
    """Une cellule texte commence par un mot de total (ou finit par « total ») ; `reports` : « report »,
    « à nouveau », « solde initial »… comptent aussi (journaux, pas balances ni états financiers)."""
    for v in row:
        if isinstance(v, str):
            t = normaliser_texte(v)
            if _MOT_TOTAL.search(t) or _TOTAL_FIN.search(t) or (reports and _MOT_REPORT.search(t)):
                return True
    return False


def _detecter_totaux(lignes: list[tuple[int, list]], tc: TableauCarte) -> list[int]:
    """N° Excel des lignes de total.
    - Mot de total (« total », « sous-total », « cumul »… ; « report », « à nouveau » dans les journaux seulement)
      sur une ligne sans date, ni propre ni reprise de sa pièce (ligne de détail d'une facture : pas un total).
    - Ligne datée (ou de détail) avec mot de total : total seulement si son montant égale la somme du bloc.
    - Sans mot : montant égal (à 1 F près) à la somme d'au moins 2 lignes depuis le total précédent (ou depuis le
      début), sur une ligne sans texte identifiant (pièce, libellé, tiers…) et sans date nouvelle.
    Balance / états financiers : une ligne qui porte un n° de compte n'est jamais un total par son libellé."""
    lc = _Lecteur(tc)
    col_date = lc.col.get("date")
    col_piece = lc.col.get("piece")
    col_compte = lc.col.get("compte") if tc.nature in NATURES_ETATS else None
    reports = tc.nature not in NATURES_ETATS
    cols_m = [lc.col[r] for r in _ROLES_MONTANT if r in lc.col][:3]
    cols_lib = [lc.col[r] for r in _ROLES_TEXTE if r in lc.col]
    blocs = {j: [0.0, 0, 0.0, 0] for j in cols_m}   # somme bloc, n bloc, somme tout, n tout
    retires, date_prec, piece_prec = [], None, None

    def cell(row, j):
        return row[j] if j is not None and j < len(row) else None

    def egal_somme(row) -> bool:
        for j in cols_m:
            m = num(cell(row, j))
            sb, nb, st, nt = blocs[j]
            if m is not None and ((nb >= 2 and abs(m - sb) <= 1) or (nt >= 2 and abs(m - st) <= 1)):
                return True
        return False

    for n, row in lignes:
        d = to_date(cell(row, col_date), serie=True) if col_date is not None else None
        piece = _texte(cell(row, col_piece))
        d_eff = d if d is not None else (date_prec if piece and piece == piece_prec else None)
        mot = _mot_total(row, reports)
        if mot and col_compte is not None and re.search(r"\d", _texte(cell(row, col_compte)) or ""):
            mot = False
        if mot:
            est_total = d_eff is None or egal_somme(row)
        else:
            est_total = False
            if cols_m:
                libre = all(_texte(cell(row, j)) is None for j in cols_lib) if cols_lib else \
                    not any(isinstance(v, str) and num(v) is None and to_date(v) is None for v in row)
                est_total = libre and (d is None or d == date_prec) and egal_somme(row)
        if est_total:
            retires.append(n)
            for j in cols_m:
                blocs[j][0], blocs[j][1] = 0.0, 0
            continue
        for j in cols_m:
            m = num(cell(row, j))
            if m is not None:
                blocs[j][0] += m
                blocs[j][1] += 1
                blocs[j][2] += m
                blocs[j][3] += 1
        if d is not None:
            date_prec, piece_prec = d, piece
    return retires


def _marquer_doublons(lignes: list[dict], cle: tuple[str, ...], cle_id: tuple[str, ...] = ("piece", "tiers")) -> None:
    """`doublon` = même clé déjà vue (toutes sources de la vue) ; la 1re occurrence reste comptée.
    Seulement si la ligne porte un élément identifiant (`cle_id` : pièce ou tiers) : sans lui, deux ventes
    identiques le même jour sont plausibles et ne sont pas marquées."""
    vues: set[tuple] = set()
    for l in lignes:
        l["doublon"] = False
        if l.get("montant") is None or not any(l.get(c) for c in cle_id):
            continue
        k = tuple(normaliser_texte(l.get(c)) if isinstance(l.get(c), str) else
                  (round(l[c], 2) if isinstance(l.get(c), float) else l.get(c)) for c in cle) + (l.get("est_retour"),)
        if k in vues:
            l["doublon"] = True
        else:
            vues.add(k)

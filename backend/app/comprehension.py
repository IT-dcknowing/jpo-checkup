"""Compréhension des fichiers déposés, quel que soit leur type : balance, grand livre, journaux (ventes, achats, paie).

L'IA n'additionne jamais (pour ne pas inventer de chiffres) : face à un journal de milliers de lignes, elle ne trouve
aucun total tout fait. Ici, le code reconnaît le type de chaque tableau et calcule les totaux de façon exacte et
traçable. Ces « faits » sont donnés à l'IA comme repères et complètent sa sortie quand elle ne trouve rien :
le rapport est alors produit sur les seules parties disponibles (note indicative), au lieu d'un refus.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import chardet

TVA = 0.18
CONF_JOURNAL = 0.80   # total exact, mais périmètre d'un journal (avoirs, période) à confirmer
CONF_BALANCE = 0.85   # agrégation par comptes SYSCOHADA
MAX_ROWS = 200_000


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def _num(v) -> float | None:
    """Nombre depuis une cellule Excel/CSV : 1 234 567,89 · 1.234.567,89 · (1 000) · -1000 FCFA."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d,.\-]", "", s.replace(" ", "").replace(" ", ""))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) != 3 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def _date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.match(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", str(v or ""))
    if m:
        d, mth, y = (int(x) for x in m.groups())
        y += 2000 if y < 100 else 0
        try:
            return date(y, mth, d)
        except ValueError:
            return None
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    if m:
        try:
            return date(*(int(x) for x in m.groups()))
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ lecture brute des tableaux
def read_tables(path: Path) -> list[tuple[str, list[list]]]:
    """[(nom de feuille, lignes)] pour un XLSX ou un CSV ; [] pour un PDF (laissé à l'IA)."""
    ext = path.suffix.lower()
    if ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        out = [(ws.title, [list(r) for _, r in zip(range(MAX_ROWS), ws.iter_rows(values_only=True))]) for ws in wb.worksheets]
        wb.close()
        return out
    if ext == ".csv":
        raw = path.read_bytes()
        enc = chardet.detect(raw[:200_000]).get("encoding") or "utf-8"
        try:
            text = raw.decode("utf-8" if enc.lower() == "ascii" else enc)
        except (UnicodeDecodeError, LookupError):
            text = raw.decode("cp1252", errors="replace")
        sample = text[:20_000]
        delim = max(";,\t|", key=sample.count)
        return [(path.stem, [r for _, r in zip(range(MAX_ROWS), csv.reader(io.StringIO(text), delimiter=delim))])]
    return []


# ------------------------------------------------------------------ reconnaissance d'un tableau
MOTS = {
    "ventes": ["vente", "client", "facture", "recette", "chiffre d'affaires", "encaissement", "ca "],
    "achats": ["achat", "fournisseur", "approvisionnement", "facture fournisseur"],
    "paie": ["paie", "salaire", "brut", "net a payer", "cnps", "its", "matricule", "salarie", "employe", "bulletin"],
    "balance": ["balance", "solde debiteur", "solde crediteur", "intitule du compte", "n° compte", "numero de compte"],
    "grand_livre": ["grand livre", "grand-livre", "journal general", "ecriture", "piece"],
}


@dataclass
class Table:
    nom: str
    entetes: list[str]
    lignes: list[list]


def _entete(rows: list[list]) -> tuple[int, list[str]] | None:
    """Ligne d'en-tête : parmi les 25 premières, celle qui a le plus de libellés texte (au moins 2)."""
    best, idx = 0, None
    for i, r in enumerate(rows[:25]):
        n = sum(1 for c in r if isinstance(c, str) and c.strip() and _num(c) is None)
        if n > best:
            best, idx = n, i
    if idx is None or best < 2:
        return None
    return idx, [_norm(c) for c in rows[idx]]


def tables_de(nom_fichier: str, feuilles: list[tuple[str, list[list]]]) -> list[Table]:
    out = []
    for feuille, rows in feuilles:
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        h = _entete(rows)
        if not h:
            continue
        i, entetes = h
        out.append(Table(f"{nom_fichier} › {feuille}", entetes, rows[i + 1:]))
    return out


def _col(entetes: list[str], *motifs: str, sauf: tuple[str, ...] = ()) -> int | None:
    for m in motifs:
        for j, h in enumerate(entetes):
            if re.search(m, h) and not any(re.search(x, h) for x in sauf):
                return j
    return None


def _est_total(ligne: list) -> bool:
    return any(isinstance(c, str) and re.search(r"\b(sous[- ]?)?total\b|\bcumul\b|\breport\b", _norm(c)) for c in ligne)


def _somme(t: Table, j: int, garde=None) -> float:
    """Somme d'une colonne, hors lignes de total et hors lignes écartées par `garde` (filtre de période)."""
    return sum(x for ligne in t.lignes if j < len(ligne) and not _est_total(ligne) and (garde is None or garde(ligne))
               and (x := _num(ligne[j])) is not None)


def _col_date(t: Table) -> int | None:
    return _col(t.entetes, r"^date", r"date", r"^periode", r"^mois")


def _dates(t: Table) -> list[date]:
    j = _col_date(t)
    return [d for r in t.lignes if j is not None and j < len(r) and not _est_total(r) and (d := _date(r[j]))]


def choisir_exercice(dates: list[date]) -> tuple[date, date, str] | None:
    """Fenêtre à analyser quand les fichiers couvrent plus d'un exercice : dernière année civile couverte
    sur au moins 10 mois, sinon les 12 derniers mois. None si la période tient déjà en 13 mois."""
    if not dates:
        return None
    lo, hi = min(dates), max(dates)
    if (hi - lo).days <= 400:
        return None
    mois = {}
    for d in dates:
        mois.setdefault(d.year, set()).add(d.month)
    for seuil in (12, 10):  # d'abord la dernière année complète, sinon une année presque complète
        for an in sorted(mois, reverse=True):
            if len(mois[an]) >= seuil:
                return date(an, 1, 1), date(an, 12, 31), f"exercice {an}"
    debut = date(hi.year - 1, hi.month, 1) if hi.month < 12 else date(hi.year, 1, 1)
    return debut, hi, "12 derniers mois"


def _col_compte(t: Table) -> int | None:
    """Colonne des numéros de compte SYSCOHADA (majorité de codes numériques de 2 à 12 chiffres)."""
    j = _col(t.entetes, r"^(n.?|no|num(ero)?|code)?\s*(de )?compte", r"^compte$", r"^comptes?\b", sauf=(r"intitule", r"libelle"))
    cands = [j] if j is not None else range(max((len(r) for r in t.lignes[:50]), default=0))
    for c in cands:
        vals = [str(r[c]).strip().split(".")[0] for r in t.lignes[:300] if c < len(r) and r[c] not in (None, "")]
        if vals and sum(bool(re.fullmatch(r"[1-9]\d{1,11}", v)) for v in vals) / len(vals) > 0.6:
            return c
    return None


def classer(t: Table, nom_fichier: str) -> str:
    """balance | grand_livre | ventes | achats | paie | autre"""
    if _col_compte(t) is not None and (_col(t.entetes, r"debit") is not None or _col(t.entetes, r"solde") is not None):
        return "grand_livre" if _col(t.entetes, r"^date") is not None and len(t.lignes) > 0 and \
            len({str(r[_col_compte(t)]) for r in t.lignes if _col_compte(t) < len(r)}) < len(t.lignes) * 0.6 else "balance"
    contexte = _norm(nom_fichier) + " " + _norm(t.nom) + " " + " ".join(t.entetes)
    scores = {k: sum(contexte.count(m) for m in mots) for k, mots in MOTS.items() if k in ("ventes", "achats", "paie")}
    if _norm(nom_fichier).find("vente") >= 0:
        scores["ventes"] += 5
    if re.search(r"achat|fournisseur", _norm(nom_fichier)):
        scores["achats"] += 5
    if re.search(r"paie|salaire|personnel", _norm(nom_fichier)):
        scores["paie"] += 5
    meilleur = max(scores, key=scores.get)
    return meilleur if scores[meilleur] >= 2 else "autre"


# ------------------------------------------------------------------ calculs par type
@dataclass
class Fait:
    zone: str          # compte_resultat | actif | passif
    champ: str
    valeur: float
    confiance: float
    source: str


@dataclass
class Comprehension:
    faits: dict[tuple[str, str], Fait] = field(default_factory=dict)
    documents: list[str] = field(default_factory=list)   # « fichier › feuille : journal des ventes (1 234 lignes) »
    avertissements: list[str] = field(default_factory=list)
    debut: date | None = None
    fin: date | None = None

    def ajouter(self, f: Fait) -> None:
        cle = (f.zone, f.champ)
        if cle in self.faits:  # même poste dans deux fichiers : on additionne (ex. ventes sur deux feuilles mensuelles)
            ancien = self.faits[cle]
            self.faits[cle] = Fait(f.zone, f.champ, ancien.valeur + f.valeur, min(ancien.confiance, f.confiance), ancien.source + " + " + f.source)
        else:
            self.faits[cle] = f

    def periode(self, t: Table, fenetre=None) -> None:
        ds = _dates(t)
        if fenetre:
            ds = [d for d in ds if fenetre[0] <= d <= fenetre[1]]
        if ds:
            self.debut = min([d for d in (self.debut, min(ds)) if d])
            self.fin = max([d for d in (self.fin, max(ds)) if d])

    def resume_texte(self) -> str:
        if not self.documents and not self.faits:
            return ""
        lignes = ["=== SYNTHÈSE CALCULÉE PAR LE SERVEUR (totaux exacts, à reprendre tels quels) ==="]
        lignes += [f"Document : {d}" for d in self.documents]
        if self.debut and self.fin:
            lignes.append(f"Période couverte par les dates : du {self.debut.isoformat()} au {self.fin.isoformat()}")
        for f in self.faits.values():
            lignes.append(f"{f.zone}.{f.champ} = {round(f.valeur)} FCFA (source : {f.source})")
        lignes += [f"Note : {a}" for a in self.avertissements]
        return "\n".join(lignes)


def _montant_ht(t: Table, tva_exoneree: bool, garde=None) -> tuple[float, str] | None:
    """Somme hors taxes d'un journal : colonne HT, sinon TTC ramené au HT (TVA 18 %), sinon colonne montant."""
    j = _col(t.entetes, r"\bht\b", r"hors tax", r"base (imposable|ht)", sauf=(r"tva", r"ttc"))
    if j is not None:
        return _somme(t, j, garde), f"colonne « {t.entetes[j]} »"
    j = _col(t.entetes, r"\bttc\b", r"toutes taxes")
    if j is not None:
        s = _somme(t, j, garde)
        return (s, f"colonne « {t.entetes[j]} » (TVA exonérée)") if tva_exoneree else (s / (1 + TVA), f"colonne « {t.entetes[j]} » ÷ 1,18")
    j = _col(t.entetes, r"^montant", r"montant", r"^total", r"^valeur", sauf=(r"tva", r"taxe"))
    if j is not None:
        return _somme(t, j, garde), f"colonne « {t.entetes[j]} »"
    return None


def _journal(c: Comprehension, t: Table, genre: str, tva_exoneree: bool, fenetre=None) -> None:
    jd = _col_date(t)
    garde = None
    if fenetre and jd is not None:
        lo, hi = fenetre[0], fenetre[1]
        def garde(r):  # lignes datées hors exercice écartées ; lignes sans date conservées
            d = _date(r[jd]) if jd < len(r) else None
            return d is None or lo <= d <= hi
    c.periode(t, fenetre)
    if genre == "paie":
        brut = _col(t.entetes, r"salaire brut", r"\bbrut\b", r"brut imposable", sauf=(r"net",))
        patron = _col(t.entetes, r"charges? patronales?", r"part patronale", r"cotisations? employeur", r"charges? employeur")
        if brut is None:
            j = _col(t.entetes, r"net a payer", r"\bnet\b")
            if j is None:
                c.avertissements.append(f"{t.nom} : journal de paie sans colonne de salaire brut ni net")
                return
            total, src = _somme(t, j, garde), f"colonne « {t.entetes[j]} » (salaires nets : sous-estime la masse salariale)"
            conf = 0.65
        else:
            total, src, conf = _somme(t, brut, garde), f"colonne « {t.entetes[brut]} »", CONF_JOURNAL
            if patron is not None:
                total += _somme(t, patron, garde)
                src += f" + « {t.entetes[patron]} »"
            else:
                c.avertissements.append(f"{t.nom} : charges patronales absentes, masse salariale = salaires bruts seuls")
        if total > 0:
            c.ajouter(Fait("compte_resultat", "charges_personnel", total, conf, f"{t.nom}, {src}"))
        return
    m = _montant_ht(t, tva_exoneree, garde)
    if not m or m[0] <= 0:
        c.avertissements.append(f"{t.nom} : aucune colonne de montant exploitable")
        return
    total, src = m
    if genre == "ventes":
        c.ajouter(Fait("compte_resultat", "chiffre_affaires_ht", total, CONF_JOURNAL, f"{t.nom}, {src}"))
        if fenetre and fenetre[2].startswith("exercice") and jd is not None:  # année précédente : CA N-1
            an = fenetre[0].year - 1
            if len({d.month for d in _dates(t) if d.year == an}) >= 10:
                def garde_n1(r):
                    d = _date(r[jd]) if jd < len(r) else None
                    return d is not None and d.year == an
                m1 = _montant_ht(t, tva_exoneree, garde_n1)
                if m1 and m1[0] > 0:
                    c.ajouter(Fait("compte_resultat", "chiffre_affaires_n1", m1[0], CONF_JOURNAL, f"{t.nom}, {m1[1]}, année {an}"))
    else:
        c.ajouter(Fait("compte_resultat", "cout_achats_marchandises", total, CONF_JOURNAL - 0.1, f"{t.nom}, {src}"))
        c.avertissements.append(f"{t.nom} : achats comptés en marchandises (le journal ne distingue pas marchandises et matières)")


# Comptes SYSCOHADA -> postes (solde = débit - crédit)
def _prefixe(compte: str, *ps: str) -> bool:
    return any(compte.startswith(p) for p in ps)


def _balance(c: Comprehension, t: Table) -> None:
    jc = _col_compte(t)
    jd, jcr = _col(t.entetes, r"solde debit", r"debit"), _col(t.entetes, r"solde credit", r"credit")
    # préférer les colonnes de solde si elles existent
    sd, sc = _col(t.entetes, r"solde debit"), _col(t.entetes, r"solde credit")
    js = _col(t.entetes, r"^solde$", r"^solde\b", sauf=(r"debit", r"credit"))
    soldes: dict[str, float] = {}
    for r in t.lignes:
        if jc is None or jc >= len(r) or _est_total(r):
            continue
        compte = str(r[jc]).strip().split(".")[0]
        if not re.fullmatch(r"[1-9]\d{1,11}", compte):
            continue
        val = lambda j: (_num(r[j]) or 0.0) if j is not None and j < len(r) else 0.0
        if sd is not None or sc is not None:
            s = val(sd) - val(sc)
        elif js is not None:
            s = val(js)
        else:
            s = val(jd) - val(jcr)
        soldes[compte] = soldes.get(compte, 0.0) + s
    if not soldes:
        return
    tot = lambda *ps: sum(v for k, v in soldes.items() if _prefixe(k, *ps))
    deb = lambda *ps: sum(v for k, v in soldes.items() if _prefixe(k, *ps) and v > 0)
    cre = lambda *ps: -sum(v for k, v in soldes.items() if _prefixe(k, *ps) and v < 0)
    src = f"{t.nom}, agrégation par comptes"
    postes = {
        ("compte_resultat", "chiffre_affaires_ht"): -tot("70"),
        ("compte_resultat", "cout_achats_marchandises"): tot("601", "603") - tot("6032"),  # 603 : variation de stocks
        ("compte_resultat", "cout_achats_matieres"): tot("602", "6032"),
        ("compte_resultat", "charges_personnel"): tot("66"),
        ("compte_resultat", "charges_exploitation_autres"): tot("604", "605", "608", "61", "62", "63", "64", "65"),
        ("compte_resultat", "dotations_amortissements"): tot("68", "69"),
        ("compte_resultat", "charges_financieres"): tot("67"),
        ("compte_resultat", "produits_financiers"): -tot("77"),
        ("compte_resultat", "impot_benefices"): tot("89"),
        ("actif", "immobilisations_nettes"): tot("2"),
        ("actif", "stocks"): tot("3"),
        ("actif", "creances_clients"): deb("41"),
        ("actif", "tresorerie_actif"): deb("50", "51", "52", "53", "54", "57", "58"),
        ("passif", "dettes_financieres"): cre("16", "17"),
        ("passif", "dettes_fournisseurs"): cre("40"),
        ("passif", "dettes_fiscales_sociales"): cre("42", "43", "44"),
        ("passif", "tresorerie_passif"): cre("56"),
    }
    presents = {k for k in soldes}
    for (zone, champ), v in postes.items():
        if v > 0:
            c.ajouter(Fait(zone, champ, v, CONF_BALANCE, src))
    if not any(_prefixe(k, "6", "7") for k in presents):
        c.avertissements.append(f"{t.nom} : aucun compte de charges ou de produits (classes 6 et 7)")


def comprendre(fichiers: list[tuple[str, Path]], tva_exoneree: bool = False) -> Comprehension:
    """fichiers : [(nom d'origine, chemin local)]. Deux passes : reconnaissance des tableaux, puis choix de
    l'exercice (si les journaux couvrent plusieurs années) et calcul des totaux sur cet exercice."""
    c = Comprehension()
    reconnus: list[tuple[Table, str]] = []
    for nom, path in fichiers:
        try:
            tables = tables_de(nom, read_tables(path))
        except Exception as e:  # noqa: BLE001 — un fichier non tabulaire reste lisible par l'IA
            c.avertissements.append(f"{nom} : structure non analysée ({type(e).__name__})")
            continue
        for t in tables:
            genre = classer(t, nom)
            libelle = {"balance": "balance", "grand_livre": "grand livre", "ventes": "journal des ventes", "achats": "journal des achats",
                       "paie": "journal de paie", "autre": "tableau non reconnu"}[genre]
            c.documents.append(f"{t.nom} : {libelle} ({len(t.lignes)} lignes)")
            reconnus.append((t, genre))
    journaux = [(t, g) for t, g in reconnus if g in ("ventes", "achats", "paie")]
    # l'exercice se lit d'abord sur les ventes, sinon sur tous les journaux
    dates = [d for t, g in journaux if g == "ventes" for d in _dates(t)] or [d for t, _ in journaux for d in _dates(t)]
    fenetre = choisir_exercice(dates)
    if fenetre:
        c.avertissements.append(f"les journaux couvrent du {min(dates).isoformat()} au {max(dates).isoformat()} : "
                                f"{fenetre[2]} retenu ({fenetre[0].isoformat()} au {fenetre[1].isoformat()})")
    for t, genre in reconnus:
        if genre in ("balance", "grand_livre"):
            c.periode(t)
            _balance(c, t)
        elif genre in ("ventes", "achats", "paie"):
            _journal(c, t, genre, tva_exoneree, fenetre)
    return c

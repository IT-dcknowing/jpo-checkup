"""Lecture des fichiers en tableaux (P0-5) : en-tête, titre, lignes de données, n° de ligne Excel.

Règles génériques uniquement :
- un `Tableau` par onglet non vide (xlsx) ou par fichier (csv) ;
- ligne d'en-tête cherchée dans les 30 premières lignes (le plus de libellés texte, au moins 2,
  de préférence suivie de lignes de données) ; en-têtes sur deux lignes (cellules fusionnées) fusionnés ;
- lignes de total et lignes sans date traitées à part (`marquer_totaux`, `heriter_dates`).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

from .modele import Tableau

LIGNES_ENTETE = 30            # zone de recherche de la ligne d'en-tête
MAX_LIGNES = 300_000          # garde-fou par feuille
TAILLE_READ_ONLY = 15 * 1024 * 1024   # au-delà : lecture openpyxl en flux (sans info de fusion)


# ------------------------------------------------------------------ utilitaires publics
def normaliser_texte(s) -> str:
    """Minuscules, sans accents, espaces (y compris insécables) simplifiés."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.replace(" ", " ").replace(" ", " ")).strip().lower()


# Suffixes monétaires tolérés après un nombre
_DEVISE = re.compile(r"(f\s*cfa|fcfa|cfa|xof|frs?|f)\.?$", re.I)
_NOMBRE = re.compile(r"^[+-]?\(?[+-]?[\d.,]+\)?-?$")


def num(v) -> float | None:
    """Nombre depuis une cellule : 1 250 000 · 1.250.000 · 1 250 000 F · (1 000) · -1000 · 12,5. None sinon."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (date, datetime)):
        return None
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(" ", "").replace("'", "")
    s = _DEVISE.sub("", s).strip()
    if not s or not re.search(r"\d", s) or not _NOMBRE.match(s):
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = s.strip("()+-")
    if "(" in s or ")" in s or "-" in s or "+" in s:
        return None
    if "," in s and "." in s:
        # le dernier séparateur est la décimale
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif s.count(",") > 1:
        s = s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        ent, dec = s.split(sep)
        # « 1.250 » / « 1,250 » : séparateur de milliers (montants FCFA entiers) ; « 12,5 » : décimale
        s = ent + dec if (len(dec) == 3 and ent and len(ent) <= 3 and ent != "0") else f"{ent or '0'}.{dec}"
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


_MOIS = {"janv": 1, "jan": 1, "fevr": 2, "fev": 2, "feb": 2, "mars": 3, "mar": 3, "avr": 4, "apr": 4, "mai": 5, "may": 5,
         "juin": 6, "jun": 6, "juil": 7, "jul": 7, "aout": 8, "aug": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11,
         "dec": 12}
_EPOQUE_EXCEL = date(1899, 12, 30)


def _ymd(y: int, m: int, d: int) -> date | None:
    y += 2000 if y < 100 else 0
    try:
        return date(y, m, d)
    except ValueError:
        return None


def to_date(v, serie: bool = False) -> date | None:
    """Date depuis datetime, date, « 25/11/2026 », « 2026-11-25 », « 25-11-26 », « 25 nov. 2026 ».
    `serie=True` (colonne reconnue comme date) : accepte aussi un numéro de série Excel entre 30000 et 60000."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if serie and 30000 <= v <= 60000:
            return _EPOQUE_EXCEL + timedelta(days=int(v))
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})(?:$|[\sT])", s)
    if m:
        return _ymd(int(m[1]), int(m[2]), int(m[3]))
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})(?:$|\s)", s)
    if m:
        return _ymd(int(m[3]), int(m[2]), int(m[1]))
    m = re.match(r"^(\d{1,2})(?:er)?[\s-]+([a-zA-Zéèûô]+)\.?[\s-]+(\d{2}|\d{4})$", s)
    if m:
        mois = normaliser_texte(m[2])
        for k, n in _MOIS.items():
            if mois.startswith(k):
                return _ymd(int(m[3]), n, int(m[1]))
    if serie and re.fullmatch(r"\d{5}(\.\d+)?", s):
        return to_date(float(s), serie=True)
    return None


def empreinte(path) -> str:
    """SHA-256 hexadécimal du contenu du fichier (détection des fichiers identiques)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


# ------------------------------------------------------------------ classification des cellules
def _vide(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _est_nombre(v) -> bool:
    return not _vide(v) and not isinstance(v, (date, datetime)) and num(v) is not None


def _est_date(v) -> bool:
    return not _vide(v) and to_date(v) is not None


def _est_libelle(v) -> bool:
    """Texte non numérique, non date, contenant au moins une lettre."""
    return isinstance(v, str) and bool(re.search(r"[^\W\d_]", v)) and num(v) is None and to_date(v) is None


def _est_annee(v) -> bool:
    try:
        x = num(v)
    except Exception:  # noqa: BLE001
        return False
    return x is not None and x == int(x) and 1990 <= x <= 2100 and not isinstance(v, bool)


def _nb_libelles(row: list) -> int:
    """Libellés texte d'une ligne ; les années (« 2025 ») comptent si la ligne n'a pas d'autre nombre (bilans N / N-1)."""
    n = sum(_est_libelle(v) for v in row)
    autres = [v for v in row if not _vide(v) and not _est_libelle(v)]
    if n and autres and all(_est_annee(v) for v in autres):
        n += len(autres)
    return n


def _a_donnees(row: list) -> bool:
    return any(_est_nombre(v) or isinstance(v, (date, datetime)) or (isinstance(v, str) and _est_date(v)) for v in row)


# ------------------------------------------------------------------ lecture brute
def _nettoyer(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _lire_xlsx(path: Path) -> list[tuple[str, list[list], dict]]:
    """[(onglet, lignes depuis la ligne 1, fusions {(ligne0, col0): (ligne0, col0) de l'ancre})]."""
    from openpyxl import load_workbook
    read_only = path.stat().st_size > TAILLE_READ_ONLY
    wb = load_workbook(path, read_only=read_only, data_only=True)
    out = []
    try:
        for ws in wb.worksheets:
            if getattr(ws, "sheet_state", "visible") != "visible" and False:
                continue
            lignes = []
            for i, r in enumerate(ws.iter_rows(min_row=1, values_only=True)):
                if i >= MAX_LIGNES:
                    break
                lignes.append([_nettoyer(v) for v in r])
            fusions = {}
            if not read_only:
                for rg in ws.merged_cells.ranges:
                    if rg.min_row > LIGNES_ENTETE + 2:
                        continue  # seules les fusions de la zone d'en-tête servent
                    for rr in range(rg.min_row, rg.max_row + 1):
                        for cc in range(rg.min_col, rg.max_col + 1):
                            if (rr, cc) != (rg.min_row, rg.min_col):
                                fusions[(rr - 1, cc - 1)] = (rg.min_row - 1, rg.min_col - 1)
            out.append((ws.title, lignes, fusions))
    finally:
        wb.close()
    return out


def _lire_csv(path: Path) -> list[list]:
    """Lignes d'un CSV : encodage détecté (chardet), délimiteur détecté parmi ; , tabulation |."""
    import chardet
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        texte = raw[3:].decode("utf-8", errors="replace")
    else:
        try:
            texte = raw.decode("utf-8")
        except UnicodeDecodeError:
            enc = chardet.detect(raw[:200_000]).get("encoding") or "cp1252"
            try:
                texte = raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                texte = raw.decode("cp1252", errors="replace")
    echantillon = texte[:20_000]
    try:
        delim = csv.Sniffer().sniff(echantillon, delimiters=";,\t|").delimiter
    except csv.Error:
        delim = max(";,\t|", key=echantillon.count)
    lignes = []
    for i, r in enumerate(csv.reader(io.StringIO(texte, newline=""), delimiter=delim)):
        if i >= MAX_LIGNES:
            break
        lignes.append([_nettoyer(v) for v in r])
    return lignes


# ------------------------------------------------------------------ détection de l'en-tête
def _suivie_de_donnees(lignes: list[list], i: int, n: int = 3) -> bool:
    """Une des `n` lignes non vides qui suivent la ligne i contient un nombre ou une date."""
    vues = 0
    for r in lignes[i + 1:]:
        if all(_vide(v) for v in r):
            continue
        if _a_donnees(r):
            return True
        vues += 1
        if vues >= n:
            break
    return False


def _ligne_entete(lignes: list[list]) -> int | None:
    """Index (0-based) de la ligne d'en-tête : le plus de libellés (≥ 2), de préférence suivie de données."""
    meilleur, cle_max = None, None
    for i, r in enumerate(lignes[:LIGNES_ENTETE]):
        n = _nb_libelles(r)
        if n < 2:
            continue
        cle = (_suivie_de_donnees(lignes, i), n, -i)
        if cle_max is None or cle > cle_max:
            meilleur, cle_max = i, cle
    return meilleur


def _valeur_fusion(lignes, fusions, i, j):
    """Valeur d'une cellule, ou celle de l'ancre de sa zone fusionnée."""
    ri, rj = fusions.get((i, j), (i, j))
    return lignes[ri][rj] if ri < len(lignes) and rj < len(lignes[ri]) else None


def _sous_entete(lignes, fusions, h: int, largeur: int) -> bool:
    """La ligne h+1 complète-t-elle l'en-tête h (en-tête sur deux lignes) ?"""
    if h + 2 >= len(lignes):
        return False
    sous = lignes[h + 1]
    non_vides = [v for v in sous if not _vide(v)]
    libs = [v for v in non_vides if _est_libelle(v)]
    if not libs or len(libs) < len(non_vides) or not _a_donnees(lignes[h + 2]):
        return False
    haut = lignes[h]
    for j in range(min(largeur, len(sous))):
        if _est_libelle(sous[j]):
            vide_haut = j >= len(haut) or _vide(haut[j])
            if vide_haut or (h, j) in fusions or any(a == (h, j) for a in fusions.values()):
                return True
    return False


def _tableau_depuis_lignes(nom_fichier: str, feuille: str, lignes: list[list], fusions: dict, empr: str) -> Tableau | None:
    # Largeur utile : dernière colonne non vide de la feuille
    largeur = max((max((j for j, v in enumerate(r) if not _vide(v)), default=-1) + 1 for r in lignes), default=0)
    if largeur == 0:
        return None
    lignes = [list(r[:largeur]) + [None] * (largeur - len(r)) for r in lignes]
    h = _ligne_entete(lignes)
    titre_parts: list[str] = []
    if h is None:
        bruts = [""] * largeur
        debut = 0
    else:
        for r in lignes[:h]:
            titre_parts += [str(v) for v in r if not _vide(v)]
        if _sous_entete(lignes, fusions, h, largeur):
            bruts = []
            for j in range(largeur):
                haut = _valeur_fusion(lignes, fusions, h, j)
                bas = lignes[h + 1][j]
                if fusions.get((h + 1, j)) == (h, j):
                    bas = None  # cellule fusionnée verticalement avec l'en-tête
                haut = "" if _vide(haut) else str(haut).strip()
                bas = "" if _vide(bas) else str(bas).strip()
                bruts.append(f"{haut} {bas}" if haut and bas and normaliser_texte(haut) != normaliser_texte(bas)
                             else (bas or haut))
            debut = h + 2
        else:
            bruts = ["" if _vide(v) else str(v).strip() for v in lignes[h]]
            debut = h + 1
    entetes = [normaliser_texte(b) or f"col_{j + 1}" for j, b in enumerate(bruts)]
    bruts = [b or f"col_{j + 1}" for j, b in enumerate(bruts)]
    donnees, numeros = [], []
    for i in range(debut, len(lignes)):
        r = lignes[i]
        if all(_vide(v) for v in r):
            continue
        # en-tête répété (saut de page imprimé) : ignoré
        if h is not None and [normaliser_texte(v) for v in r] == [normaliser_texte(v) for v in lignes[h]]:
            continue
        donnees.append(r)
        numeros.append(i + 1)  # numéro de ligne Excel / CSV (1-based)
    return Tableau(fichier=nom_fichier, feuille=feuille, entetes=entetes, entetes_bruts=bruts, lignes=donnees,
                   numeros=numeros, titre=" ".join(titre_parts), empreinte_fichier=empr)


def lire_tableaux(nom_fichier: str, path) -> list[Tableau]:
    """Un `Tableau` par onglet non vide d'un xlsx/xlsm, ou un seul pour un csv/txt. [] pour un autre format."""
    path = Path(path)
    ext = Path(nom_fichier).suffix.lower() or path.suffix.lower()
    empr = empreinte(path)
    if ext in (".xlsx", ".xlsm"):
        feuilles = _lire_xlsx(path)
    elif ext in (".csv", ".txt", ".tsv"):
        feuilles = [(Path(nom_fichier).stem, _lire_csv(path), {})]
    else:
        return []
    out = []
    for feuille, lignes, fusions in feuilles:
        t = _tableau_depuis_lignes(nom_fichier, feuille, lignes, fusions, empr)
        if t is not None:
            out.append(t)
    return out


# ------------------------------------------------------------------ totaux (P0-5)
_MOTS_TOTAL = re.compile(r"^[\W_]*(sous[- ]?totaux|sous[- ]?total|totaux|total|cumuls?|cumule|report|a nouveau|"
                         r"solde initial|solde de depart)\b")
_TOTAL_FIN = re.compile(r"\b(sous[- ]?total|total|totaux)\s*:?\s*$")


def _ligne_mot_total(row: list) -> bool:
    """Une cellule texte commence par un mot-clé de total (ou se termine par « total »)."""
    for v in row:
        if isinstance(v, str):
            t = normaliser_texte(v)
            if _MOTS_TOTAL.search(t) or _TOTAL_FIN.search(t):
                return True
    return False


def _date_ligne(row: list) -> date | None:
    for v in row:
        if isinstance(v, (date, datetime)) or (isinstance(v, str) and _est_date(v)):
            return to_date(v)
    return None


def marquer_totaux(tableau: Tableau, col_montant: int | None, col_libelles: list[int] | None = None) -> list[int]:
    """Retire de `tableau.lignes` (et `numeros`) les lignes de total ; les ajoute à `tableau.lignes_total`.

    Ligne de total : cellule texte commençant par total|sous-total|cumul|report|à nouveau|solde initial|solde de départ ;
    ou montant égal (à 1 F près) à la somme d'au moins 2 lignes depuis le total précédent (ou depuis le début), à
    condition que la ligne soit « vide » par ailleurs : aucune valeur dans `col_libelles` (pièce, désignation…), ou,
    sans `col_libelles`, aucune cellule texte ; et pas de date, ou la même date que la ligne précédente.
    Retourne les numéros Excel retirés."""
    gardees, nums, retires = [], [], []
    somme_bloc = somme_tout = 0.0
    n_bloc = n_tout = 0
    date_prec = None
    for row, n in zip(tableau.lignes, tableau.numeros):
        m = num(row[col_montant]) if col_montant is not None and col_montant < len(row) else None
        est_total = _ligne_mot_total(row)
        if not est_total and m is not None:
            if col_libelles is not None:
                libre = all(j >= len(row) or _vide(row[j]) for j in col_libelles)
            else:
                libre = not any(_est_libelle(v) for v in row)
            d = _date_ligne(row)
            if libre and (d is None or d == date_prec):
                est_total = (n_bloc >= 2 and abs(m - somme_bloc) <= 1) or (n_tout >= 2 and abs(m - somme_tout) <= 1)
        if est_total:
            retires.append(n)
            somme_bloc, n_bloc = 0.0, 0
            continue
        gardees.append(row)
        nums.append(n)
        if m is not None:
            somme_bloc += m
            somme_tout += m
            n_bloc += 1
            n_tout += 1
        date_prec = _date_ligne(row) or date_prec
    tableau.lignes, tableau.numeros = gardees, nums
    tableau.lignes_total.extend(retires)
    return retires


# ------------------------------------------------------------------ dates manquantes (P0-5)
def _cle_piece(v) -> str:
    if _vide(v):
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return normaliser_texte(v)


def heriter_dates(tableau: Tableau, col_date: int | None, col_piece: int | None = None) -> None:
    """Ligne sans date : hérite de la date de la ligne datée précédente si elle porte le même n° de pièce
    (n° Excel ajouté à `dates_heritees`) ; sinon retirée et ajoutée à `lignes_sans_date`. Sans `col_date` : rien."""
    if col_date is None:
        return
    gardees, nums = [], []
    date_prec, piece_prec = None, ""
    for row, n in zip(tableau.lignes, tableau.numeros):
        d = to_date(row[col_date], serie=True) if col_date < len(row) else None
        piece = _cle_piece(row[col_piece]) if col_piece is not None and col_piece < len(row) else ""
        if d is not None:
            date_prec, piece_prec = d, piece
        elif date_prec is not None and piece and piece == piece_prec:
            row = list(row)
            while len(row) <= col_date:
                row.append(None)
            row[col_date] = date_prec
            tableau.dates_heritees.append(n)
        else:
            tableau.lignes_sans_date.append(n)
            continue
        gardees.append(row)
        nums.append(n)
    tableau.lignes, tableau.numeros = gardees, nums

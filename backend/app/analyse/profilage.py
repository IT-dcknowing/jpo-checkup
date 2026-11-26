"""Profilage des fichiers d'un dossier : Tableau / PDF / fiche entreprise -> ProfilFeuille + Dossier.

Le profil décrit chaque tableau (statistiques par colonne, échantillon réparti, lignes suspectes de total) pour
que l'IA construise la carte des données. Aucun calcul de montant ici : les tableaux complets sont rendus à part
pour l'entrepôt. Règles génériques uniquement.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from . import lecture as _lecture  # fonctions internes de lecture (ligne d'en-tête)
from .lecture import empreinte, normaliser_texte, num, to_date
from .lecture import _ligne_mot_total  # détection partagée des mots de total
from .modele import Dossier, ProfilColonne, ProfilFeuille, Tableau

TAILLE_ECHANTILLON = 30
MAX_FICHE = 20_000             # caractères de la fiche entreprise
MAX_PAGE_PDF = 6_000           # caractères par page PDF
MAX_PAGES_PDF = 200
MAX_CELLULE = 60               # caractères par valeur d'échantillon
MAX_EXEMPLE = 40               # caractères par exemple de colonne
MAX_IA = 60_000                # taille visée du rendu pour l'IA
SEUIL_DOMINANT = 0.8           # part d'un type pour être « dominant »
PAGE_VIDE = "[page sans texte : scan probable]"

EXT_TABLEUR = {".xlsx": "xlsx", ".xlsm": "xlsx", ".csv": "csv", ".tsv": "csv", ".txt": "csv"}
EXT_FICHE = {".md", ".txt", ".docx"}


# ------------------------------------------------------------------ cellules
def _vide(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _texte(v, limite: int = MAX_CELLULE) -> str:
    """Représentation courte et stable d'une cellule (dates ISO, nombres entiers sans « .0 »)."""
    if _vide(v):
        return ""
    if isinstance(v, datetime):
        s = v.date().isoformat() if v.time() == datetime.min.time() else v.isoformat(sep=" ")
    elif isinstance(v, date):
        s = v.isoformat()
    elif isinstance(v, float):
        s = str(int(v)) if v.is_integer() else repr(round(v, 6))
    else:
        s = str(v).strip()
    return s if len(s) <= limite else s[: limite - 1] + "…"


_MOTS_DATE_FORTS = ("date",)
_MOTS_DATE_FAIBLES = ("jour", "periode", "mois", "echeance")
ETENDUE_SERIE_MAX = 3_660      # jours : au-delà, des « numéros de série » sont plutôt des montants


def _indice_date(entete: str) -> str | None:
    """« fort » (en-tête date), « faible » (jour, période, mois, échéance : simple indice) ou None."""
    e = normaliser_texte(entete)
    if any(m in e for m in _MOTS_DATE_FORTS):
        return "fort"
    if any(m in e for m in _MOTS_DATE_FAIBLES):
        return "faible"
    return None


def _est_serie(v) -> bool:
    """Valeur (nombre ou texte de 5 chiffres) lisible seulement comme numéro de série Excel."""
    return (isinstance(v, (int, float, str)) and not isinstance(v, bool)
            and to_date(v) is None and to_date(v, serie=True) is not None)


def _accepter_series(entete: str, non_vides: list) -> bool:
    """Lire les numéros de série Excel comme des dates ? L'en-tête n'est qu'un indice : il faut aussi que les
    valeurs ressemblent à des dates (étendue ≤ 10 ans ; pour un indice faible, valeurs non toutes identiques)."""
    indice = _indice_date(entete)
    if indice is None:
        return False
    series = [to_date(v, serie=True) for v in non_vides if _est_serie(v)]
    if not series:
        return False
    if (max(series) - min(series)).days > ETENDUE_SERIE_MAX:
        return False
    if indice == "faible" and len(series) >= 3 and len(set(series)) == 1:
        return False
    return True


def _type_cellule(v, serie: bool) -> str:
    if isinstance(v, (date, datetime)):
        return "date"
    if isinstance(v, str) and to_date(v, serie=serie) is not None:
        return "date"
    if serie and isinstance(v, (int, float)) and not isinstance(v, bool) and to_date(v, serie=True) is not None:
        return "date"
    if num(v) is not None:
        return "nombre"
    return "texte"


# ------------------------------------------------------------------ colonnes
def profiler_colonne(index: int, entete: str, valeurs: list) -> ProfilColonne:
    """Statistiques d'une colonne : type dominant, vides, distincts, exemples, min/max/somme, dates extrêmes."""
    n = len(valeurs)
    non_vides = [v for v in valeurs if not _vide(v)]
    serie = _accepter_series(entete, non_vides)
    types = {"date": 0, "nombre": 0, "texte": 0}
    nombres: list[float] = []
    dates: list[date] = []
    longueurs: list[int] = []
    for v in non_vides:
        t = _type_cellule(v, serie)
        types[t] += 1
        if t == "date":
            d = to_date(v, serie=serie)
            if d is not None:
                dates.append(d)
        elif t == "nombre":
            nombres.append(num(v))
        else:
            longueurs.append(len(str(v).strip()))
    if not non_vides:
        dominant = "vide"
    else:
        t_max = max(types, key=types.get)
        dominant = t_max if types[t_max] / len(non_vides) >= SEUIL_DOMINANT else "mixte"
    distincts, exemples = set(), []
    for v in non_vides:
        s = _texte(v, 10_000)
        if s not in distincts:
            distincts.add(s)
            court = _texte(v, MAX_EXEMPLE)
            if len(exemples) < 5 and court not in exemples:  # distincts après troncature
                exemples.append(court)
    p = ProfilColonne(index=index, entete=entete, type_dominant=dominant,
                      part_vides=round(1 - len(non_vides) / n, 4) if n else 1.0,
                      distincts=len(distincts), exemples=exemples)
    if nombres:
        p.min, p.max = min(nombres), max(nombres)
        p.somme = round(sum(nombres), 2)
        p.part_entiers = round(sum(x == int(x) for x in nombres) / len(nombres), 4)
        p.part_negatifs = round(sum(x < 0 for x in nombres) / len(nombres), 4)
    if dates:
        p.date_min, p.date_max = min(dates).isoformat(), max(dates).isoformat()
    if longueurs:
        p.longueur_moyenne = round(sum(longueurs) / len(longueurs), 1)
    return p


# ------------------------------------------------------------------ échantillon
def _repartir(indices: list[int], k: int) -> list[int]:
    """k éléments régulièrement répartis (premier et dernier inclus)."""
    if k <= 0:
        return []
    if len(indices) <= k:
        return list(indices)
    if k == 1:
        return [indices[0]]
    pas = (len(indices) - 1) / (k - 1)
    return sorted({indices[round(i * pas)] for i in range(k)})


def choisir_echantillon(nb: int, prioritaires: list[int], taille: int = TAILLE_ECHANTILLON) -> list[int]:
    """Positions (0-based) d'un échantillon de `taille` lignes parmi `nb` : début, milieu, fin, plus les positions
    prioritaires (total, date vide). Toutes les prioritaires sont gardées si elles laissent au moins 3 places
    (début, milieu, fin) ; sinon elles sont réparties sur les deux tiers de l'échantillon."""
    if nb <= taille:
        return list(range(nb))
    prio = sorted(set(p for p in prioritaires if 0 <= p < nb))
    if len(prio) <= taille - 3:
        choisis = set(prio)
    else:
        choisis = set(_repartir(prio, max(1, (2 * taille) // 3)))
    reste = taille - len(choisis)
    tiers = reste // 3
    debut = list(range(nb))
    milieu_dep = max(0, nb // 2 - (reste - 2 * tiers) // 2)
    blocs = [debut[:tiers], debut[milieu_dep:milieu_dep + reste - 2 * tiers], debut[nb - tiers:] if tiers else []]
    for b in blocs:
        choisis.update(b)
    # compléter (chevauchements) par des lignes régulièrement réparties
    for p in _repartir(list(range(nb)), taille * 2):
        if len(choisis) >= taille:
            break
        choisis.add(p)
    return sorted(choisis)[:taille] if len(choisis) > taille else sorted(choisis)


def _ligne_entete(t: Tableau) -> int | None:
    """Repli : n° Excel estimé de la ligne d'en-tête (ligne précédant la première ligne de données)."""
    if all(b.startswith("col_") for b in t.entetes_bruts):
        return None
    if not t.numeros:
        return None
    return max(1, t.numeros[0] - 1)


_ESTIMER = object()


def lire_tableaux_avec_entete(nom: str, path) -> list[tuple[Tableau, int | None]]:
    """Comme `lire_tableaux`, avec le n° Excel de la ligne d'en-tête détectée (None : pas d'en-tête).
    Contournement local : `Tableau` ne porte pas cette information (cf. contrat modele.py)."""
    path = Path(path)
    ext = Path(nom).suffix.lower() or path.suffix.lower()
    empr = empreinte(path)
    if ext in (".xlsx", ".xlsm"):
        feuilles = _lecture._lire_xlsx(path)
    elif ext in (".csv", ".txt", ".tsv"):
        feuilles = [(Path(nom).stem, _lecture._lire_csv(path), {})]
    else:
        return []
    out = []
    for feuille, lignes, fusions in feuilles:
        t = _lecture._tableau_depuis_lignes(nom, feuille, lignes, fusions, empr)
        if t is None:
            continue
        largeur = max((len(r) for r in lignes), default=0)
        h = _lecture._ligne_entete([list(r) + [None] * (largeur - len(r)) for r in lignes])
        out.append((t, None if h is None else h + 1))
    return out


def profiler_tableau(id_: str, t: Tableau, fmt: str, ligne_entete=_ESTIMER) -> ProfilFeuille:
    """ProfilFeuille d'un Tableau : colonnes, échantillon réparti, lignes suspectes de total.
    `ligne_entete` : n° Excel de l'en-tête s'il est connu (sinon estimé depuis la première ligne de données)."""
    largeur = max([len(t.entetes)] + [len(r) for r in t.lignes])
    bruts = list(t.entetes_bruts) + [f"col_{j + 1}" for j in range(len(t.entetes_bruts), largeur)]
    colonnes = [profiler_colonne(j, bruts[j], [r[j] if j < len(r) else None for r in t.lignes])
                for j in range(largeur)]
    pos_total = [i for i, r in enumerate(t.lignes) if _ligne_mot_total(r)]
    prioritaires = list(pos_total)
    cols_date = [c.index for c in colonnes if c.type_dominant == "date"]
    if cols_date:
        jd = cols_date[0]
        prioritaires += [i for i, r in enumerate(t.lignes) if jd >= len(r) or _vide(r[jd])]
    echantillon = [{"ligne": t.numeros[i], "valeurs": [_texte(v) for v in t.lignes[i]]}
                   for i in choisir_echantillon(len(t.lignes), prioritaires)]
    return ProfilFeuille(id=id_, fichier=t.fichier, feuille=t.feuille, format=fmt,
                         empreinte_fichier=t.empreinte_fichier, nb_lignes=len(t.lignes),
                         ligne_entete=_ligne_entete(t) if ligne_entete is _ESTIMER else ligne_entete,
                         titre=t.titre[:500], colonnes=colonnes,
                         echantillon=echantillon,
                         lignes_suspectes_total=sorted(set(t.numeros[i] for i in pos_total) | set(t.lignes_total)))


# ------------------------------------------------------------------ PDF et fiche
def lire_pdf(path) -> list[str]:
    """Texte par page (pdfplumber), tronqué à 6 000 caractères ; page sans texte signalée « scan probable »."""
    import pdfplumber
    pages = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages[:MAX_PAGES_PDF]:
            texte = (page.extract_text() or "").strip()
            pages.append(texte[:MAX_PAGE_PDF] if texte else PAGE_VIDE)
    return pages


def _decoder(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        import chardet
        enc = chardet.detect(raw[:200_000]).get("encoding") or "cp1252"
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            return raw.decode("cp1252", errors="replace")


def lire_fiche(nom: str, path) -> str:
    """Texte brut d'une fiche entreprise .md/.txt/.docx (paragraphes puis tableaux), 20 000 caractères au plus."""
    ext = Path(nom).suffix.lower() or Path(path).suffix.lower()
    if ext == ".docx":
        import docx
        doc = docx.Document(str(path))
        morceaux = [p.text for p in doc.paragraphs if p.text.strip()]
        for tab in doc.tables:
            for row in tab.rows:
                cellules = []
                for c in row.cells:
                    s = c.text.strip()
                    if s and (not cellules or cellules[-1] != s):  # cellules fusionnées répétées
                        cellules.append(s)
                if cellules:
                    morceaux.append(" | ".join(cellules))
        texte = "\n".join(morceaux)
    elif ext in (".md", ".txt"):
        texte = _decoder(Path(path).read_bytes())
    else:
        raise ValueError(f"format de fiche non pris en charge : {ext or 'sans extension'}")
    return texte.strip()[:MAX_FICHE]


# ------------------------------------------------------------------ dossier
def profiler(fichiers: list[tuple[str, Path]], formulaire: dict,
             fiche: tuple[str, Path] | None) -> tuple[Dossier, dict[str, Tableau]]:
    """Profile les fichiers du dossier. Retourne le Dossier (profils t1, t2… dans l'ordre de lecture, fiche,
    formulaire, fichiers ignorés) et le dict id de profil -> Tableau complet (pour l'entrepôt ; PDF exclus)."""
    profils: list[ProfilFeuille] = []
    tableaux: dict[str, Tableau] = {}
    ignores: list[dict] = []
    vus: dict[str, str] = {}

    def nouvel_id() -> str:
        return f"t{len(profils) + 1}"

    for nom, path in fichiers:
        path = Path(path)
        try:
            empr = empreinte(path)
        except OSError as e:
            ignores.append({"fichier": nom, "raison": f"illisible ({type(e).__name__})"})
            continue
        if empr in vus:
            ignores.append({"fichier": nom, "raison": f"doublon de {vus[empr]}"})
            continue
        vus[empr] = nom
        ext = Path(nom).suffix.lower() or path.suffix.lower()
        try:
            if ext in EXT_TABLEUR:
                lus = lire_tableaux_avec_entete(nom, path)
                if not lus:
                    ignores.append({"fichier": nom, "raison": "aucun tableau lisible"})
                for t, h in lus:
                    t.empreinte_fichier = t.empreinte_fichier or empr
                    p = profiler_tableau(nouvel_id(), t, EXT_TABLEUR[ext], h)
                    profils.append(p)
                    tableaux[p.id] = t
            elif ext == ".pdf":
                pages = lire_pdf(path)
                profils.append(ProfilFeuille(id=nouvel_id(), fichier=nom, feuille="", format="pdf",
                                             empreinte_fichier=empr, nb_lignes=0, ligne_entete=None, titre="",
                                             colonnes=[], echantillon=[], texte_pages=pages))
            else:
                ignores.append({"fichier": nom, "raison": f"format non pris en charge ({ext or 'sans extension'})"})
        except Exception as e:  # noqa: BLE001 — un fichier illisible n'arrête pas le dossier
            ignores.append({"fichier": nom, "raison": f"lecture impossible ({type(e).__name__}: {str(e)[:120]})"})

    texte_fiche = ""
    if fiche is not None:
        nom_f, path_f = fiche
        try:
            texte_fiche = lire_fiche(nom_f, path_f)
        except Exception as e:  # noqa: BLE001
            ignores.append({"fichier": nom_f, "raison": f"fiche entreprise illisible ({str(e)[:120]})"})
    return Dossier(profils=profils, fiche_entreprise=texte_fiche, formulaire=dict(formulaire or {}),
                   fichiers_ignores=ignores), tableaux


# ------------------------------------------------------------------ rendu pour l'IA
# Format tabulaire des colonnes (paliers les plus compacts) : une liste de valeurs par colonne, dans cet ordre.
FORMAT_COLONNES = ["index", "entete", "type_dominant", "part_vides", "distincts", "min", "max", "somme",
                   "date_min", "date_max", "exemple"]


def _arrondi(x):
    if isinstance(x, float):
        return int(x) if x.is_integer() else round(x, 2)
    return x


def _colonne_compacte(c: ProfilColonne, nb_exemples: int, cellule: int, detail: int) -> dict | list:
    """detail 2 : toutes les statistiques ; 1 : sans parts d'entiers/négatifs ni longueur moyenne ;
    0 : liste de valeurs selon `FORMAT_COLONNES`."""
    exemples = [e[:cellule] for e in c.exemples[:nb_exemples]]
    if detail <= 0:
        return [c.index, c.entete[:cellule], c.type_dominant, round(c.part_vides, 2), c.distincts,
                _arrondi(c.min), _arrondi(c.max), _arrondi(c.somme), c.date_min, c.date_max,
                exemples[0] if exemples else None]
    d = {k: _arrondi(v) for k, v in asdict(c).items() if v is not None}
    if detail == 1:
        for k in ("part_entiers", "part_negatifs", "longueur_moyenne"):
            d.pop(k, None)
        d["entete"] = c.entete[:max(cellule, MAX_EXEMPLE)]
    d["exemples"] = exemples
    return d


def _reduire_echantillon(p: ProfilFeuille, k: int) -> list[dict]:
    """k lignes de l'échantillon, réparties, en gardant d'abord les lignes suspectes de total."""
    ech = p.echantillon
    if len(ech) <= k:
        return ech
    suspects = set(p.lignes_suspectes_total)
    prio = [i for i, e in enumerate(ech) if e["ligne"] in suspects]
    choisis = set(_repartir(prio, k // 2))
    for i in _repartir(list(range(len(ech))), k):
        if len(choisis) >= k:
            break
        choisis.add(i)
    return [ech[i] for i in sorted(choisis)]


def _profil_compact(p: ProfilFeuille, k: int, nb_exemples: int, cellule: int, page: int, detail: int) -> dict:
    d = {"id": p.id, "fichier": p.fichier, "feuille": p.feuille, "format": p.format, "nb_lignes": p.nb_lignes,
         "ligne_entete": p.ligne_entete, "titre": p.titre if detail > 0 else p.titre[:120]}
    if p.format == "pdf":
        d["texte_pages"] = [t[:page] for t in (p.texte_pages or [])]
        return d
    d["colonnes"] = [_colonne_compacte(c, nb_exemples, cellule, detail) for c in p.colonnes]
    d["echantillon"] = [{"ligne": e["ligne"], "valeurs": [v[:cellule] for v in e["valeurs"]]}
                        for e in _reduire_echantillon(p, k)]
    if p.lignes_suspectes_total:
        d["lignes_suspectes_total"] = p.lignes_suspectes_total[:100]
        if len(p.lignes_suspectes_total) > 100:
            d["lignes_suspectes_total_nb"] = len(p.lignes_suspectes_total)
    return d


# (lignes d'échantillon, exemples par colonne, caractères par cellule, caractères par page PDF, détail colonnes)
PALIERS_IA = [(TAILLE_ECHANTILLON, 5, MAX_CELLULE, MAX_PAGE_PDF, 2), (15, 5, MAX_CELLULE, MAX_PAGE_PDF, 2),
              (8, 5, MAX_CELLULE, 3_000, 2), (8, 3, 30, 1_500, 1), (5, 2, 20, 800, 1), (5, 1, 20, 800, 0),
              (3, 1, 15, 300, 0), (1, 1, 12, 120, 0), (0, 0, 12, 60, 0)]


def profils_pour_ia(dossier: Dossier, limite: int = MAX_IA) -> str:
    """JSON compact des profils pour le prompt de la carte, borné à ~`limite` caractères : échantillons réduits à 15
    puis 8 lignes, puis exemples, cellules et pages PDF raccourcis, statistiques secondaires retirées, enfin colonnes
    au format tabulaire (`colonnes_format`). Aucun tableau ni aucune colonne n'est supprimé : au-delà du dernier
    palier (dossier énorme), le texte peut dépasser la limite."""
    texte = ""
    for k, ex, cel, page, detail in PALIERS_IA:
        racine: dict = {}
        if detail <= 0:
            racine["colonnes_format"] = FORMAT_COLONNES
        racine["profils"] = [_profil_compact(p, k, ex, cel, page, detail) for p in dossier.profils]
        texte = json.dumps(racine, ensure_ascii=False, separators=(",", ":"))
        if len(texte) <= limite:
            break
    return texte

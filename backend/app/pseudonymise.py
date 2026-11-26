"""Pseudonymisation des tiers (clients, fournisseurs, salariés) dans les tableaux CSV / XLSX avant l'appel à l'IA.

L'anonymisation de base (anonymize.py) ne connaît que le déclarant. Les journaux, eux, contiennent les noms des
clients, des fournisseurs et des salariés. Ici, chaque tableau est relu colonne par colonne :
- colonnes d'identité (client, fournisseur, salarié, nom, matricule, tiers...) : valeurs remplacées par Tiers_1, Tiers_2...
  (le même nom reçoit toujours le même pseudonyme : l'IA voit qu'il s'agit d'un même tiers, sans savoir lequel) ;
- colonnes de libellé (libellé, intitulé, désignation, observation...) : remplacées dans les journaux et sur les comptes
  de tiers (classe 4) d'une balance ou d'un grand livre, où le libellé porte souvent un nom ;
- journaux de paie : jamais transmis ligne à ligne (salaires nominatifs) ; seuls les totaux calculés par le serveur
  partent dans la synthèse.
Tableaux sans en-tête reconnaissable : tout texte qui n'est ni un nombre, ni une date, ni un code court est masqué.

PDF : voir pseudonymiser_pdf (tableaux détectés, civilités, étiquettes, paie réduite aux totaux) ; un nom isolé dans un
texte libre peut subsister (limite indiquée dans la notice).
"""
from __future__ import annotations

import re
from pathlib import Path

from .comprehension import Table, _col_compte, _date, _entete, _norm, _num, classer, read_tables

IDENTITE = re.compile(r"client|fournisseur|salari|employ|^nom\b|\bnom$|prenom|matricule|tiers|beneficiaire|raison sociale"
                      r"|contact|telephone|e-?mail|adresse|agent|collaborateur|personnel$|cnps n|n.? ?cnps|ifu|ncc")
LIBELLE = re.compile(r"libell|intitul|designation|observation|commentaire|description|objet|motif|reference client")
COMPTE_TIERS = re.compile(r"^4[0-7]")
MAX_LIGNES = 20_000


class Pseudonymes:
    """Table de correspondance commune à tous les fichiers d'un dépôt."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def __call__(self, valeur) -> str:
        cle = _norm(valeur)
        if not cle:
            return ""
        if cle not in self._map:
            self._map[cle] = f"Tiers_{len(self._map) + 1}"
        return self._map[cle]


def _cell(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float):
        if x != x:
            return ""
        return str(int(x)) if x == int(x) else str(round(x, 4))
    if hasattr(x, "isoformat"):
        return x.isoformat()[:10]
    return re.sub(r"\s+", " ", str(x)).strip()


def _neutre(x) -> bool:
    """Valeur sans risque d'identifier une personne : nombre, date, code court sans lettre minuscule."""
    if x is None or isinstance(x, (int, float)) or hasattr(x, "isoformat"):
        return True
    s = str(x).strip()
    return not s or _num(s) is not None or _date(s) is not None or bool(re.fullmatch(r"[A-Z0-9][A-Z0-9 ./_-]{0,11}", s))


def _ligne(cells: list[str]) -> str:
    return " | ".join(cells).rstrip(" |")


def pseudonymiser_tableaux(path: Path, nom_fichier: str, pseudo: Pseudonymes) -> str | None:
    """Texte du fichier (même mise en forme que readers.py) avec les tiers remplacés ; None si ce n'est pas un tableau."""
    feuilles = read_tables(path)
    if not feuilles:
        return None
    blocs = []
    for feuille, rows in feuilles:
        rows = [r for r in rows[:MAX_LIGNES] if any(c not in (None, "") for c in r)]
        if not rows:
            continue
        h = _entete(rows)
        if h is None:  # pas d'en-tête : on ne garde que les valeurs neutres
            lignes = [_ligne([_cell(c) if _neutre(c) else "[TEXTE]" for c in r]) for r in rows]
            blocs.append(f"--- FEUILLE « {feuille} » ---\n" + "\n".join(lignes))
            continue
        i, entetes = h
        t = Table(f"{nom_fichier} › {feuille}", entetes, rows[i + 1:])
        genre = classer(t, nom_fichier)
        if genre == "paie":
            blocs.append(f"--- FEUILLE « {feuille} » : journal de paie ({len(t.lignes)} lignes) — détail nominatif non transmis, "
                         "totaux dans la synthèse calculée par le serveur ---")
            continue
        ident = {j for j, e in enumerate(entetes) if IDENTITE.search(e)}
        libelle = {j for j, e in enumerate(entetes) if LIBELLE.search(e)} - ident
        jc = _col_compte(t) if genre in ("balance", "grand_livre") else None
        journal = genre in ("ventes", "achats", "autre")
        # lignes au-dessus de l'en-tête (titre, raison sociale...) : neutres seulement
        lignes = [_ligne([_cell(c) if _neutre(c) else "[TEXTE]" for c in r]) for r in rows[:i]]
        lignes.append(_ligne([_cell(c) for c in rows[i]]))
        for r in t.lignes:
            tiers = jc is not None and jc < len(r) and COMPTE_TIERS.match(str(r[jc] or "").strip())
            out = []
            for j, c in enumerate(r):
                if j in ident and not _neutre(c):
                    out.append(pseudo(c))
                elif j in libelle and (journal or tiers) and c not in (None, ""):
                    out.append("[LIBELLÉ]" if _neutre(c) is False else _cell(c))
                else:
                    out.append(_cell(c))
            lignes.append(_ligne(out))
        blocs.append(f"--- FEUILLE « {feuille} » ---\n" + "\n".join(lignes))
    return "\n\n".join(blocs) if blocs else None


# ------------------------------------------------------------------ PDF (texte sans colonnes)
CIVILITE = re.compile(r"\b(M\.|Mr\.?|Mme|Mlle|Monsieur|Madame|Mademoiselle|Dr\.?|Me)\s+"
                      r"([A-ZÀ-Ý][\w'’-]+(?:\s+[A-ZÀ-Ý][\w'’-]+){0,3})")
ETIQUETTE = re.compile(r"(?i:\b(client|fournisseur|salari[ée]e?|employ[ée]e?|b[ée]n[ée]ficiaire|nom(?: et pr[ée]noms?)?|pr[ée]noms?|"
                       r"tiers|agent|collaborateur|matricule))\s*[:：]\s*([A-ZÀ-Ý0-9][\w'’&.-]*(?:\s+[A-ZÀ-Ý][\w'’&.-]*){0,4})")
TOTAL_PAIE = re.compile(r"\b(total|totaux|sous-total|cumul|masse salariale|r[ée]capitulatif)\b", re.I)


def _remplacer(texte: str, originaux: dict[str, str]) -> str:
    for orig in sorted(originaux, key=len, reverse=True):
        if len(orig) >= 3:
            texte = re.sub(rf"(?<!\w){re.escape(orig)}(?!\w)", originaux[orig], texte, flags=re.I)
    return texte


def pseudonymiser_pdf(path: Path, nom_fichier: str, texte: str, pseudo: Pseudonymes, max_pages: int = 80) -> str:
    """Texte d'un PDF (déjà extrait, OCR compris) avec les tiers remplacés, au mieux :
    1. tableaux détectés dans le PDF : colonnes d'identité relevées puis remplacées partout dans le texte ;
    2. noms précédés d'une civilité (M., Mme, Monsieur...) ou d'une étiquette (« Client : », « Salarié : »...) ;
    3. journal ou bulletins de paie : seules les lignes de totaux sont gardées (aucune ligne nominative).
    Reste une limite : un nom isolé dans un texte libre, sans civilité ni étiquette, n'est pas reconnu."""
    import pdfplumber

    originaux: dict[str, str] = {}
    paie = bool(re.search(r"paie|salaire|bulletin", _norm(nom_fichier)))
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:max_pages]:
            for tab in page.extract_tables() or []:
                rows = [r for r in tab if r and any(c not in (None, "") for c in r)]
                h = _entete(rows) if rows else None
                if not h:
                    continue
                i, entetes = h
                t = Table(nom_fichier, entetes, rows[i + 1:])
                if classer(t, nom_fichier) == "paie":
                    paie = True
                for j, e in enumerate(entetes):
                    if IDENTITE.search(e):
                        for r in t.lignes:
                            if j < len(r) and not _neutre(r[j]):
                                originaux[str(r[j]).strip()] = pseudo(r[j])
    if paie:
        totaux = [ligne for ligne in texte.splitlines() if TOTAL_PAIE.search(ligne) and re.search(r"\d", ligne)]
        texte = ("[Bulletins / journal de paie : détail nominatif non transmis ; lignes de totaux uniquement]\n"
                 + "\n".join(totaux))
    texte = _remplacer(texte, originaux)
    texte = CIVILITE.sub(lambda m: f"{m.group(1)} {pseudo(m.group(2))}", texte)
    texte = ETIQUETTE.sub(lambda m: f"{m.group(1)} : {m.group(2) if _neutre(m.group(2).strip()) else pseudo(m.group(2))}", texte)
    return texte

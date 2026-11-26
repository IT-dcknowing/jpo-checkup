"""Tests du profilage (données synthétiques générées dans tmp_path)."""
import json
import shutil
from datetime import date, timedelta

import docx
from openpyxl import Workbook

from app.analyse.modele import Dossier, ProfilColonne, ProfilFeuille
from app.analyse.profilage import (PAGE_VIDE, choisir_echantillon, profiler, profiler_colonne, profils_pour_ia)


# ------------------------------------------------------------------ fabriques
def _xlsx(path, feuilles: dict):
    wb = Workbook()
    wb.remove(wb.active)
    for nom, lignes in feuilles.items():
        ws = wb.create_sheet(nom)
        for r in lignes:
            ws.append(r)
    wb.save(path)
    return path


def _journal_ventes(n=200, totaux_tous=50):
    """Titre, en-tête FR, n ventes, un sous-total tous les `totaux_tous`, quelques lignes sans date."""
    lignes = [["JOURNAL DES VENTES 2025"], [], ["Date", "N° pièce", "Client", "Désignation", "Qté", "PU", "Montant"]]
    d0 = date(2025, 1, 2)
    bloc = 0
    for i in range(n):
        d = d0 + timedelta(days=i)
        jour = None if i % 37 == 5 else d
        m = (i % 7 + 1) * 1000
        lignes.append([jour, f"F{i:04d}", f"Client {i % 9}", "Article", i % 7 + 1, 1000, m])
        bloc += m
        if (i + 1) % totaux_tous == 0:
            lignes.append([None, None, None, "Sous-total", None, None, bloc])
            bloc = 0
    lignes.append(["TOTAL GÉNÉRAL", None, None, None, None, None, 999])
    return lignes


def _pdf(path, pages: list[str]):
    """PDF minimal écrit à la main (pages avec ou sans texte)."""
    objets = ["<< /Type /Catalog /Pages 2 0 R >>", None,
              "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for texte in pages:
        flux = f"BT /F1 12 Tf 72 720 Td ({texte}) Tj ET" if texte else ""
        objets.append(f"<< /Length {len(flux)} >>\nstream\n{flux}\nendstream")
        contenu = len(objets)
        objets.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {contenu} 0 R "
                      f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objets)} 0 R")
    objets[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    positions = []
    for i, o in enumerate(objets, 1):
        positions.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n".encode()
    for p in positions:
        out += f"{p:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return path


# ------------------------------------------------------------------ profiler
def test_xlsx_plusieurs_onglets_ids_et_statistiques(tmp_path):
    f = _xlsx(tmp_path / "classeur.xlsx", {
        "Ventes": _journal_ventes(),
        "Banque": [["Date opération", "Libellé", "Débit", "Crédit", "Solde"],
                   *[[date(2025, 3, 1) + timedelta(days=i), f"Opération {i}", "1 250 000" if i % 2 else None,
                      None if i % 2 else 500, 10_000 - i] for i in range(40)]],
    })
    dossier, tableaux = profiler([("classeur.xlsx", f)], {"secteur": "commerce"}, None)
    assert [p.id for p in dossier.profils] == ["t1", "t2"]
    assert set(tableaux) == {"t1", "t2"}
    t1, t2 = dossier.profils
    assert t1.format == "xlsx" and t1.feuille == "Ventes" and "JOURNAL" in t1.titre
    assert t1.ligne_entete == 3
    assert t1.nb_lignes == len(tableaux["t1"].lignes)
    cols = {c.entete: c for c in t1.colonnes}
    assert cols["Date"].type_dominant == "date"
    assert cols["Date"].date_min == "2025-01-02"
    assert cols["Montant"].type_dominant == "nombre" and cols["Montant"].min == 999  # ligne TOTAL GÉNÉRAL incluse
    assert cols["Client"].type_dominant == "texte" and cols["Client"].distincts == 9
    assert all(len(e) <= 40 for c in t1.colonnes for e in c.exemples)
    # échantillon : 30 lignes, début/fin, toutes les lignes de total (5) et des lignes sans date
    nums = [e["ligne"] for e in t1.echantillon]
    assert len(nums) == 30 and nums == sorted(nums)
    t = tableaux["t1"]
    assert nums[0] == t.numeros[0] and nums[-1] == t.numeros[-1]
    assert set(t1.lignes_suspectes_total) <= set(nums)
    assert len(t1.lignes_suspectes_total) == 5
    jd = 0
    sans_date = {n for r, n in zip(t.lignes, t.numeros) if r[jd] is None}
    assert sans_date & set(nums)
    # montants texte « 1 250 000 » reconnus comme nombres
    debit = {c.entete: c for c in t2.colonnes}["Débit"]
    assert debit.max == 1_250_000 and debit.part_vides == 0.5
    assert dossier.formulaire == {"secteur": "commerce"}


def test_csv_point_virgule_doublon_et_format_inconnu(tmp_path):
    contenu = "date;libelle;montant\n" + "\n".join(f"0{i % 9 + 1}/02/2025;achat {i};{i * 100}" for i in range(12))
    a = tmp_path / "achats.csv"
    a.write_text(contenu, encoding="cp1252")
    b = tmp_path / "copie.csv"
    shutil.copy(a, b)
    c = tmp_path / "notes.odt"
    c.write_bytes(b"xx")
    d = tmp_path / "vide.csv"
    d.write_text("", encoding="utf-8")
    dossier, tableaux = profiler([("achats.csv", a), ("copie.csv", b), ("notes.odt", c), ("vide.csv", d)], {}, None)
    assert [p.id for p in dossier.profils] == ["t1"]
    p = dossier.profils[0]
    assert p.format == "csv" and p.nb_lignes == 12 and len(p.echantillon) == 12
    assert len(p.empreinte_fichier) == 64
    raisons = {i["fichier"]: i["raison"] for i in dossier.fichiers_ignores}
    assert raisons["copie.csv"] == "doublon de achats.csv"
    assert "format" in raisons["notes.odt"]
    assert "vide.csv" in raisons


def test_csv_virgule_sans_entete(tmp_path):
    f = tmp_path / "export.csv"
    f.write_text("\n".join(f"2025-0{i % 9 + 1}-10,{i * 10},{-i}" for i in range(50)), encoding="utf-8")
    dossier, _ = profiler([("export.csv", f)], {}, None)
    p = dossier.profils[0]
    assert p.ligne_entete is None
    assert [c.entete for c in p.colonnes] == ["col_1", "col_2", "col_3"]
    assert p.colonnes[2].part_negatifs > 0.9
    assert len(p.echantillon) == 30


def test_pdf_pages_et_scan_probable(tmp_path):
    f = _pdf(tmp_path / "etats.pdf", ["Bilan actif 2025 Total 1 234 567", ""])
    dossier, tableaux = profiler([("etats.pdf", f)], {}, None)
    p = dossier.profils[0]
    assert p.format == "pdf" and p.id == "t1" and tableaux == {}
    assert "1 234 567" in p.texte_pages[0]
    assert p.texte_pages[1] == PAGE_VIDE


def test_fiche_docx_md_et_invalide(tmp_path):
    doc = docx.Document()
    doc.add_paragraph("Activité : négoce de matériaux")
    tab = doc.add_table(rows=1, cols=2)
    tab.rows[0].cells[0].text = "Sites"
    tab.rows[0].cells[1].text = "Deux magasins"
    doc.add_paragraph("x" * 30_000)
    fd = tmp_path / "fiche.docx"
    doc.save(fd)
    dossier, _ = profiler([], {}, ("fiche.docx", fd))
    assert dossier.fiche_entreprise.startswith("Activité : négoce")
    assert len(dossier.fiche_entreprise) == 20_000
    fm = tmp_path / "fiche.md"
    fm.write_text("# Entreprise\nDécouvert autorisé : 5 000 000", encoding="utf-8")
    assert "Découvert" in profiler([], {}, ("fiche.md", fm))[0].fiche_entreprise
    fx = tmp_path / "fiche.rtf"
    fx.write_text("{\\rtf1}", encoding="utf-8")
    d3, _ = profiler([], {}, ("fiche.rtf", fx))
    assert d3.fiche_entreprise == "" and d3.fichiers_ignores[0]["fichier"] == "fiche.rtf"


def test_fichier_corrompu_ne_bloque_pas(tmp_path):
    bad = tmp_path / "abime.xlsx"
    bad.write_bytes(b"pas un zip")
    ok = _xlsx(tmp_path / "ok.xlsx", {"S": [["Date", "Montant"], [date(2025, 1, 1), 10], [date(2025, 1, 2), 20]]})
    dossier, _ = profiler([("abime.xlsx", bad), ("ok.xlsx", ok)], {}, None)
    assert [p.fichier for p in dossier.profils] == ["ok.xlsx"]
    assert "lecture impossible" in dossier.fichiers_ignores[0]["raison"]


# ------------------------------------------------------------------ unitaires
def test_colonne_mixte_et_vide():
    assert profiler_colonne(0, "x", [None, "", None]).type_dominant == "vide"
    c = profiler_colonne(1, "x", ["a", "b", 1, 2, "2025-01-01"])
    assert c.type_dominant == "mixte" and c.distincts == 5
    serie = profiler_colonne(2, "Date", [45000, 45001, 45002])
    assert serie.type_dominant == "date" and serie.date_min == "2023-03-15"


def test_choisir_echantillon_prioritaires():
    pos = choisir_echantillon(1000, [500, 777, 3])
    assert len(pos) == 30 and {0, 999, 500, 777, 3} <= set(pos)
    trop = choisir_echantillon(1000, list(range(100, 1000, 10)))
    assert len(trop) == 30 and 0 in trop and 999 in trop
    assert choisir_echantillon(10, [2]) == list(range(10))


# ------------------------------------------------------------------ rendu IA
def _profil_gros(i: int) -> ProfilFeuille:
    cols = [ProfilColonne(index=j, entete=f"colonne {j}", type_dominant="texte", part_vides=0, distincts=99,
                          exemples=["x" * 40] * 5) for j in range(12)]
    ech = [{"ligne": n + 2, "valeurs": ["v" * 60] * 12} for n in range(30)]
    return ProfilFeuille(id=f"t{i}", fichier="f.xlsx", feuille=f"S{i}", format="xlsx", empreinte_fichier="e",
                         nb_lignes=1000, ligne_entete=1, titre="", colonnes=cols, echantillon=ech,
                         lignes_suspectes_total=[17])


def test_profils_pour_ia_borne_sans_supprimer(tmp_path):
    petit = Dossier(profils=[_profil_gros(1)], fiche_entreprise="", formulaire={})
    d = json.loads(profils_pour_ia(petit))
    assert len(d["profils"][0]["echantillon"]) == 30
    gros = Dossier(profils=[_profil_gros(i) for i in range(1, 9)]
                   + [ProfilFeuille(id="t9", fichier="p.pdf", feuille="", format="pdf", empreinte_fichier="e",
                                    nb_lignes=0, ligne_entete=None, titre="", colonnes=[], echantillon=[],
                                    texte_pages=["y" * 6000] * 5)],
                   fiche_entreprise="", formulaire={})
    texte = profils_pour_ia(gros)
    assert len(texte) <= 60_000
    d = json.loads(texte)
    assert [p["id"] for p in d["profils"]] == [f"t{i}" for i in range(1, 10)]
    ech = d["profils"][0]["echantillon"]
    assert len(ech) in (15, 8) and 17 in [e["ligne"] for e in ech]
    # très gros dossier : toujours tous les tableaux
    enorme = Dossier(profils=[_profil_gros(i) for i in range(1, 80)], fiche_entreprise="", formulaire={})
    assert len(json.loads(profils_pour_ia(enorme))["profils"]) == 79


# ================================================================== cas limites adverses (données sales)
import csv as _csv
import random as _random
import time as _time


def test_onglets_anglais_abreges_titre_negatifs_et_montants_texte(tmp_path):
    """En-têtes anglais/abrégés, titre sur deux lignes, montants « (1 000) », « 1 250 000 F », dates texte."""
    banque = [["RELEVE DE COMPTE", None, None], ["Période du 01/01/2025 au 31/03/2025"], [],
              ["Dt", "Description", "Amt", "Bal"]]
    banque += [[f"{(i % 28) + 1:02d}/0{i % 3 + 1}/2025", f"Op {i}",
                "(1 000)" if i % 4 == 0 else f"{(i + 1) * 1000:,} F".replace(",", " "), 50_000 - i] for i in range(60)]
    f = _xlsx(tmp_path / "Releve.XLSX", {"Bank": banque, "Vide": [], "Notes": [["Commentaire libre"]]})
    dossier, tableaux = profiler([("Releve.XLSX", f)], {}, None)
    p = dossier.profils[0]
    assert p.format == "xlsx" and p.feuille == "Bank"
    assert "RELEVE" in p.titre and p.ligne_entete == 4
    cols = {c.entete: c for c in p.colonnes}
    assert cols["Dt"].type_dominant == "date" and cols["Dt"].date_min == "2025-01-01"
    assert cols["Amt"].type_dominant == "nombre" and cols["Amt"].min == -1000 and cols["Amt"].part_negatifs == 0.25
    # ids consécutifs sans trou malgré l'onglet vide
    assert [q.id for q in dossier.profils] == [f"t{i}" for i in range(1, len(dossier.profils) + 1)]
    assert set(tableaux) == {q.id for q in dossier.profils}


def test_ordre_ids_xlsx_pdf_csv_et_doublon_renomme(tmp_path):
    f1 = _xlsx(tmp_path / "a.xlsx", {"S1": [["Date", "Montant"], [date(2025, 1, 1), 1], [date(2025, 1, 2), 2]],
                                     "S2": [["Date", "Montant"], [date(2025, 2, 1), 3], [date(2025, 2, 2), 4]]})
    pdf = _pdf(tmp_path / "b.pdf", ["Compte de resultat 2025"])
    c = tmp_path / "c.csv"
    c.write_text("date,amount\n2025-01-01,10\n2025-01-02,20\n", encoding="utf-8")
    sous = tmp_path / "sous"
    sous.mkdir()
    copie = sous / "autre_nom.xlsx"
    shutil.copy(f1, copie)
    dossier, tableaux = profiler([("a.xlsx", f1), ("b.pdf", pdf), ("autre_nom.xlsx", copie), ("c.csv", c),
                                 ("absent.xlsx", tmp_path / "absent.xlsx")], None, None)
    assert [(p.id, p.format) for p in dossier.profils] == [("t1", "xlsx"), ("t2", "xlsx"), ("t3", "pdf"), ("t4", "csv")]
    assert set(tableaux) == {"t1", "t2", "t4"}
    raisons = {i["fichier"]: i["raison"] for i in dossier.fichiers_ignores}
    assert raisons["autre_nom.xlsx"] == "doublon de a.xlsx"
    assert "absent.xlsx" in raisons
    assert dossier.formulaire == {}


def test_totaux_intercales_tous_dans_echantillon_24_mois(tmp_path):
    """Journal de 2 ans : 24 sous-totaux mensuels + total général (25 lignes de total) parmi ~2 000 lignes.
    Spec : l'échantillon inclut toujours les lignes qui contiennent un mot de total (25 ≤ 30 : c'est possible)."""
    lignes = [["Date", "Pièce", "Client", "Montant"]]
    for mois in range(24):
        an, m = 2024 + mois // 12, mois % 12 + 1
        s = 0
        for j in range(80):
            v = (j % 9 + 1) * 1500
            lignes.append([date(an, m, j % 28 + 1), f"F{mois:02d}{j:03d}", f"Client {j % 13}", v])
            s += v
        lignes.append([None, None, f"Sous-total {m:02d}/{an}", s])
    lignes.append([None, None, "TOTAL GÉNÉRAL", 1])
    f = _xlsx(tmp_path / "journal.xlsx", {"Ventes": lignes})
    dossier, _ = profiler([("journal.xlsx", f)], {}, None)
    p = dossier.profils[0]
    assert len(p.lignes_suspectes_total) == 25
    nums = {e["ligne"] for e in p.echantillon}
    assert len(p.echantillon) <= 30
    assert set(p.lignes_suspectes_total) <= nums


def test_ligne_vide_entre_entete_et_donnees(tmp_path):
    """Export courant : une ligne vide sous l'en-tête. La ligne d'en-tête reste la ligne 3."""
    lignes = [["Grand livre"], [], ["Date", "Compte", "Libellé", "Débit", "Crédit"], []]
    lignes += [[date(2025, 1, i + 1), "411000", f"Facture {i}", 1000 * i, None] for i in range(20)]
    f = _xlsx(tmp_path / "gl.xlsx", {"GL": lignes})
    p = profiler([("gl.xlsx", f)], {}, None)[0].profils[0]
    assert p.ligne_entete == 3


def test_entetes_evoquant_jour_ou_echeance_mais_montants(tmp_path):
    """Faux positifs de mots-clés : « Recette du jour », « Échéance » (mensualité) contiennent des montants
    entre 30 000 et 60 000 F : ce sont des nombres, pas des dates (statistiques min/max/somme attendues)."""
    _random.seed(3)
    lignes = [["Date", "Recette du jour", "Échéance", "Capital restant"]]
    for i in range(90):
        lignes.append([date(2025, 1, 1) + timedelta(days=i), _random.randint(30_000, 59_000), 45_500,
                       5_000_000 - i * 45_500])
    f = _xlsx(tmp_path / "caisse.xlsx", {"Caisse": lignes})
    p = profiler([("caisse.xlsx", f)], {}, None)[0].profils[0]
    cols = {c.entete: c for c in p.colonnes}
    assert cols["Recette du jour"].type_dominant == "nombre"
    assert cols["Recette du jour"].somme is not None and cols["Recette du jour"].min >= 30_000
    assert cols["Échéance"].type_dominant == "nombre" and cols["Échéance"].somme == 45_500 * 90


def test_csv_dates_numero_de_serie_en_texte(tmp_path):
    """CSV exporté avec les dates en numéros de série Excel (texte « 45292 ») sous un en-tête « Date »."""
    f = tmp_path / "export.csv"
    f.write_text("Date;Libelle;Montant\n" + "\n".join(f"{45292 + i};Vente {i};{1000 + i}" for i in range(40)),
                 encoding="utf-8")
    p = profiler([("export.csv", f)], {}, None)[0].profils[0]
    c = p.colonnes[0]
    assert c.type_dominant == "date" and c.date_min == "2024-01-01"


def test_exemples_distincts_apres_troncature():
    """Libellés bancaires longs au préfixe commun : les exemples (≤ 40 car.) doivent rester distincts."""
    valeurs = [f"VIREMENT RECU EN FAVEUR DE LA SOCIETE CLIENTE REF {i:05d}" for i in range(50)]
    c = profiler_colonne(1, "Libellé", valeurs)
    assert c.distincts == 50
    assert all(len(e) <= 40 for e in c.exemples)
    assert len(set(c.exemples)) == len(c.exemples)


def test_dates_vides_prioritaires_dans_echantillon(tmp_path):
    """Lignes sans date (pièces sur plusieurs lignes) : présentes dans l'échantillon, avec début et fin."""
    lignes = [["Date", "Pièce", "Désignation", "Montant"]]
    for i in range(3000):
        lignes.append([None if i in (1500, 2222) else date(2025, 1, 1) + timedelta(days=i % 365),
                       f"P{i}", "Article", 100 + i])
    f = _xlsx(tmp_path / "v.xlsx", {"V": lignes})
    dossier, tableaux = profiler([("v.xlsx", f)], {}, None)
    p, t = dossier.profils[0], tableaux["t1"]
    nums = [e["ligne"] for e in p.echantillon]
    assert len(nums) == 30 and nums[0] == t.numeros[0] and nums[-1] == t.numeros[-1]
    assert {1502, 2224} <= set(nums)
    milieu = t.numeros[len(t.numeros) // 2]
    assert any(abs(n - milieu) < 200 for n in nums)


def test_mot_total_faux_positif_ne_casse_pas_la_repartition(tmp_path):
    """Un fournisseur dont le nom commence par « Total » sur toutes les lignes : l'échantillon garde début/fin."""
    lignes = [["Date", "Fournisseur", "Montant"]]
    lignes += [[date(2025, 1, 1) + timedelta(days=i % 300), "Total station service", 25_000 + i] for i in range(600)]
    f = _xlsx(tmp_path / "carburant.xlsx", {"Achats": lignes})
    dossier, t = profiler([("carburant.xlsx", f)], {}, None)
    p = dossier.profils[0]
    nums = [e["ligne"] for e in p.echantillon]
    assert len(nums) == 30 and nums[0] == t["t1"].numeros[0] and nums[-1] == t["t1"].numeros[-1]


def test_csv_en_tete_seul_et_lignes_courtes(tmp_path):
    f = tmp_path / "entete.csv"
    f.write_text("date,montant,libelle\n", encoding="utf-8")
    g = tmp_path / "irregulier.csv"
    g.write_text("date;montant;libelle;commentaire\n01/01/2025;100\n02/01/2025;200;x;y;z\n03/01/2025\n",
                 encoding="utf-8")
    dossier, _ = profiler([("entete.csv", f), ("irregulier.csv", g)], {}, None)
    for p in dossier.profils:
        assert all(0 <= c.part_vides <= 1 for c in p.colonnes)
        assert len(p.echantillon) == p.nb_lignes


def test_fiche_md_cp1252_et_txt_majuscule(tmp_path):
    fm = tmp_path / "FICHE.TXT"
    fm.write_bytes("Société : négoce — découvert autorisé 2 000 000 F".encode("cp1252"))
    d, _ = profiler([], {}, ("FICHE.TXT", fm))
    assert "négoce" in d.fiche_entreprise and "Société" in d.fiche_entreprise
    assert d.fichiers_ignores == []


def test_pdf_page_blanche_avec_espaces_et_corrompu(tmp_path):
    ok = _pdf(tmp_path / "ok.pdf", ["   ", "Bilan 2025 total actif 5 000 000"])
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 tronque")
    dossier, _ = profiler([("bad.pdf", bad), ("ok.pdf", ok)], {}, None)
    assert [p.fichier for p in dossier.profils] == ["ok.pdf"] and dossier.profils[0].id == "t1"
    assert dossier.profils[0].texte_pages[0] == PAGE_VIDE
    assert dossier.fichiers_ignores[0]["fichier"] == "bad.pdf"


def test_performance_20000_lignes_xlsx_et_csv(tmp_path):
    _random.seed(1)
    lignes = [["ETAT DES VENTES"], ["Date", "N° facture", "Client", "Désignation", "Qté", "PU", "Montant"]]
    for i in range(20_000):
        q, pu = _random.randint(1, 20), _random.choice([500, 1250, 7500, 15_000])
        lignes.append([date(2024, 1, 1) + timedelta(days=i // 60), f"FA{i:06d}", f"Client {i % 400}",
                       f"Article {i % 150}", q, pu, q * pu if i % 50 else f"{q * pu:,} F".replace(",", " ")])
        if i % 1000 == 999:
            lignes.append([None, None, None, "Sous-total", None, None, 1])
    fx = _xlsx(tmp_path / "gros.xlsx", {"Ventes": lignes})
    fc = tmp_path / "gros.csv"
    with open(fc, "w", newline="", encoding="utf-8") as h:
        w = _csv.writer(h, delimiter=";")
        for r in lignes[1:]:
            w.writerow(["" if v is None else (v.strftime("%d/%m/%Y") if isinstance(v, date) else v) for v in r])
    t0 = _time.perf_counter()
    dossier, tableaux = profiler([("gros.xlsx", fx), ("gros.csv", fc)], {}, None)
    duree = _time.perf_counter() - t0
    assert duree < 60, duree
    for p in dossier.profils:
        assert p.nb_lignes == 20_020 and len(p.echantillon) == 30
        assert len(p.lignes_suspectes_total) == 20
        assert {c.entete: c for c in p.colonnes}["Montant"].type_dominant == "nombre"
        assert {c.entete: c for c in p.colonnes}["Date"].date_max == (date(2024, 1, 1) + timedelta(days=19_999 // 60)).isoformat()
    assert len(profils_pour_ia(dossier)) <= 60_000


def test_profils_pour_ia_classeur_paie_12_mois_large(tmp_path):
    """Classeur de paie : 12 onglets mensuels × 35 colonnes. Rendu borné à ~60 000 caractères, 12 tableaux gardés."""
    _random.seed(7)
    wb = Workbook()
    wb.remove(wb.active)
    entetes = ["Matricule", "Nom et prénoms", "Fonction", "Site", "Catégorie", "Date embauche"] + \
              [f"Rubrique {k}" for k in range(29)]
    for m in range(12):
        ws = wb.create_sheet(f"Paie {m + 1:02d}-2025")
        ws.append([f"ETAT DE PAIE {m + 1:02d}/2025"])
        ws.append(entetes)
        for e in range(60):
            ws.append([f"M{e:04d}", f"Salarié {e} nom complet", "Agent commercial", f"Site {e % 3}", "C2",
                       date(2020, 1, 1) + timedelta(days=e * 20)] + [_random.randint(1000, 900_000) for _ in range(29)])
        ws.append(["TOTAL"] + [None] * 34)
    f = tmp_path / "paie.xlsx"
    wb.save(f)
    dossier, _ = profiler([("paie.xlsx", f)], {}, None)
    texte = profils_pour_ia(dossier)
    d = json.loads(texte)
    assert len(d["profils"]) == 12
    assert len(texte) <= 60_000 * 1.05, len(texte)

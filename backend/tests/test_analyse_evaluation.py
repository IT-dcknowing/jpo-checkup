"""Tests de l'export du mode évaluation (objets synthétiques construits à la main, faux entrepôt)."""
import json
import math
from datetime import date, datetime
from decimal import Decimal

from app.analyse.evaluation import MAX_LIGNES_REQUETE, colonnes_personnelles, exporter, json_sur
from app.analyse.modele import (Carte, ColonneCarte, Cout, Dossier, Fait, ProfilColonne, ProfilFeuille, Requete,
                                ResultatControle, TableauCarte)


# ------------------------------------------------------------------ fabriques
class FauxEntrepot:
    """Entrepôt minimal : requêtes stockées + pseudonymisation stable."""

    def __init__(self, requetes=None, pseudonymes=None):
        self.requetes = requetes if requetes is not None else []
        self.pseudonymes = pseudonymes if pseudonymes is not None else {}
        self.appels = []

    def pseudonymiser(self, valeur):
        self.appels.append(valeur)
        if valeur not in self.pseudonymes:
            self.pseudonymes[valeur] = f"Tiers_{len(self.pseudonymes) + 1:03d}"
        return self.pseudonymes[valeur]


def _col(i, entete, type_="texte", exemples=()):
    return ProfilColonne(index=i, entete=entete, type_dominant=type_, part_vides=0.0, distincts=len(exemples),
                         exemples=list(exemples))


def _profil_ventes():
    cols = [_col(0, "Date", "date", ["2025-01-02"]), _col(1, "Client", "texte", ["Durand Paul", "Kone Ami"]),
            _col(2, "Désignation", "texte", ["Article A"]), _col(3, "Montant", "nombre", ["1000"])]
    ech = [{"ligne": 4, "valeurs": ["2025-01-02", "Durand Paul", "Article A", "1000"]},
           {"ligne": 5, "valeurs": ["2025-01-03", "Kone Ami", "Article B", "2 500"]},
           {"ligne": 6, "valeurs": ["", "", "Sous-total", "3500"]}]
    return ProfilFeuille(id="t1", fichier="ventes.xlsx", feuille="Feuil1", format="xlsx", empreinte_fichier="abc",
                         nb_lignes=3, ligne_entete=3, titre="JOURNAL", colonnes=cols, echantillon=ech,
                         lignes_suspectes_total=[6])


def _profil_paie_sans_carte():
    cols = [_col(0, "Matricule", "texte", ["M-77"]), _col(1, "Nom et prénom", "texte", ["Yao Marc"]),
            _col(2, "Salaire brut", "nombre", ["250000"])]
    ech = [{"ligne": 2, "valeurs": ["M-77", "Yao Marc", "250000"]}]
    return ProfilFeuille(id="t2", fichier="paie.csv", feuille="paie", format="csv", empreinte_fichier="def",
                         nb_lignes=1, ligne_entete=1, titre="", colonnes=cols, echantillon=ech)


def _profil_pdf():
    return ProfilFeuille(id="t3", fichier="etats.pdf", feuille="", format="pdf", empreinte_fichier="ghi",
                         nb_lignes=0, ligne_entete=None, titre="", colonnes=[], echantillon=[],
                         texte_pages=["Client principal : Durand Paul — chiffre d'affaires 1 234 567"])


def _carte():
    return Carte(tableaux=[TableauCarte(id="t1", nature="ventes", confiance=0.9, justification="journal",
                                        colonnes=[ColonneCarte(index=0, nom="Date", role="date"),
                                                  ColonneCarte(index=1, nom="Client", role="tiers"),
                                                  ColonneCarte(index=2, nom="Désignation", role="designation"),
                                                  ColonneCarte(index=3, nom="Montant", role="montant")])])


def _dossier():
    return Dossier(profils=[_profil_ventes(), _profil_paie_sans_carte(), _profil_pdf()], fiche_entreprise="",
                   formulaire={"secteur": "commerce"},
                   fichiers_ignores=[{"fichier": "copie.xlsx", "raison": "doublon de ventes.xlsx"}])


def _requete(i, n):
    return Requete(id=f"q{i}", sql="SELECT date, montant FROM ventes", objectif="CA", tables=["ventes"],
                   colonnes=["date", "montant"], lignes=[[date(2025, 1, 1 + k % 28), Decimal("10.5")] for k in range(n)],
                   nb_lignes=n, duree_ms=3)


def _exporter(entrepot=None, carte=None, **kw):
    params = dict(dossier=_dossier(), carte=_carte() if carte is None else carte,
                  echanges=[{"etape": "carte", "prompt": "Ligne : Durand Paul achète", "reponse": {"ok": True}}],
                  controles=[ResultatControle(code="lignes_total", tableau="t1", reussi=False, message="ligne 6",
                                              mesure=float("nan"))],
                  entrepot=entrepot, faits=[Fait(id="ca", libelle="CA", valeur=3500.0, unite="FCFA", requete="q1")],
                  non_evaluables=[{"partie": "stocks", "raison": "absent"}],
                  couts=[Cout(modele="m-a", etape="carte", tokens_entree=100, tokens_sortie=10, usd=0.01),
                         Cout(modele="m-b", etape="etats_pdf", tokens_entree=50, usd=0.02)],
                  rapport_md="# Rapport", meta={"rapport_id": "r-1", "reference": "REF-1",
                                                "debut": datetime(2026, 1, 1, 10, 0, 0),
                                                "fin": "2026-01-01T10:00:42"})
    params.update(kw)
    return exporter(**params)


# ------------------------------------------------------------------ tests
def test_structure_et_serialisable():
    ent = FauxEntrepot(requetes=[_requete(1, 3)])
    exp = _exporter(ent)
    texte = json.dumps(exp, allow_nan=False)
    assert json.loads(texte) == exp
    for cle in ("version", "rapport_id", "reference", "moteur", "modeles", "profils", "fichiers_ignores", "carte",
                "echanges_ia", "controles", "requetes", "faits", "non_evaluables", "couts", "rapport_md", "duree_s"):
        assert cle in exp
    assert exp["rapport_id"] == "r-1" and exp["reference"] == "REF-1" and exp["moteur"] == "analyse"
    assert exp["duree_s"] == 42.0
    assert exp["fichiers_ignores"][0]["raison"].startswith("doublon")
    assert exp["controles"][0]["mesure"] is None           # NaN -> null
    assert exp["faits"][0]["requete"] == "q1"
    assert exp["carte"]["tableaux"][0]["nature"] == "ventes"
    assert exp["rapport_md"] == "# Rapport"


def test_requetes_tronquees_et_dates_iso():
    ent = FauxEntrepot(requetes={"q1": _requete(1, 120), "q2": _requete(2, 2)})
    exp = _exporter(ent)
    q1, q2 = exp["requetes"]
    assert len(q1["lignes"]) == MAX_LIGNES_REQUETE and q1["nb_lignes"] == 120 and q1["lignes_tronquees"]
    assert q1["lignes"][0] == ["2025-01-01", 10.5]
    assert q2["lignes_tronquees"] is False
    assert q1["sql"].startswith("SELECT") and q1["tables"] == ["ventes"] and q1["colonnes"] == ["date", "montant"]


def test_pseudonymisation_par_entrepot():
    ent = FauxEntrepot()
    exp = _exporter(ent)
    texte = json.dumps(exp)
    assert "Durand Paul" not in texte and "Kone Ami" not in texte
    t1 = exp["profils"][0]
    assert t1["echantillon"][0]["valeurs"][1] == ent.pseudonymes["Durand Paul"]
    assert t1["echantillon"][1]["valeurs"][1] == ent.pseudonymes["Kone Ami"]
    assert t1["echantillon"][0]["valeurs"][2] == "Article A"   # non personnel : intact
    assert t1["colonnes"][1]["exemples"] == [ent.pseudonymes["Durand Paul"], ent.pseudonymes["Kone Ami"]]
    assert t1["colonnes"][1]["pseudonymisee"] == "tiers"
    # textes PDF et échanges : valeurs connues remplacées
    assert ent.pseudonymes["Durand Paul"] in exp["profils"][2]["texte_pages"][0]
    assert ent.pseudonymes["Durand Paul"] in exp["echanges_ia"][0]["prompt"]
    # le dossier d'origine n'est pas modifié
    assert "Durand Paul" in json.dumps(json_sur(_dossier()))


def test_colonnes_sans_carte_masquees_par_entete():
    exp = _exporter(FauxEntrepot())
    t2 = exp["profils"][1]
    vals = t2["echantillon"][0]["valeurs"]
    assert "Yao Marc" not in vals and "M-77" not in vals
    assert vals[2] == "250000"


def test_sans_entrepot_pseudonymisation_locale_stable():
    exp = _exporter(None)
    texte = json.dumps(exp)
    assert "Durand Paul" not in texte and "Yao Marc" not in texte
    t1 = exp["profils"][0]
    assert t1["echantillon"][0]["valeurs"][1] == "Tiers_001"
    assert t1["colonnes"][1]["exemples"][0] == "Tiers_001"   # même valeur -> même pseudonyme
    assert exp["requetes"] == []
    roles = colonnes_personnelles(_profil_paie_sans_carte(), None)
    assert roles == {0: "matricule", 1: "salarie"}


def test_carte_dict_et_roles_non_personnels_respectes():
    carte = _carte().model_dump()
    exp = _exporter(FauxEntrepot(), carte=carte)
    assert exp["profils"][0]["echantillon"][0]["valeurs"][1].startswith("Tiers_")
    # rôle non personnel donné par la carte : colonne intacte
    assert exp["profils"][0]["echantillon"][0]["valeurs"][2] == "Article A"


def test_role_carte_prime_sur_indice_entete():
    p = _profil_ventes()
    carte = _carte()
    carte.tableaux[0].colonnes[1].role = "libelle"        # la carte dit « libelle » pour la colonne « Client »
    assert colonnes_personnelles(p, carte) == {}
    assert colonnes_personnelles(p, None) == {1: "tiers"}


def test_couts_total_et_modeles():
    exp = _exporter(FauxEntrepot())
    c = exp["couts"]
    assert math.isclose(c["total_usd"], 0.03)
    assert c["par_etape"] == {"carte": 0.01, "etats_pdf": 0.02}
    assert c["tokens"]["tokens_entree"] == 150
    assert exp["modeles"] == ["m-a", "m-b"]
    exp2 = _exporter(FauxEntrepot(), meta={"modeles": {"carte": "m-x"}, "duree_s": "7.5", "moteur": "autre"})
    assert exp2["modeles"] == {"carte": "m-x"} and exp2["duree_s"] == 7.5 and exp2["moteur"] == "autre"


def test_entrees_vides():
    exp = exporter(None, None, None, None, None, None, None, None, None, None)
    json.dumps(exp, allow_nan=False)
    assert exp["profils"] == [] and exp["couts"]["total_usd"] == 0 and exp["duree_s"] is None
    assert exp["carte"] is None and exp["rapport_md"] == ""


def test_json_sur_types_divers():
    class Num:
        def item(self):
            return 3

    out = json_sur({"d": date(2025, 2, 1), "t": (1, float("inf")), "s": {2}, "n": Num(), 4: b"x"})
    assert out == {"d": "2025-02-01", "t": [1, None], "s": [2], "n": 3, "4": "x"}
    json.dumps(out, allow_nan=False)


# ====================================================================== cas limites (revue adverse)
import time
import uuid

import numpy as np
import pandas as pd
from openpyxl import Workbook

from app.analyse.entrepot import Entrepot
from app.analyse.modele import CategorieCarte, PerimetreCarte
from app.analyse.profilage import profiler


def _tout(exp) -> str:
    """Export complet sérialisé (strict) : sert à chercher une fuite n'importe où."""
    return json.dumps(exp, ensure_ascii=False, allow_nan=False)


def _profil(id_, colonnes, lignes, *, feuille="Feuil1", titre="", fichier="f.xlsx", fmt="xlsx"):
    """Profil construit à la main : colonnes = [(entete, type)], lignes = valeurs texte (n° Excel à partir de 2)."""
    cols = []
    for j, (ent, typ) in enumerate(colonnes):
        ex = list(dict.fromkeys(str(r[j]) for r in lignes if j < len(r) and r[j] not in (None, "")))[:5]
        cols.append(_col(j, ent, typ, ex))
    ech = [{"ligne": i + 2, "valeurs": list(r)} for i, r in enumerate(lignes)]
    return ProfilFeuille(id=id_, fichier=fichier, feuille=feuille, format=fmt, empreinte_fichier=id_,
                         nb_lignes=len(lignes), ligne_entete=1, titre=titre, colonnes=cols, echantillon=ech)


def _tc(id_, nature, roles):
    return TableauCarte(id=id_, nature=nature, confiance=0.9, justification="test",
                        colonnes=[ColonneCarte(index=i, nom=r, role=r) for i, r in enumerate(roles) if r])


def _exp_simple(profils, carte=None, entrepot=None, echanges=None, pdf_pages=None):
    if pdf_pages is not None:
        profils = list(profils) + [ProfilFeuille(id=f"t{len(profils) + 1}", fichier="etats.pdf", feuille="",
                                                 format="pdf", empreinte_fichier="pdf", nb_lignes=0,
                                                 ligne_entete=None, titre="", colonnes=[], echantillon=[],
                                                 texte_pages=list(pdf_pages))]
    d = Dossier(profils=profils, fiche_entreprise="", formulaire={})
    return exporter(d, carte, echanges, [], entrepot, [], [], [], "", {})


# ------------------------------------------------------------------ intégration : profilage + entrepôt réels
def test_integration_classeur_sale_entrepot_reel(tmp_path):
    """Classeur réaliste (titre, total intercalé, montants texte, négatifs, 2 onglets) -> aucun nom en clair,
    pseudonymes cohérents entre échantillons exportés et résultats de requêtes."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Ventes 2025"
    ws.append(["JOURNAL DES VENTES — EXERCICE 2025"])
    ws.append([])
    ws.append(["Date", "Customer", "Désignation", "Qté", "PU", "Montant"])
    clients = ["Durand Paul", "Kone Ami", "Ets Bamba & Fils", "Yeo Salif"]
    for k in range(40):
        ws.append([datetime(2025, 1 + k % 12, 1 + k % 27), clients[k % 4], f"Article {k % 5}", 2, 500,
                   "1 250 000 F" if k == 3 else (-1000 if k == 7 else 1000)])
        if k == 19:
            ws.append([None, "TOTAL", None, None, None, 999999])
    ws2 = wb.create_sheet("Paie")
    ws2.append(["Matricule", "Nom et prénom", "Salaire brut"])
    for k, nom in enumerate(["Traore Awa", "Gnagne Luc", "Kouame Eric"]):
        ws2.append([f"M-{100 + k}", nom, 250000 + k])
    p = tmp_path / "classeur.xlsx"
    wb.save(p)
    dossier, tableaux = profiler([("classeur.xlsx", p)], {}, None)
    ids = [pr.id for pr in dossier.profils]
    assert len(ids) == 2
    carte = Carte(tableaux=[_tc(ids[0], "ventes", ["date", "tiers", "designation", "qte", "pu", "montant"]),
                            _tc(ids[1], "paie", ["matricule", "salarie", "brut"])])
    ent = Entrepot(dossier, tableaux, carte)
    ent.requete("SELECT tiers, SUM(montant) AS m FROM ventes GROUP BY tiers ORDER BY tiers", "CA par client")
    ent.requete("SELECT matricule, salarie, brut FROM paie ORDER BY matricule", "paie")
    exp = exporter(dossier, carte, [], [], ent, [], [], [], "", {"rapport_id": "r"})
    texte = _tout(exp)
    for nom in clients + ["Traore Awa", "Gnagne Luc", "Kouame Eric", "M-100", "M-101"]:
        assert nom not in texte, nom
    # même personne -> même pseudonyme dans les profils et dans les requêtes
    lignes_donnees = [e["valeurs"] for e in exp["profils"][0]["echantillon"] if e["valeurs"][0]]
    pseudos_ech = {v[1] for v in lignes_donnees}
    pseudos_req = {r[0] for r in exp["requetes"][0]["lignes"] if r[0]}
    assert pseudos_ech and pseudos_ech <= pseudos_req
    # la ligne de total reste lisible dans l'échantillon (preuve exigée par la spec) : « TOTAL » n'est pas un tiers
    total = [e["valeurs"] for e in exp["profils"][0]["echantillon"] if not e["valeurs"][0]]
    assert total and total[0][1] == "TOTAL"
    sal_ech = {e["valeurs"][1] for e in exp["profils"][1]["echantillon"]}
    sal_req = {r[1] for r in exp["requetes"][1]["lignes"]}
    assert sal_ech == sal_req
    # colonnes non personnelles intactes (montants texte, négatifs)
    vals = [e["valeurs"][5] for e in exp["profils"][0]["echantillon"] if len(e["valeurs"]) > 5]
    assert "1 250 000 F" in vals and "-1000" in vals


# ------------------------------------------------------------------ fuites de données personnelles
def test_matricule_au_format_pseudonyme_reste_masque():
    """Un matricule réel du type « EMP_1023 » ressemble à un pseudonyme : il doit quand même être masqué."""
    p = _profil("t1", [("Matricule", "texte"), ("Salarié", "texte"), ("Brut", "nombre")],
                [["EMP_1023", "Traore Awa", "250000"], ["MAT_00042", "Kone Ami", "180000"]])
    carte = Carte(tableaux=[_tc("t1", "paie", ["matricule", "salarie", "brut"])])
    texte = _tout(_exp_simple([p], carte, FauxEntrepot()))
    assert "EMP_1023" not in texte and "MAT_00042" not in texte


def test_entetes_pluriels_sans_carte_masques():
    """Tableau hors carte avec en-têtes au pluriel (« Clients », « Fournisseurs », « Employés », « Bénéficiaires »)."""
    p = _profil("t1", [("Date", "date"), ("Clients", "texte"), ("Fournisseurs", "texte"), ("Employés", "texte"),
                       ("Bénéficiaires", "texte"), ("Montant", "nombre")],
                [["2025-03-01", "Durand Paul", "Sotra Logistique", "Traore Awa", "Yeo Salif", "1 250 000"]])
    texte = _tout(_exp_simple([p], None, None))
    for nom in ("Durand Paul", "Sotra Logistique", "Traore Awa", "Yeo Salif"):
        assert nom not in texte, nom
    assert "1 250 000" in texte


def test_matricule_numerique_sans_carte_masque():
    """Matricules purement numériques (type dominant « nombre ») dans un tableau de paie hors carte."""
    p = _profil("t1", [("Matricule", "nombre"), ("Nom et prénom", "texte"), ("Salaire brut", "nombre")],
                [["104523", "Traore Awa", "250000"], ["104524", "Gnagne Luc", "300000"]])
    texte = _tout(_exp_simple([p], None, None))
    assert "Traore Awa" not in texte
    assert "104523" not in texte and "104524" not in texte


def test_titre_et_nom_de_feuille_contenant_un_tiers():
    """Relevé de compte client : le nom du tiers figure dans le titre et le nom d'onglet (un onglet par client)."""
    p = _profil("t1", [("Date", "date"), ("Client", "texte"), ("Montant", "nombre")],
                [["2025-01-05", "DURAND PAUL", "150000"], ["2025-02-05", "DURAND PAUL", "-20000"]],
                feuille="DURAND PAUL", titre="Relevé de compte client : DURAND PAUL")
    carte = Carte(tableaux=[_tc("t1", "creances", ["date", "tiers", "montant"])])
    texte = _tout(_exp_simple([p], carte, FauxEntrepot()))
    assert "DURAND PAUL" not in texte


def test_casse_differente_dans_echanges():
    """Le modèle reformule un nom avec une autre casse que dans les données : il ne doit pas sortir en clair."""
    p = _profil("t1", [("Date", "date"), ("Client", "texte"), ("Montant", "nombre")],
                [["2025-01-05", "DURAND PAUL", "150000"]])
    carte = Carte(tableaux=[_tc("t1", "ventes", ["date", "tiers", "montant"])])
    ech = [{"etape": "carte", "prompt": "…", "reponse": {"justification": "Le client Durand Paul domine les ventes."}}]
    texte = _tout(_exp_simple([p], carte, FauxEntrepot(), echanges=ech))
    assert "Durand Paul" not in texte


def test_carte_exportee_sans_noms_en_clair():
    """La carte (réponse validée du modèle) contient des exemples de libellés tirés des échantillons : ils doivent
    être anonymisés comme les échanges."""
    p = _profil("t1", [("Date", "date"), ("Bénéficiaire", "texte"), ("Libellé", "texte"), ("Débit", "nombre")],
                [["2025-01-05", "Kone Ami", "VIR SALAIRE Kone Ami", "150000"]])
    carte = Carte(tableaux=[_tc("t1", "banque", ["date", "tiers", "libelle", "debit"])],
                  categories_libelles=[CategorieCarte(motif="vir salaire", categorie="salaire",
                                                      exemples=["VIR SALAIRE Kone Ami"])])
    ech = [{"etape": "carte", "prompt": "VIR SALAIRE Kone Ami", "reponse": json_sur(carte)}]
    exp = _exp_simple([p], carte, FauxEntrepot(), echanges=ech)
    assert "Kone Ami" not in json.dumps(exp["echanges_ia"], ensure_ascii=False)
    assert "Kone Ami" not in json.dumps(exp["carte"], ensure_ascii=False)


# ------------------------------------------------------------------ remplacements abusifs dans les textes
def test_codes_tiers_courts_ne_corrompent_pas_les_montants():
    """Colonne tiers contenant des codes numériques (« 100 », « 001 ») : les montants des PDF et des échanges
    ne doivent pas être altérés."""
    p = _profil("t1", [("Date", "date"), ("Code client", "texte"), ("Montant", "nombre")],
                [["2025-01-05", "100", "2 100 000"], ["2025-01-06", "001", "1001000"]])
    carte = Carte(tableaux=[_tc("t1", "ventes", ["date", "tiers", "montant"])])
    ech = [{"etape": "carte", "prompt": "total 1001000 ; CA 2 100 000", "reponse": {}}]
    exp = _exp_simple([p], carte, None, echanges=ech, pdf_pages=["Chiffre d'affaires 2 100 000 ; résultat 1001000"])
    assert exp["profils"][1]["texte_pages"][0] == "Chiffre d'affaires 2 100 000 ; résultat 1001000"
    assert exp["echanges_ia"][0]["prompt"] == "total 1001000 ; CA 2 100 000"


def test_mot_total_dans_colonne_tiers_ne_masque_pas_les_textes():
    """Ligne de total dont le libellé « TOTAL » tombe dans la colonne tiers : les textes PDF (« TOTAL ACTIF »)
    et l'échantillon (preuve de la ligne de total) ne doivent pas être réécrits."""
    p = _profil("t1", [("Date", "date"), ("Client", "texte"), ("Montant", "nombre")],
                [["2025-01-05", "Durand Paul", "1000"], ["", "TOTAL", "1000"]])
    carte = Carte(tableaux=[_tc("t1", "ventes", ["date", "tiers", "montant"])])
    exp = _exp_simple([p], carte, FauxEntrepot(), pdf_pages=["TOTAL ACTIF 5 000 000"])
    assert exp["profils"][1]["texte_pages"][0] == "TOTAL ACTIF 5 000 000"


def test_nom_court_ne_casse_pas_les_mots():
    """Prénom court (« Ali ») : pas de remplacement à l'intérieur d'un mot (« Alimentation »)."""
    p = _profil("t1", [("Date", "date"), ("Client", "texte"), ("Montant", "nombre")],
                [["2025-01-05", "Ali", "1000"]])
    carte = Carte(tableaux=[_tc("t1", "ventes", ["date", "tiers", "montant"])])
    exp = _exp_simple([p], carte, None, pdf_pages=["Alimentation générale : 3 000 000"])
    assert exp["profils"][1]["texte_pages"][0] == "Alimentation générale : 3 000 000"


# ------------------------------------------------------------------ données sales qui doivent passer
def test_faux_positifs_entetes_numeriques_et_dates_intacts():
    """« Montant client », « Nb clients », « Date fournisseur » : colonnes non textuelles jamais masquées."""
    p = _profil("t1", [("Date fournisseur", "date"), ("Montant client", "nombre"), ("Nb clients", "nombre"),
                       ("Désignation", "texte")],
                [["05/01/2025", "1 250 000 F", "12", "Riz 25 kg"], ["", "-35 000", "3", "Sucre"]])
    exp = _exp_simple([p], None, None)
    assert exp["profils"][0]["echantillon"][0]["valeurs"] == ["05/01/2025", "1 250 000 F", "12", "Riz 25 kg"]
    assert exp["profils"][0]["echantillon"][1]["valeurs"] == ["", "-35 000", "3", "Sucre"]


def test_echantillon_irregulier_et_valeurs_non_textuelles():
    """Lignes plus courtes/longues que les colonnes, None, nombres dans la colonne tiers : pas d'erreur."""
    p = _profil("t1", [("Client", "texte"), ("Montant", "nombre")], [["Durand Paul", "10"]])
    p.echantillon = [{"ligne": 2, "valeurs": [None]}, {"ligne": 3, "valeurs": [411001, 5, "x", "y"]},
                     {"ligne": 4, "valeurs": []}, {"ligne": 5, "valeurs": ["  ", "1"]}]
    carte = Carte(tableaux=[_tc("t1", "ventes", ["tiers", "montant"])])
    exp = _exp_simple([p], carte, FauxEntrepot())
    texte = _tout(exp)
    assert "411001" not in texte
    assert exp["profils"][0]["echantillon"][1]["valeurs"][1:] == [5, "x", "y"]


def test_carte_dict_invalide_exportee_telle_quelle_et_masquage_par_entete():
    carte = {"tableaux": [{"id": "t1", "nature": "inconnue"}], "champ": float("nan")}
    p = _profil("t1", [("Client", "texte"), ("Montant", "nombre")], [["Durand Paul", "10"]])
    exp = _exp_simple([p], carte, None)
    texte = _tout(exp)
    assert "Durand Paul" not in texte and exp["carte"]["champ"] is None and exp["origine_carte"] is None


def test_meta_types_varies_serialisables():
    """rapport_id en UUID, modèles en ensemble, référence numérique : export sérialisable strict."""
    rid = uuid.uuid4()
    exp = exporter(None, None, None, None, None, None, None, None, None,
                   {"rapport_id": rid, "reference": 2025001, "modeles": {"m-a"}, "duree_s": 12})
    texte = _tout(exp)
    assert str(rid) in texte and exp["duree_s"] == 12.0


def test_json_sur_valeurs_pandas_numpy():
    """Résultats issus de pandas/numpy : NaT/NA -> null, entiers numpy -> int, datetime64 -> ISO."""
    out = json_sur([pd.NaT, pd.NA, np.float64("nan"), np.int64(7), np.datetime64("2025-01-02T00:00:00.000000000"),
                    pd.Timestamp("2025-03-04 05:06:07"), Decimal("-1250000.50")])
    assert out[0] is None and out[1] is None and out[2] is None and out[3] == 7
    assert str(out[4]).startswith("2025-01-02")
    assert out[5] == "2025-03-04T05:06:07" and out[6] == -1250000.5
    json.dumps(out, allow_nan=False)


def test_requetes_tuples_volumineuses_et_erreurs():
    """Résultat DuckDB (tuples), 200 lignes renvoyées pour 20 000, requête en erreur, dict sans nb_lignes."""
    gros = Requete(id="q1", sql="SELECT * FROM ventes", objectif="détail", tables=["ventes"], colonnes=["d", "m"],
                   lignes=[(date(2025, 1, 1), -1000.0 * k) for k in range(200)], nb_lignes=20000, duree_ms=40)
    err = Requete(id="q2", sql="SELECT x FROM y", objectif="faux", tables=[], colonnes=[], lignes=[], nb_lignes=0,
                  duree_ms=1, erreur="Catalog Error: Table y does not exist")
    brut = {"id": "q3", "sql": "SELECT 1", "lignes": [[1]] * 60}
    exp = _exp_simple([], None, FauxEntrepot(requetes=[gros, err, brut]))
    q1, q2, q3 = exp["requetes"]
    assert len(q1["lignes"]) == 50 and q1["nb_lignes"] == 20000 and q1["lignes_tronquees"]
    assert q1["lignes"][1] == ["2025-01-01", -1000.0]
    assert q2["erreur"].startswith("Catalog") and q2["lignes"] == [] and not q2["lignes_tronquees"]
    assert len(q3["lignes"]) == 50 and q3["nb_lignes"] == 60 and q3["lignes_tronquees"]
    _tout(exp)


def test_performance_gros_volume_de_pseudonymes():
    """20 000 tiers connus de l'entrepôt (gros journal de ventes), 3 échanges IA (~60 000 car. de prompt, carte de
    10 tableaux), 10 pages PDF : l'export doit rester rapide (< 3 s)."""
    ent = FauxEntrepot(pseudonymes={"tiers": {f"Client numero {k:05d}": f"Tiers_{k + 1:05d}" for k in range(20000)}})
    lignes = [["2025-01-05", f"Client numero {k:05d}", str(1000 + k)] for k in range(30)]
    p = _profil("t1", [("Date", "date"), ("Client", "texte"), ("Montant", "nombre")], lignes)
    carte = Carte(tableaux=[_tc("t1", "ventes", ["date", "tiers", "montant"])])
    prompt = (json.dumps(lignes) + " ") * 40
    ech = [{"etape": "carte", "prompt": prompt, "reponse": {"tableaux": [
        {"id": f"t{i}", "justification": "journal", "colonnes": [f"c{j}" for j in range(15)]} for i in range(10)]}}
        for _ in range(3)]
    pages = ["Chiffre d'affaires 1 250 000 ; Client numero 00007 " * 100] * 10
    t0 = time.perf_counter()
    exp = _exp_simple([p], carte, ent, echanges=ech, pdf_pages=pages)
    duree = time.perf_counter() - t0
    texte = _tout(exp)
    assert "Client numero 00007" not in texte
    assert duree < 3, f"{duree:.1f} s"

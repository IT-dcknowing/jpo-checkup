"""Tests de l'entrepôt DuckDB (jeux synthétiques générés dans tmp_path ou construits à la main)."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import pytest
from openpyxl import Workbook

from app.analyse import entrepot as mod
from app.analyse.entrepot import VUES, Entrepot
from app.analyse.lecture import lire_tableaux
from app.analyse.modele import (Carte, CategorieCarte, ColonneCarte, Dossier, LignesExclues, PerimetreCarte,
                                PeriodeCarte, RelationCarte, TableauCarte, Tableau)


# ------------------------------------------------------------------ aides
def _xlsx(path, feuilles: dict[str, list[list]]):
    wb = Workbook()
    wb.remove(wb.active)
    for nom, lignes in feuilles.items():
        ws = wb.create_sheet(nom)
        for r in lignes:
            ws.append(r)
    wb.save(path)
    return path


def _csv(path, lignes: list[list], sep=";"):
    path.write_text("\n".join(sep.join("" if v is None else str(v) for v in r) for r in lignes), encoding="utf-8")
    return path


def _cols(*roles, **opts) -> list[ColonneCarte]:
    """Colonnes de carte dans l'ordre des index ; None = colonne ignorée ; opts : {index: {champ: valeur}}."""
    out = []
    for i, r in enumerate(roles):
        if r is not None:
            out.append(ColonneCarte(index=i, nom=r, role=r, **opts.get(f"c{i}", {})))
    return out


def _tc(id_, nature, colonnes, **kw) -> TableauCarte:
    return TableauCarte(id=id_, nature=nature, confiance=0.9, justification="test", colonnes=colonnes, **kw)


def _dossier(formulaire=None) -> Dossier:
    return Dossier(profils=[], fiche_entreprise="", formulaire=formulaire or {})


def _un(ent, sql):
    r = ent.requete(sql, "test")
    assert r.erreur is None, r.erreur
    return r


# ------------------------------------------------------------------ ventes
@pytest.fixture
def ventes(tmp_path):
    p = _xlsx(tmp_path / "journal.xlsx", {"Ventes": [
        ["JOURNAL DES VENTES 2025"],
        ["Date", "N° facture", "Client", "Désignation", "Qté", "P.U.", "Montant HT"],
        [datetime(2025, 1, 3), "F001", "Durand Paul", "Article A", 2, 1000, "2 000"],
        [datetime(2025, 1, 3), "F002", "Kone Ami", "Article B", 1, 1500, 1500],
        [datetime(2025, 1, 4), "F002", "Kone Ami", "Article B", 1, 1500, 1500],      # pièce répétée, autre date
        [None, None, None, "Total janvier", None, None, 5000],                        # total (mot)
        [datetime(2025, 2, 1), "F003", "Durand Paul", "Article A", 1, 1000, "1 000"],
        [datetime(2025, 2, 1), "F003", "Durand Paul", "Article A", 1, 1000, "1 000"],  # doublon exact
        [datetime(2025, 2, 2), "AV01", "Kone Ami", "Avoir sur F002", 1, 500, 500],     # avoir
        [datetime(2025, 2, 3), "F004", "Traore Ali", "Article C", 1, 800, -800],        # retour négatif
        [datetime(2025, 2, 4), "F005", "Traore Ali", "Article C", 3, 800, None],        # montant à calculer
        [None, None, None, None, None, None, 1700],                                    # sous-total sans texte
        [datetime(2025, 2, 5), "F006", "Client test", "Ligne à exclure", 1, 99, 99],
    ]})
    t1, = lire_tableaux("journal.xlsx", p)
    exclue = t1.numeros[-1]
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "piece", "tiers", "designation", "qte", "pu", "montant_ht"),
                                lignes_a_exclure=LignesExclues(autres=[exclue]), perimetre=PerimetreCarte(site="Site 1"))])
    return Entrepot(_dossier(), {"t1": t1}, carte), t1


def test_ventes_retraits_doublons_retours(ventes):
    ent, t1 = ventes
    r = _un(ent, "SELECT count(*), sum(montant) FILTER (WHERE NOT doublon AND NOT est_retour), "
                 "sum(montant) FILTER (WHERE est_retour), count(*) FILTER (WHERE doublon) FROM ventes")
    nb, ca, retours, doublons = r.lignes[0]
    assert nb == 8                       # 11 lignes - total mot - sous-total - exclue
    assert ca == 2000 + 1500 + 1500 + 1000 + 2400
    assert retours == 500 + 800          # stockés positifs
    assert doublons == 1
    assert ent.lignes_retirees["t1"]["carte"] == [t1.numeros[-1]]
    assert len(ent.lignes_retirees["t1"]["totaux"]) == 2
    # table brute : toutes les lignes, retraits signalés
    b = _un(ent, "SELECT _retrait, count(*) FROM brut_t1 GROUP BY 1 ORDER BY 1 NULLS FIRST")
    assert b.lignes == [[None, 8], ["exclue_carte", 1], ["total_detecte", 2]]
    assert _un(ent, "SELECT DISTINCT site FROM ventes").lignes == [["Site 1"]]
    assert _un(ent, "SELECT min(ligne) FROM ventes").lignes[0][0] == 3      # n° Excel


def test_ventes_remise_taux_et_ttc(tmp_path):
    lignes = [["date", "qte", "pu", "remise %", "montant ttc", "tva"],
              ["2025-03-01", 2, 1000, 10, 2360, 360],
              ["2025-03-02", 1, 1000, 0, None, None]]
    t, = lire_tableaux("v.csv", _csv(tmp_path / "v.csv", lignes))
    cols = _cols("date", "qte", "pu", "remise_taux", "montant", "tva", c4={"ht_ttc": "TTC"})
    carte = Carte(tableaux=[_tc("t1", "ventes", cols)])
    # non assujetti : TTC conservé
    e1 = Entrepot(_dossier(), {"t1": t}, carte)
    assert _un(e1, "SELECT montant, remise FROM ventes ORDER BY ligne").lignes == [[2360.0, 0.1], [1000.0, 0.0]]
    # assujetti : TTC - TVA ; montant calculé qte × pu × (1 - taux)
    t, = lire_tableaux("v.csv", tmp_path / "v.csv")
    e2 = Entrepot(_dossier({"tva_assujetti": True}), {"t1": t}, carte)
    assert _un(e2, "SELECT montant FROM ventes ORDER BY ligne").lignes == [[2000.0], [1000.0]]
    assert any("TTC" in n for n in e2.notes)


# ------------------------------------------------------------------ pseudonymisation
def test_pseudonymisation_stable(tmp_path):
    p = _xlsx(tmp_path / "f.xlsx", {
        "Achats": [["Date", "Fournisseur", "Montant"], [datetime(2025, 1, 2), "Société Alpha", 1000],
                   [datetime(2025, 1, 3), "SOCIETE ALPHA ", 2000], [datetime(2025, 1, 4), "Beta SARL", 300]],
        "Paie": [["Mois", "Matricule", "Nom", "Salaire brut", "Net"], ["03/2025", 1023, "Yao Marc", 200000, 180000],
                 ["mars 2025", 1024, "Société Alpha", 150000, 140000]]})
    ta, tp = lire_tableaux("f.xlsx", p)
    carte = Carte(tableaux=[_tc("t1", "achats", _cols("date", "tiers", "montant")),
                            _tc("t2", "paie", _cols("periode", "matricule", "salarie", "brut", "net"))])
    ent = Entrepot(_dossier(), {"t1": ta, "t2": tp}, carte)
    assert _un(ent, "SELECT tiers FROM achats ORDER BY ligne").lignes == [["Tiers_001"], ["Tiers_001"], ["Tiers_002"]]
    paie = _un(ent, "SELECT periode, matricule, salarie FROM paie ORDER BY ligne").lignes
    assert paie == [["2025-03-01", "Matricule_001", "Salarie_001"], ["2025-03-01", "Matricule_002", "Salarie_002"]]
    assert ent.pseudonymes["tiers"]["Société Alpha"] == "Tiers_001"
    assert ent.pseudonymes["matricule"]["1023"] == "Matricule_001"
    assert ent.pseudonymiser("societe alpha") == "Tiers_001"         # stable, insensible casse/accents
    # aucune valeur d'origine dans les tables brutes
    brut = _un(ent, "SELECT * FROM brut_t1").lignes + _un(ent, "SELECT * FROM brut_t2").lignes
    assert not any("Alpha" in str(v) or "Yao" in str(v) or v == "1023" for r in brut for v in r)


# ------------------------------------------------------------------ trésorerie et virements internes
def test_tresorerie_et_virements(tmp_path):
    banque = _xlsx(tmp_path / "releve.xlsx", {"Releve": [
        ["Date", "Libellé", "Débit", "Crédit", "Solde"],
        [datetime(2025, 5, 2), "Remise chèque client", None, "500 000", 1500000],
        [datetime(2025, 5, 3), "VIREMENT VERS CAISSE", 200000, None, 1300000],
        [datetime(2025, 5, 10), "Virement interne sans retour", 75000, None, 1225000],
        [datetime(2025, 5, 12), "Frais tenue de compte", 5000, None, 1220000],
        [datetime(2025, 5, 20), "Achat marchandises", 90000, None, 1130000],
    ]})
    caisse = _csv(tmp_path / "caisse.csv", [
        ["Date", "Libelle", "Entree", "Sortie"],
        ["04/05/2025", "Approvisionnement", "199 999,5", ""],      # contrepartie à 0,5 F et 1 jour
        ["10/05/2025", "Encaissement", "76 500", ""],              # montant différent : pas de lien
        ["27/05/2025", "Depot banque", "", "90 000"],              # 7 jours : hors fenêtre
    ])
    tb, = lire_tableaux("releve.xlsx", banque)
    tcai, = lire_tableaux("caisse.csv", caisse)
    carte = Carte(
        tableaux=[_tc("t1", "banque", _cols("date", "libelle", "debit", "credit", "solde"),
                      perimetre=PerimetreCarte(compte="Banque principale")),
                  _tc("t2", "caisse", _cols("date", "libelle", "entree", "sortie"))],
        categories_libelles=[CategorieCarte(motif="virement", categorie="virement_interne"),
                             CategorieCarte(motif="virement vers caisse", categorie="virement_interne"),
                             CategorieCarte(motif="frais", categorie="frais_bancaires", tableaux=["t1"]),
                             CategorieCarte(motif="remise cheque", categorie="encaissement_client")])
    ent = Entrepot(_dossier(), {"t1": tb, "t2": tcai}, carte)
    r = _un(ent, "SELECT src, compte, nature_compte, entree, sortie, categorie_libelle, virement_interne "
                 "FROM tresorerie ORDER BY src, ligne").lignes
    assert r[0] == ["t1", "Banque principale", "banque", 500000.0, None, "encaissement_client", False]
    assert r[1][4] == 200000.0 and r[1][6] is True                 # sortie banque appariée
    assert r[2][6] is True                                         # catégorisé sans contrepartie : marqué…
    assert r[3][5] == "frais_bancaires"
    assert r[5][:2] == ["t2", "caisse:caisse.csv›caisse"]
    assert r[5][3] == 199999.5 and r[5][6] is True                  # contrepartie non catégorisée marquée
    assert r[6][6] is False and r[7][6] is False
    # … et signalé
    assert [a["ligne"] for a in ent.anomalies if a["type"] == "virement_sans_contrepartie"] == [4]
    assert _un(ent, "SELECT min(entree), min(sortie) FROM tresorerie").lignes[0][0] >= 0


def test_virement_par_relation_et_sens(tmp_path):
    def t(fichier, lignes):
        return Tableau(fichier=fichier, feuille="F", entetes=[], entetes_bruts=[], lignes=lignes,
                       numeros=list(range(2, 2 + len(lignes))))
    b = t("b.xlsx", [[date(2025, 6, 1), "OP 1", -30000], [date(2025, 6, 9), "OP 2", -1000]])
    m = t("m.xlsx", [[date(2025, 6, 3), "OP 3", 30000, None], [date(2025, 6, 9), "OP 4", 1000, None]])
    m2 = t("m2.xlsx", [[date(2025, 6, 9), "OP 5", 1000]])      # hors relation : jamais apparié
    carte = Carte(tableaux=[_tc("t1", "banque", _cols("date", "libelle", "montant", c2={"signe": "signe"})),
                            _tc("t2", "mobile_money", _cols("date", "libelle", "debit", "credit",
                                                            c2={"signe": "positif_entree"})),
                            _tc("t3", "caisse", _cols("date", "libelle", "montant", c2={"signe": "positif_sortie"}))],
                  relations=[RelationCarte(type="virement_interne", tableaux=["t1", "t2"], justification="x")])
    ent = Entrepot(_dossier(), {"t1": b, "t2": m, "t3": m2}, carte)
    r = _un(ent, "SELECT src, entree, sortie, virement_interne FROM tresorerie ORDER BY src, ligne").lignes
    assert r == [["t1", None, 30000.0, True], ["t1", None, 1000.0, True],
                 ["t2", 30000.0, None, True], ["t2", 1000.0, None, True],
                 ["t3", None, 1000.0, False]]
    assert ent.anomalies == []


# ------------------------------------------------------------------ autres vues
def test_autres_vues_et_etats_pdf(tmp_path):
    p = _xlsx(tmp_path / "compta.xlsx", {
        "Balance": [["Compte", "Intitulé", "Débit", "Crédit"], [601000, "Achats", "3 000 000", 0],
                    ["701 000", "Ventes", 0, "9 000 000"], [None, "Total classe 6 et 7", 3000000, 9000000]],
        "Stock": [["Référence", "Désignation", "Qté", "PU"], ["R1", "Produit 1", 10, 250], ["R2", "Produit 2", 4, 100]],
        "Emprunt": [["Échéance", "Capital", "Intérêts", "Mensualité", "Restant dû"],
                    [datetime(2025, 1, 31), 90000, 10000, 100000, 910000]],
        "Bilan": [["Poste", "Exercice N", "Exercice N-1"], ["Capitaux propres", "5 000 000", "4 000 000"],
                  ["TOTAL ACTIF", 12000000, 11000000]],
        "Depenses": [["Date", "Libellé", "Montant"], [datetime(2025, 2, 1), "Loyer février", 300000],
                     [datetime(2025, 2, 1), "Loyer février", 300000]]})
    tb, ts, te, tbil, td = lire_tableaux("compta.xlsx", p)
    carte = Carte(tableaux=[
        _tc("t1", "balance", _cols("compte", "intitule", "debit", "credit"), exercice="2025"),
        _tc("t2", "stock", _cols("reference", "designation", "qte", "pu"), date_inventaire="2025-12-31"),
        _tc("t3", "emprunt", _cols("echeance", "capital", "interets", "mensualite", "capital_restant")),
        _tc("t4", "bilan", _cols("poste", "valeur_n", "valeur_n1"), periode=PeriodeCarte(fin="2024-12-31")),
        _tc("t5", "depenses", _cols("date", "libelle", "montant"))],
        categories_libelles=[CategorieCarte(motif="loyer", categorie="loyer")])
    ent = Entrepot(_dossier(), {"t1": tb, "t2": ts, "t3": te, "t4": tbil, "t5": td}, carte)
    assert _un(ent, "SELECT exercice, compte, solde FROM balance ORDER BY ligne").lignes == [
        ["2025", "601000", 3000000.0], ["2025", "701000", -9000000.0]]
    assert _un(ent, "SELECT sum(valeur), max(date_inventaire) FROM stocks").lignes == [[2900.0, "2025-12-31"]]
    assert _un(ent, "SELECT date, capital_restant, mensualite FROM emprunts").lignes == [["2025-01-31", 910000.0, 100000.0]]
    assert _un(ent, "SELECT exercice, poste, valeur_n, valeur_n1 FROM etats_financiers").lignes == [
        ["2024", "Capitaux propres", 5000000.0, 4000000.0]]
    assert _un(ent, "SELECT categorie_libelle, doublon FROM depenses ORDER BY ligne").lignes == [
        ["loyer", False], ["loyer", True]]
    n = ent.ajouter_etats_financiers("t9", [{"exercice": "2025", "poste": "resultat_net", "valeur_n": 1.5e6,
                                             "valeur_n1": None, "page": 2}])
    assert n == 1
    assert _un(ent, "SELECT src, ligne, poste FROM etats_financiers WHERE src = 't9'").lignes == [["t9", 2, "resultat_net"]]


def test_tableau_hors_carte_et_vues_vides(tmp_path):
    t, = lire_tableaux("x.csv", _csv(tmp_path / "x.csv", [["a", "b"], ["x", 1], ["y", 2]], sep=","))
    ent = Entrepot(_dossier(), {"t1": t}, Carte(tableaux=[]))
    assert _un(ent, "SELECT count(*) FROM brut_t1").lignes == [[2]]
    for vue in VUES:
        assert _un(ent, f"SELECT count(*) FROM {vue}").lignes == [[0]]
    types = dict(_un(ent, "SELECT column_name, data_type FROM information_schema.columns "
                          "WHERE table_name = 'ventes'").lignes)
    assert types["date"] == "DATE" and types["montant"] == "DOUBLE" and types["est_retour"] == "BOOLEAN"


# ------------------------------------------------------------------ requêtes
def test_requete_bridee(ventes):
    ent, _ = ventes
    ok = ent.requete("WITH x AS (SELECT * FROM ventes) SELECT count(*) AS n FROM x", "compter")
    assert ok.id == "q1" and ok.erreur is None and ok.colonnes == ["n"] and ok.tables == ["ventes"]
    for sql in ("DROP VIEW ventes", "SELECT 1; DROP VIEW ventes", "INSERT INTO n_ventes (src) VALUES ('x')",
                "SET enable_external_access = true", "PRAGMA database_list", "SELECT * FROM inconnue",
                "SELECT * FROM read_csv('/etc/hosts')", "COPY ventes TO 'x.csv'"):
        r = ent.requete(sql, "refus", auteur="ia")
        assert r.erreur, sql
        assert r.auteur == "ia"
    assert list(ent.requetes) == [f"q{i}" for i in range(1, 10)]
    assert _un(ent, "SELECT count(*) FROM ventes").lignes == [[8]]      # rien n'a été modifié
    grand = _un(ent, "SELECT * FROM range(1234)")
    assert len(grand.lignes) == 200 and grand.nb_lignes == 1234


def test_requete_delai(ventes, monkeypatch):
    ent, _ = ventes
    monkeypatch.setattr(mod, "DELAI_S", 0.3)
    t0 = time.perf_counter()
    r = ent.requete("SELECT count(*) FROM range(100000000) a, range(100000) b", "trop long")
    assert r.erreur and "délai" in r.erreur
    assert time.perf_counter() - t0 < 5
    assert ent.requete("SELECT 1", "après").erreur is None           # la connexion reste utilisable


def test_schema_vues(ventes):
    ent, _ = ventes
    s = ent.schema_vues()
    for vue, cols in VUES.items():
        assert f"- {vue} (" in s
        for c, _, _ in cols:
            assert f"    {c} " in s
    assert "brut_t1 [ventes]" in s and "c2=Client" in s


# ------------------------------------------------------------------ performance
def test_performance_20000_lignes():
    d0 = date(2024, 1, 1)
    lignes = [[d0 + timedelta(days=i % 600), f"F{i}", f"Client {i % 700}", f"Article {i % 50}", i % 5 + 1, "1 500",
               f"{(i % 5 + 1) * 1500:,}".replace(",", " ")] for i in range(20_000)]
    t = Tableau(fichier="gros.csv", feuille="gros", entetes=[f"c{i}" for i in range(7)], entetes_bruts=[],
                lignes=lignes, numeros=list(range(2, 20_002)))
    tr = Tableau(fichier="b.csv", feuille="b", entetes=[], entetes_bruts=[],
                 lignes=[[d0 + timedelta(days=i % 300), f"Virement {i}", 1000 + i % 97, None] for i in range(5000)]
                 + [[d0 + timedelta(days=i % 300), f"Approvisionnement {i}", None, 1000 + i % 97] for i in range(5000)],
                 numeros=list(range(2, 10_002)))
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "piece", "tiers", "designation", "qte", "pu", "montant")),
                            _tc("t2", "banque", _cols("date", "libelle", "debit", "credit"))],
                  categories_libelles=[CategorieCarte(motif="virement", categorie="virement_interne")])
    t0 = time.perf_counter()
    ent = Entrepot(_dossier(), {"t1": t, "t2": tr}, carte)
    duree = time.perf_counter() - t0
    assert _un(ent, "SELECT count(*), sum(montant) FROM ventes").lignes[0][0] == 20_000
    assert duree < 4, duree      # objectif < 2 s ; marge pour les machines de CI


# ====================================================================== cas limites (revue adverse)
def _t(lignes, fichier="f.xlsx", feuille="F", debut=2) -> Tableau:
    """Tableau construit à la main (sans en-tête), n° Excel consécutifs à partir de `debut`."""
    return Tableau(fichier=fichier, feuille=feuille, entetes=[], entetes_bruts=[], lignes=lignes,
                   numeros=list(range(debut, debut + len(lignes))))


def test_classeur_sale_plusieurs_onglets(tmp_path):
    """En-têtes anglais/abrégés, titres, montants texte « 1 250 000 F », dates texte, totaux intercalés, vides."""
    p = _xlsx(tmp_path / "export.xlsx", {
        "Sales": [["SALES REPORT"], ["Period: Q1 2025"], [],
                  ["Date", "Inv#", "Customer", "Item", "Qty", "U.P.", "Amount"],
                  ["02/01/2025", "INV-1", "Client Un", "Produit X", "2", "625 000 F", "1 250 000 F"],
                  ["2025-01-02", "INV-2", "Client Deux", "Produit Y", 1, 300000, "300.000"],
                  [None, None, None, "Sous-total", None, None, "1 550 000 F"],
                  ["15 févr. 2025", "INV-3", "", "Produit X", 1, 625000, None],
                  [None, None, None, None, None, None, None],
                  ["TOTAL GENERAL", None, None, None, None, None, "2 175 000"]],
        "Expenses": [["Dt", "Lib.", "Mt"], [datetime(2025, 1, 5), "Loyer janvier", "(150 000)"],
                     [datetime(2025, 1, 6), "Electricite", "45 000 FCFA"], [None, "Total", "195 000"]]})
    tv, td = lire_tableaux("export.xlsx", p)
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "piece", "tiers", "designation", "qte", "pu", "montant")),
                            _tc("t2", "depenses", _cols("date", "libelle", "montant"))])
    ent = Entrepot(_dossier(), {"t1": tv, "t2": td}, carte)
    r = _un(ent, "SELECT date, montant, est_retour FROM ventes ORDER BY ligne").lignes
    assert r == [["2025-01-02", 1250000.0, False], ["2025-01-02", 300000.0, False], ["2025-02-15", 625000.0, False]]
    assert len(ent.lignes_retirees["t1"]["totaux"]) == 2
    d = _un(ent, "SELECT count(*), sum(abs(montant)) FROM depenses").lignes
    assert d == [[2, 195000.0]]


def test_balance_report_a_nouveau_n_est_pas_un_total():
    """Le compte 12 « Report à nouveau » (SYSCOHADA) est un compte, pas une ligne de total : il doit rester."""
    bal = _t([["101000", "Capital social", 0, "10 000 000"],
              ["121000", "Report à nouveau créditeur", 0, "2 500 000"],
              ["601000", "Achats de marchandises", "3 000 000", 0],
              [None, "Total classe 6", "3 000 000", 0]])
    bil = _t([["Capital", "10 000 000", "10 000 000"], ["Report à nouveau", "2 500 000", "1 800 000"],
              ["Résultat net de l'exercice", "900 000", "700 000"]], feuille="Bilan")
    carte = Carte(tableaux=[_tc("t1", "balance", _cols("compte", "intitule", "debit", "credit"), exercice="2025"),
                            _tc("t2", "bilan", _cols("poste", "valeur_n", "valeur_n1"), exercice="2025")])
    ent = Entrepot(_dossier(), {"t1": bal, "t2": bil}, carte)
    assert [r[0] for r in _un(ent, "SELECT compte FROM balance ORDER BY ligne").lignes] == ["101000", "121000", "601000"]
    assert _un(ent, "SELECT sum(solde) FILTER (WHERE compte LIKE '1%') FROM balance").lignes == [[-12500000.0]]
    assert _un(ent, "SELECT count(*) FROM etats_financiers").lignes == [[3]]


def test_total_date_avec_mot_et_somme_retire():
    """Total mensuel daté (fin de mois) : mot de total ET montant = somme du bloc ⇒ ligne de total retirée."""
    v = _t([[date(2025, 1, 3), "F1", "Article A", 1000],
            [date(2025, 1, 20), "F2", "Article B", 2500],
            [date(2025, 1, 31), None, "TOTAL JANVIER", 3500],
            [date(2025, 2, 2), "F3", "Article A", 1000]])
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "piece", "designation", "montant"))])
    ent = Entrepot(_dossier(), {"t1": v}, carte)
    assert _un(ent, "SELECT count(*), sum(montant) FROM ventes").lignes == [[3, 4500.0]]
    assert ent.lignes_retirees["t1"]["totaux"] == [4]


def test_ligne_detail_sans_date_mot_total_gardee():
    """Facture multi-lignes (date sur la 1re ligne seulement) : un article dont la désignation finit par « total »
    est une ligne de détail (pièce, qté, PU), pas une ligne de total."""
    v = _t([[date(2025, 3, 4), "F10", "Vidange moteur", 1, 15000, 15000],
            [None, "F10", "Lavage total", 1, 5000, 5000],
            [None, "F10", "Filtre à huile", 2, 2500, 5000],
            [date(2025, 3, 5), "F11", "Vidange moteur", 1, 15000, 15000]])
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "piece", "designation", "qte", "pu", "montant"))])
    ent = Entrepot(_dossier(), {"t1": v}, carte)
    r = _un(ent, "SELECT ligne, date, montant FROM ventes ORDER BY ligne").lignes
    assert [x[0] for x in r] == [2, 3, 4, 5]
    assert all(x[1] is not None for x in r)          # date héritée de la pièce
    assert sum(x[2] for x in r) == 40000.0


def test_tresorerie_debit_affiche_negatif():
    """Export bancaire qui affiche les débits avec un signe moins : un débit reste une sortie (≥ 0)."""
    b = _t([[date(2025, 5, 2), "Paiement fournisseur", "-200 000", None, "800 000"],
            [date(2025, 5, 3), "Remise de chèques", None, "300 000", "1 100 000"],
            [date(2025, 5, 4), "Frais", -2500, None, "1 097 500"]])
    carte = Carte(tableaux=[_tc("t1", "banque", _cols("date", "libelle", "debit", "credit", "solde"))])
    ent = Entrepot(_dossier(), {"t1": b}, carte)
    assert _un(ent, "SELECT entree, sortie FROM tresorerie ORDER BY ligne").lignes == [
        [None, 200000.0], [300000.0, None], [None, 2500.0]]


def test_remise_taux_texte_pourcentage():
    """Colonne remise_taux saisie en texte « 10 % » : montant manquant = qte × pu × (1 − 0,10)."""
    v = _t([[date(2025, 4, 1), 2, "1 000", "10%"], [date(2025, 4, 2), 4, 500, "5 %"], [date(2025, 4, 3), 1, 800, None]])
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "qte", "pu", "remise_taux"))])
    ent = Entrepot(_dossier(), {"t1": v}, carte)
    assert _un(ent, "SELECT remise, montant FROM ventes ORDER BY ligne").lignes == [
        [0.1, 1800.0], [0.05, 1900.0], [None, 800.0]]


def test_remise_montant_affichee_negative():
    """Remise en montant affichée en négatif (export de caisse) : c'est une réduction, montant = qte × pu − |remise|."""
    v = _t([[date(2025, 4, 1), "T1", 2, 1000, -200], [date(2025, 4, 2), "T2", 1, 1000, 100]])
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "piece", "qte", "pu", "remise"))])
    ent = Entrepot(_dossier(), {"t1": v}, carte)
    assert _un(ent, "SELECT montant, est_retour FROM ventes ORDER BY ligne").lignes == [[1800.0, False], [900.0, False]]


def test_sans_entete_premiere_ligne_est_une_donnee(tmp_path):
    """Fichier sans en-tête : la carte le dit (`sans_entete`) ; la 1re ligne est une vente, et le nom du client
    qu'elle porte ne doit pas sortir en clair (schéma envoyé à l'agent, table brute)."""
    p = _csv(tmp_path / "brut.csv", [["03/01/2025", "F001", "Durand Paul", "Article A", 2000],
                                     ["04/01/2025", "F002", "Kone Ami", "Article B", 1500],
                                     ["05/01/2025", "F003", "Yao Marc", "Article C", 700]])
    t, = lire_tableaux("brut.csv", p)
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "piece", "tiers", "designation", "montant"),
                                sans_entete=True)])
    ent = Entrepot(_dossier(), {"t1": t}, carte)
    assert _un(ent, "SELECT count(*), sum(montant) FROM ventes").lignes == [[3, 4200.0]]
    assert "Durand" not in ent.schema_vues()


def test_paie_periodes_variees_et_vendeur_pseudonymise():
    p = _t([["Janvier 2025", "M01", "Yao Marc", "250 000", "220 000"],
            ["Févr.-25", "M01", "YAO MARC", 250000, 220000],
            [datetime(2025, 3, 31), "M02", "Kone Ami", 180000, 160000],
            ["2025/04", "M02", "Koné Ami", 180000, 160000]])
    v = _t([[date(2025, 1, 2), "T1", "Vendeur Alpha", 1000]], feuille="V")
    carte = Carte(tableaux=[_tc("t1", "paie", _cols("periode", "matricule", "salarie", "brut", "net")),
                            _tc("t2", "ventes", _cols("date", "piece", "vendeur", "montant"))])
    ent = Entrepot(_dossier(), {"t1": p, "t2": v}, carte)
    r = _un(ent, "SELECT periode, salarie FROM paie ORDER BY ligne").lignes
    assert [x[0] for x in r] == ["2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01"]
    assert r[0][1] == r[1][1] and r[2][1] == r[3][1] and r[0][1] != r[2][1]
    assert _un(ent, "SELECT vendeur FROM ventes").lignes == [["Vendeur_001"]]
    assert not any("Alpha" in str(c) for row in _un(ent, "SELECT * FROM brut_t2").lignes for c in row)


def test_mots_cles_faux_positifs_retour():
    """« Savoir-faire », « Retournement » : pas des retours ; « Avoir n° 12 » en est un."""
    v = _t([[date(2025, 1, 2), "F1", "Formation savoir-faire", 1000],
            [date(2025, 1, 3), "F2", "Retournement de matelas", 2000],
            [date(2025, 1, 4), "AV12", "Avoir n° 12", 500]])
    carte = Carte(tableaux=[_tc("t1", "ventes", _cols("date", "piece", "designation", "montant"))])
    ent = Entrepot(_dossier(), {"t1": v}, carte)
    assert _un(ent, "SELECT est_retour FROM ventes ORDER BY ligne").lignes == [[False], [False], [True]]


def test_requete_contournements_refuses(ventes):
    ent, _ = ventes
    for sql in ("SELECT * FROM query('DROP VIEW ventes')", "SELECT * FROM glob('/etc/*')",
                "SELECT * FROM read_text('/etc/hostname')", "FROM ventes", "EXPLAIN SELECT 1",
                "ATTACH ':memory:' AS x", "-- commentaire\nDELETE FROM n_ventes"):
        assert ent.requete(sql, "contournement", auteur="ia").erreur, sql
    assert _un(ent, "SELECT count(*) FROM ventes").lignes == [[8]]
    import json
    r = _un(ent, "SELECT date, montant, sum(montant) OVER () AS tot, DATE '2025-01-01' + INTERVAL 1 DAY AS ts, "
                 "qte::DECIMAL(10,2) AS d, sum(qte::INTEGER) OVER () AS h FROM ventes LIMIT 1")
    json.dumps(r.lignes)                                     # résultat sérialisable (trace, export évaluation)


def test_tva_regime_reel_et_carte_assujetti():
    """Formulaire réel (régime réel normal, non exonéré) et carte « assujetti » : TTC converti en HT."""
    v = _t([[date(2025, 1, 2), 11800, 1800]])
    carte = Carte(entreprise={"tva_assujetti": True},
                  tableaux=[_tc("t1", "ventes", _cols("date", "montant", "tva", c1={"ht_ttc": "TTC"}))])
    ent = Entrepot(_dossier({"regime_fiscal": "REEL_NORMAL", "tva_exoneree": False}), {"t1": v}, carte)
    assert _un(ent, "SELECT montant FROM ventes").lignes == [[10000.0]]


def test_performance_virements_montants_repetes():
    """Mobile money : milliers de transferts du même montant rond ; l'appariement doit rester rapide."""
    d0 = date(2025, 1, 1)
    sorties = [[d0 + timedelta(days=i % 200), f"Transfert {i}", "10 000", None] for i in range(5000)]
    entrees = [[d0 + timedelta(days=i % 200), f"Reception {i}", None, "10 000"] for i in range(5000)]
    ventes = [[d0 + timedelta(days=i % 300), f"T{i}", f"Client {i % 900}", "Article", 1, "5 000 F", "5 000 F"]
              for i in range(20_000)]
    carte = Carte(tableaux=[_tc("t1", "banque", _cols("date", "libelle", "debit", "credit")),
                            _tc("t2", "mobile_money", _cols("date", "libelle", "debit", "credit")),
                            _tc("t3", "ventes", _cols("date", "piece", "tiers", "designation", "qte", "pu", "montant"))],
                  relations=[RelationCarte(type="virement_interne", tableaux=["t1", "t2"], justification="x")])
    t0 = time.perf_counter()
    ent = Entrepot(_dossier(), {"t1": _t(sorties), "t2": _t(entrees, feuille="M"), "t3": _t(ventes, feuille="V")}, carte)
    duree = time.perf_counter() - t0
    assert _un(ent, "SELECT count(*) FILTER (WHERE virement_interne) FROM tresorerie").lignes == [[10000]]
    assert duree < 4, duree

"""Compréhension des fichiers quel que soit leur type, et rapport partiel au lieu d'un refus."""
import copy
from pathlib import Path

from app.comprehension import _num, comprendre
from app.llm import LLMError
from app.main import create_app
from conftest import FakeLLM
from fake_supabase import FakeSupabase
from fastapi.testclient import TestClient
from test_api import auth, client, deposer_fichier, depot, tok, traiter  # noqa: F401 (fixtures)

F = Path(__file__).parent / "fichiers"
JOURNAUX = ["journal_ventes.xlsx", "journal_achats.xlsx", "journal_paie.xlsx"]


def test_nombres_tous_formats():
    assert _num("1 234 567,89") == 1234567.89 and _num("1.234.567,89") == 1234567.89 and _num("(1 000)") == -1000
    assert _num("125 000 000 FCFA") == 125000000 and _num("12,5") == 12.5 and _num("abc") is None and _num(42) == 42


def test_journaux_reconnus_et_totaux_exacts():
    c = comprendre([(n, F / n) for n in JOURNAUX])
    genres = " ".join(c.documents)
    assert "journal des ventes" in genres and "journal des achats" in genres and "journal de paie" in genres
    f = {k[1]: round(v.valeur) for k, v in c.faits.items()}
    assert f["chiffre_affaires_ht"] == 1_871_300_000          # colonne HT, ligne TOTAL ignorée
    assert f["cout_achats_marchandises"] == 305_560_000       # TTC ramené au HT
    assert f["charges_personnel"] == 42_007_560                # brut + charges patronales
    assert str(c.debut) == "2025-07-01" and str(c.fin) == "2026-06-30"


def test_balance_agregee_par_comptes():
    c = comprendre([("balance.csv", F / "balance.csv")])
    f = {k[1]: round(v.valeur) for k, v in c.faits.items()}
    assert f["chiffre_affaires_ht"] == 425_000_000 and f["cout_achats_marchandises"] == 292_000_000
    assert f["charges_personnel"] == 63_700_000 and f["creances_clients"] == 89_000_000 and f["tresorerie_passif"] == 12_500_000


def _ia_vide():
    vide = copy.deepcopy(FakeLLM().payload)
    for bloc in (vide["compte_resultat"], *vide["bilan"].values()):
        for n in bloc.values():
            n["valeur"], n["confiance"] = "NON_DISPONIBLE", 0.0
    vide["meta"]["confidence_global"] = 0.15
    return vide


def _deposer_journaux(c, tok):
    docs = [{"id": deposer_fichier(c.sb, tok, nom=n, data=(F / n).read_bytes(), categorie="autre"), "type": "autre"} for n in JOURNAUX]
    return depot(c, tok, docs=docs).json()["job_id"]


def test_journaux_seuls_donnent_un_rapport_partiel(settings):
    """Le cas réel JPO-2026-0003 : 3 journaux, l'IA ne trouve aucun total -> rapport indicatif, pas de refus."""
    sb = FakeSupabase()
    with TestClient(create_app(settings, llm=FakeLLM(payload=_ia_vide()), sb=sb)) as c:
        c.sb = sb
        tok = sb.add_user("x@exemple.ci")
        v = traiter(c, tok, _deposer_journaux(c, tok))
        assert v["statut"] == "ENVOYE" and v["rapport_disponible"], v
        assert v["indicatif"] and v["verdict"].startswith("Note indicative")
        assert v["score_performance"] is not None and v["couverture_pct"] < 60
        msgs = [e["message"] for e in c.get(f"/api/diagnostics/{v['job_id']}/journal", headers=auth(tok)).json()["evenements"]]
        assert any("journal des ventes" in m for m in msgs) and any("Totaux calculés" in m for m in msgs)
        assert any("note indicative" in m for m in msgs)
        md = next(iter(k for k in sb.storage["rapports"] if k.endswith(".md")))
        texte = sb.storage["rapports"][md].decode()
        assert "Note indicative" in texte and "documents partiels" in texte
        assert "bénéfice faible" not in texte  # pas de conclusion sur un résultat inconnu


def test_ia_indisponible_mais_totaux_calcules(settings):
    sb = FakeSupabase()
    with TestClient(create_app(settings, llm=FakeLLM(error=LLMError("tous les modèles ont échoué")), sb=sb)) as c:
        c.sb = sb
        tok = sb.add_user("x@exemple.ci")
        v = traiter(c, tok, _deposer_journaux(c, tok))
        assert v["statut"] == "ENVOYE" and v["rapport_disponible"]
        assert sb.tables["rapports"][0]["modele_ia"].startswith("calcul direct")


def test_prompt_ia_recoit_la_synthese_anonymisee(settings):
    sb, llm = FakeSupabase(), FakeLLM(payload=_ia_vide())
    with TestClient(create_app(settings, llm=llm, sb=sb)) as c:
        c.sb = sb
        tok = sb.add_user("x@exemple.ci")
        traiter(c, tok, _deposer_journaux(c, tok))
        assert "SYNTHÈSE CALCULÉE PAR LE SERVEUR" in llm.prompts[0] and "1871300000" in llm.prompts[0]


def _journal_ventes(chemin, debut, mois):
    import datetime as dt

    from openpyxl import Workbook
    w = Workbook(); ws = w.active; ws.append(["Date", "Facture", "Client", "Montant HT"])
    for m in range(mois):
        an, mo = debut[0] + (debut[1] - 1 + m) // 12, (debut[1] - 1 + m) % 12 + 1
        ws.append([dt.date(an, mo, 15), f"F{m}", "Client", 1_000_000 * (an - 2023)])  # 2024 : 1 M/mois, 2025 : 2 M, 2026 : 3 M
    w.save(chemin)


def test_journal_sur_plusieurs_annees_retient_le_dernier_exercice_complet(tmp_path):
    """Le cas réel : ventes de janvier 2025 à novembre 2026 -> exercice 2025 retenu, pas 23 mois additionnés."""
    _journal_ventes(tmp_path / "ventes.xlsx", (2025, 1), 23)
    c = comprendre([("01_Journal_des_Ventes.xlsx", tmp_path / "ventes.xlsx")])
    f = {k[1]: round(v.valeur) for k, v in c.faits.items()}
    assert f["chiffre_affaires_ht"] == 12 * 2_000_000                # 2025 seulement (2026 n'a que 11 mois)
    assert "chiffre_affaires_n1" not in f                            # pas de 2024 dans le fichier
    assert str(c.debut) == "2025-01-15" and str(c.fin) == "2025-12-15"
    assert any("exercice 2025 retenu" in a for a in c.avertissements)


def test_deux_annees_completes_donnent_le_ca_n1(tmp_path):
    _journal_ventes(tmp_path / "ventes.xlsx", (2024, 1), 24)
    f = {k[1]: round(v.valeur) for k, v in comprendre([("ventes.xlsx", tmp_path / "ventes.xlsx")]).faits.items()}
    assert f["chiffre_affaires_ht"] == 24_000_000 and f["chiffre_affaires_n1"] == 12_000_000


def test_jusqu_a_dix_fichiers(client, tok):
    docs = [{"id": deposer_fichier(client.sb, tok, nom=f"journal_{i}.csv"), "type": "autre"} for i in range(11)]
    assert depot(client, tok, docs=docs).status_code == 422          # 11 : refusé
    assert depot(client, tok, docs=docs[:10]).status_code == 202     # 10 : accepté

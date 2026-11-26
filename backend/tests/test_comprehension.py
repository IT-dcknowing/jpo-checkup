"""Compréhension des fichiers quel que soit leur type, et rapport partiel au lieu d'un refus."""
import copy
from pathlib import Path

from fastapi.testclient import TestClient

from app.comprehension import _num, comprendre
from app.llm import LLMError
from app.main import create_app
from conftest import FakeLLM
from fake_supabase import FakeSupabase
from test_api import auth, deposer_fichier, depot, traiter

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

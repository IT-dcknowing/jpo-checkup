import json

import httpx
import pytest

from app import report
from app.anonymize import anonymize
from app.extraction import LectureImpossible, finalize
from app.llm import LLMAuthError, LLMError, OpenRouterClient, parse_json_text
from app.readers import ReaderError, fit_budget, read_file
from app.schemas import ND, DepotForm, IAOutput, parse_number
from app.security import RateLimiter, sign_token, verify_token
from conftest import EXEMPLE, FORM


def make_form(**over):
    d = dict(raison_sociale=FORM["raison_sociale"], ncc=FORM["ncc"], secteur=FORM["secteur"], effectif=15, contact_nom=FORM["contact_nom"],
             contact_email="awa@example.ci", contact_telephone="07 67 13 19 93", type_client="PROSPECT", jour_evenement="2026-12-16",
             regime_fiscal="REEL_SIMPLIFIE", fne="NON_ACTIVE", dgi="A_JOUR", cnps="A_JOUR", its="A_JOUR", cmu="EN_RETARD", disa="A_JOUR",
             tva_exoneree=False, consentement=True)
    return DepotForm(**{**d, **over})


def test_form_accepts_front_values():
    f = make_form(secteur="banque", fne="NON_APPLICABLE", dgi="NON_DISPONIBLE")  # code de secteur et « je ne sais pas » du front
    assert f.secteur == "Banque" and f.secteur_exempte_fne and f.dgi == "NE_SAIT_PAS"
    with pytest.raises(ValueError):
        make_form(consentement=False)


def test_exempt_fne_cannot_be_claimed_by_non_exempt_sector(settings):
    from app.pipeline import build_data
    ia = IAOutput.model_validate(EXEMPLE["ia"]).model_dump()
    assert build_data(make_form(fne="NON_APPLICABLE"), "R", ia)["ia"]["conformite"]["fne"]["statut"] == ND
    assert build_data(make_form(secteur="Pharmacie", fne="NON_ACTIVE"), "R", ia)["ia"]["conformite"]["fne"]["statut"] == "NON_APPLICABLE"


# ------------------------------------------------------------------ moteur : jeu de référence (spec §17)
def test_golden_file_scores(settings):
    from app.pipeline import build_data
    data = build_data(make_form(), "JPO-2026-0001", IAOutput.model_validate(EXEMPLE["ia"]).model_dump())
    f = report.compute(settings, data)
    assert (f["score_performance"], f["grade_performance"]) == (50, "C")
    assert (f["score_conformite"], f["grade_conformite"]) == (75, "B")
    assert f["couverture_pct"] == 95


# ------------------------------------------------------------------ nombres et validation
@pytest.mark.parametrize("raw,exp", [
    (125000000, 125000000.0), ("125 000 000", 125000000.0), ("1.250.000,50", 1250000.5), ("1,250,000.5", 1250000.5),
    ("12,5", 12.5), ("(1 000)", -1000.0), ("45 000 FCFA", 45000.0), ("NON_DISPONIBLE", ND), ("abc", ND), (None, ND), (True, ND),
    (float("nan"), ND),
])
def test_parse_number(raw, exp):
    assert parse_number(raw) == exp


def test_champ_accepts_bare_numbers_and_percent_confidence():
    out = IAOutput.model_validate({"compte_resultat": {"chiffre_affaires_ht": 1000, "stocks": {"valeur": "5", "confiance": 90}}})
    assert out.compte_resultat.chiffre_affaires_ht.valeur == 1000.0  # type: ignore[attr-defined]
    assert out.compte_resultat.chiffre_affaires_ht.confiance == 0.7  # type: ignore[attr-defined]


def test_empty_ia_output_is_invalid():
    with pytest.raises(ValueError):
        IAOutput.model_validate({"foo": 1})


def test_finalize_rejects_negatives_and_low_confidence():
    ia = json.loads(json.dumps(EXEMPLE["ia"]))
    ia["compte_resultat"]["charges_personnel"]["valeur"] = -5
    ia["compte_resultat"]["stocks"] = {"valeur": 1, "confiance": 0.2}
    ia["bilan"]["actif"]["immobilisations_nettes"]["valeur"] = "-1"
    ia["meta"]["period_start"] = "n'importe quoi"
    ext = finalize(IAOutput.model_validate(ia), make_form(), "m")
    assert ext.ia["compte_resultat"]["charges_personnel"]["valeur"] == ND
    assert ext.ia["bilan"]["actif"]["immobilisations_nettes"]["valeur"] == ND
    assert ext.ia["meta"]["period_start"] == "2025-01-01" and ext.ia["meta"]["period_end"] == "2025-12-31"
    assert any("négatif" in w for w in ext.warnings)


def test_finalize_negative_equity_allowed():
    ia = json.loads(json.dumps(EXEMPLE["ia"]))
    ia["bilan"]["passif"]["capitaux_propres"]["valeur"] = -3000000
    ext = finalize(IAOutput.model_validate(ia), make_form(), "m")
    assert ext.ia["bilan"]["passif"]["capitaux_propres"]["valeur"] == -3000000


def test_finalize_unreadable_raises():
    ia = {"meta": {"confidence_global": 0.1}, "compte_resultat": {}, "bilan": {}}
    with pytest.raises(LectureImpossible):
        finalize(IAOutput.model_validate(ia), make_form(), "m")


# ------------------------------------------------------------------ client IA et repli
def mock_client(settings, handler):
    settings.openrouter_api_key = "cle-test"
    settings.llm_backoff = [0, 0]
    return OpenRouterClient(settings, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)


def ok_response(payload):
    return httpx.Response(200, json={"choices": [{"message": {"content": "```json\n" + json.dumps(payload) + "\n```"}}]})


def test_fallback_chain_moves_to_next_model(settings):
    seen = []

    def handler(req):
        model = json.loads(req.content)["model"]
        seen.append(model)
        if model == settings.llm_models[0]:
            return httpx.Response(429)
        if model == settings.llm_models[1]:
            return httpx.Response(200, json={"choices": [{"message": {"content": "pas du json"}}]})
        return ok_response(EXEMPLE["ia"])

    out, model = mock_client(settings, handler).complete_validated("s", "u", IAOutput.model_validate)
    assert model == settings.llm_models[2]
    assert out.compte_resultat.chiffre_affaires_ht.valeur == 125000000  # type: ignore[attr-defined]
    assert seen.count(settings.llm_models[0]) == 3  # 1 essai + 2 reprises (backoff)
    assert seen.count(settings.llm_models[1]) == 2  # essai + relance avec le message d'erreur


def test_optional_params_dropped_on_400(settings):
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        return httpx.Response(400, json={"error": "response_format non supporté"}) if "response_format" in body else ok_response(EXEMPLE["ia"])

    _, model = mock_client(settings, handler).complete_validated("s", "u", IAOutput.model_validate)
    assert model == settings.llm_models[0] and len(bodies) == 2 and "response_format" not in bodies[1]


def test_auth_error_stops_everything(settings):
    with pytest.raises(LLMAuthError):
        mock_client(settings, lambda r: httpx.Response(401)).complete_validated("s", "u", IAOutput.model_validate)


def test_missing_key_is_explicit(settings):
    with pytest.raises(LLMAuthError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(settings).complete_validated("s", "u", IAOutput.model_validate)


def test_all_models_fail(settings):
    with pytest.raises(LLMError, match="Tous les modèles"):
        mock_client(settings, lambda r: httpx.Response(500)).complete_validated("s", "u", IAOutput.model_validate)


def test_parse_json_text_variants():
    assert parse_json_text('Voici : {"a": 1} merci') == {"a": 1}
    assert parse_json_text('```json\n{"a": 2}\n```') == {"a": 2}
    with pytest.raises(ValueError):
        parse_json_text("rien")


# ------------------------------------------------------------------ lecture des fichiers
def test_read_csv_cp1252_semicolon(tmp_path):
    p = tmp_path / "b.csv"
    p.write_bytes("compte;libellé;solde\n701;Ventes de marchandises;1 250 000\n601;Achats;750000\n".encode("cp1252"))
    r = read_file(p)
    assert "Ventes de marchandises" in r.text and "701 | " in r.text


def test_read_xlsx(tmp_path):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Balance"
    ws.append(["Compte", "Solde"]); ws.append([701, 1250000.0]); ws.append([None, None])
    p = tmp_path / "b.xlsx"; wb.save(p)
    r = read_file(p)
    assert "FEUILLE « Balance »" in r.text and "701 | 1250000" in r.text


def test_fake_extension_rejected(tmp_path):
    p = tmp_path / "x.pdf"; p.write_bytes(b"ceci n'est pas un pdf")
    with pytest.raises(ReaderError):
        read_file(p)


def test_fit_budget_keeps_summary_accounts():
    lines = ["Compte | Solde"] + [f"7010{i:03d} | {i}" for i in range(500)] + ["70 | 999"]
    out = fit_budget("\n".join(lines), 1500)
    assert len(out) <= 1600 and "70 | 999" in out and "tronqué" in out


# ------------------------------------------------------------------ anonymisation et sécurité
def test_anonymize_removes_identifiers():
    form = make_form()
    text = ("Société Kouassi & Fils SARL NCC 1234567A contact Awa Kouassi awa@example.ci tel 07 67 13 19 93 / +225 27 22 42 14 43 "
            "RIB CI93 CI008 01234 12345678901 12 IBAN CI93 CI00 8012 3412 3456 7890 1234 montant 125 000 000")
    out = anonymize(text, form)
    for secret in ("Kouassi", "1234567A", "awa@example.ci", "07 67 13 19 93", "27 22 42 14 43", "12345678901"):
        assert secret not in out
    assert "125 000 000" in out


def test_tokens_expire_and_are_kind_bound():
    t = sign_token("k", "dl", "abc", 60)
    assert verify_token("k", t, "dl") == "abc"
    assert verify_token("k", t, "admin") is None
    assert verify_token("autre", t, "dl") is None
    assert verify_token("k", sign_token("k", "dl", "abc", -1), "dl") is None
    assert verify_token("k", t[:-2] + "xx", "dl") is None


def test_rate_limiter():
    r = RateLimiter(2, 3600)
    assert r.allow("ip") and r.allow("ip") and not r.allow("ip") and r.allow("autre")

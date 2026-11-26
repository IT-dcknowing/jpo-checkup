"""Tests de fournisseur.py : faux client Anthropic injecté, jamais de réseau."""
import json
from types import SimpleNamespace as NS

import pytest

from app.analyse import fournisseur as F
from app.analyse.modele import Carte, Cout

SCHEMA = {
    "type": "object",
    "properties": {
        "nature": {"type": "string", "enum": ["ventes", "banque", "autre"]},
        "confiance": {"type": "number", "minimum": 0, "maximum": 1},
        "colonnes": {"type": "array", "items": {"type": "integer"}},
        "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["nature", "confiance", "colonnes"],
    "additionalProperties": False,
}
OK = {"nature": "ventes", "confiance": 0.8, "colonnes": [0, 2], "note": None}


# ---------------------------------------------------------------------- faux client
def _reponse(texte, stop="end_turn", modele="claude-sonnet-5-5", usage=None, details=None, blocs=None):
    usage = usage or NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                        cache_creation_input_tokens=0, iterations=None)
    contenu = blocs if blocs is not None else [NS(type="text", text=texte)]
    return NS(content=contenu, stop_reason=stop, model=modele, usage=usage, stop_details=details)


class _Messages:
    def __init__(self, reponse):
        self.reponse = reponse
        self.appels = []

    def create(self, **kw):
        self.appels.append(kw)
        return self.reponse


class FauxClient:
    def __init__(self, reponse):
        self.messages = _Messages(reponse)
        self.beta = NS(messages=_Messages(reponse))


# ---------------------------------------------------------------------- coût et tarifs
def test_cout_usd_tarifs():
    assert F.cout_usd("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert F.cout_usd("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
    assert F.cout_usd("claude-opus-5-5", 0, 0, cache_lus=1_000_000) == pytest.approx(0.20)
    assert F.cout_usd("claude-sonnet-5-5", 1_000_000, 1_000_000, 1_000_000, 1_000_000) == pytest.approx(
        2 + 10 + 0.10 + 2 * 1.25)
    assert F.cout_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    # modèle de repli inconnu : tarif de la famille ; famille inconnue : le plus cher
    assert F.cout_usd("claude-opus-4-8", 1_000_000, 0) == pytest.approx(4.0)
    assert F.cout_usd("modele-x", 1_000_000, 0) == pytest.approx(4.0)


# ---------------------------------------------------------------------- Anthropic
def test_anthropic_succes_parametres_et_cout():
    usage = NS(input_tokens=1200, output_tokens=300, cache_read_input_tokens=5000,
               cache_creation_input_tokens=800, iterations=None)
    client = FauxClient(_reponse(json.dumps(OK), usage=usage))
    f = F.FournisseurAnthropic(modele="claude-sonnet-5-5", effort="medium", client=client)
    donnees, cout = f.json("carte", "SYS", "USR", SCHEMA, 4000)
    assert donnees == OK
    assert client.messages.appels == []          # sonnet-5-5 : passe par beta (repli serveur)
    kw = client.beta.messages.appels[0]
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["model"] == "claude-sonnet-5-5" and kw["max_tokens"] == 4000
    assert kw["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert kw["messages"] == [{"role": "user", "content": "USR"}]
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "medium"}
    assert (cout.tokens_entree, cout.tokens_sortie, cout.tokens_cache_lus, cout.tokens_cache_ecrits) == (
        1200, 300, 5000, 800)
    attendu = (1200 * 2 + 300 * 10 + 5000 * 0.10 + 800 * 2 * 1.25) / 1e6
    assert cout.usd == pytest.approx(attendu) and cout.etape == "carte" and cout.modele == "claude-sonnet-5-5"
    assert f.dernier_echange["brut"] == json.dumps(OK)


def test_anthropic_haiku_sans_repli_et_sans_effort():
    client = FauxClient(_reponse(json.dumps(OK), modele="claude-haiku-5-5"))
    f = F.FournisseurAnthropic(modele="claude-haiku-5-5", effort=None, client=client)
    f.json("etats_pdf", "S", "U", SCHEMA, 1000)
    assert client.beta.messages.appels == []
    kw = client.messages.appels[0]
    assert "fallbacks" not in kw and "betas" not in kw and "effort" not in kw["output_config"]


def test_anthropic_ignore_blocs_non_texte():
    blocs = [NS(type="thinking", thinking="..."), NS(type="fallback"), NS(type="text", text=json.dumps(OK))]
    f = F.FournisseurAnthropic(client=FauxClient(_reponse(None, blocs=blocs)))
    assert f.json("carte", "S", "U", SCHEMA, 1000)[0] == OK


def test_anthropic_cout_par_iterations_repli():
    iters = [NS(type="message", model="claude-sonnet-5-5", input_tokens=1000, output_tokens=0,
                cache_read_input_tokens=0, cache_creation_input_tokens=0),
             NS(type="fallback_message", model="claude-haiku-5-5", input_tokens=1000, output_tokens=500,
                cache_read_input_tokens=0, cache_creation_input_tokens=0)]
    usage = NS(input_tokens=1000, output_tokens=500, cache_read_input_tokens=0, cache_creation_input_tokens=0,
               iterations=iters)
    f = F.FournisseurAnthropic(client=FauxClient(_reponse(json.dumps(OK), modele="claude-haiku-5-5", usage=usage)))
    _, cout = f.json("carte", "S", "U", SCHEMA, 1000)
    assert cout.tokens_entree == 2000 and cout.tokens_sortie == 500 and cout.modele == "claude-haiku-5-5"
    assert cout.usd == pytest.approx((1000 * 2 + 1000 * 0.10 + 500 * 0.50) / 1e6)


def test_anthropic_usage_en_dict():
    usage = {"input_tokens": 10, "output_tokens": 5}
    f = F.FournisseurAnthropic(modele="claude-opus-5-5", client=FauxClient(_reponse(json.dumps(OK), usage=usage,
                                                                                    modele="claude-opus-5-5")))
    _, cout = f.json("carte", "S", "U", SCHEMA, 100)
    assert cout.usd == pytest.approx((10 * 4 + 5 * 20) / 1e6)


def test_anthropic_refus():
    rep = _reponse("", stop="refusal", details=NS(category="cyber", explanation="x"))
    f = F.FournisseurAnthropic(client=FauxClient(rep))
    with pytest.raises(F.RefusModele) as e:
        f.json("carte", "S", "U", SCHEMA, 100)
    assert "cyber" in str(e.value) and e.value.cout is not None and e.value.cout.usd > 0


def test_anthropic_max_tokens():
    f = F.FournisseurAnthropic(client=FauxClient(_reponse('{"nature": "ven', stop="max_tokens")))
    with pytest.raises(F.ReponseTronquee) as e:
        f.json("carte", "S", "U", SCHEMA, 50)
    assert e.value.brut.startswith('{"nature"') and "50" in str(e.value)


@pytest.mark.parametrize("texte", ["pas du json", "[1, 2]", "", '{"nature": "ventes"'])
def test_anthropic_json_invalide(texte):
    f = F.FournisseurAnthropic(client=FauxClient(_reponse(texte)))
    with pytest.raises(F.JsonInvalide) as e:
        f.json("carte", "S", "U", SCHEMA, 100)
    assert e.value.cout is not None and e.value.brut == texte


def test_anthropic_hors_schema():
    mauvais = {"nature": "inconnue", "confiance": 2, "colonnes": ["a"], "extra": 1}
    f = F.FournisseurAnthropic(client=FauxClient(_reponse(json.dumps(mauvais))))
    with pytest.raises(F.JsonInvalide) as e:
        f.json("carte", "S", "U", SCHEMA, 100)
    msg = str(e.value)
    assert "hors liste" in msg and "maximum" in msg


# ---------------------------------------------------------------------- schéma
def test_verifier_schema_cas_varies():
    assert F.verifier_schema(OK, SCHEMA) == []
    assert any("obligatoire" in x for x in F.verifier_schema({"nature": "autre"}, SCHEMA))
    assert any("inattendue" in x for x in F.verifier_schema({**OK, "x": 1}, SCHEMA))
    assert any("type" in x for x in F.verifier_schema({**OK, "colonnes": [True]}, SCHEMA))
    assert any("variante" in x for x in F.verifier_schema({**OK, "note": 3}, SCHEMA))


def test_verifier_schema_pydantic_carte():
    """Le schéma pydantic de la Carte ($defs, $ref, anyOf, défauts) est compris."""
    schema = Carte.model_json_schema()
    bonne = {"tableaux": [{"id": "t1", "nature": "ventes", "confiance": 0.9, "justification": "j",
                           "colonnes": [{"index": 0, "nom": "Date", "role": "date"}]}]}
    assert F.verifier_schema(bonne, schema) == []
    mauvaise = {"tableaux": [{"id": "t1", "nature": "banane", "confiance": 0.9, "justification": "j",
                              "colonnes": [{"index": -1, "nom": "Date", "role": "date"}]}]}
    erreurs = F.verifier_schema(mauvaise, schema)
    assert any("nature" in x for x in erreurs) and any("index" in x for x in erreurs)


# ---------------------------------------------------------------------- simulé et budget
def test_simule_liste_dict_fonction_exception():
    f = F.FournisseurSimule([OK, json.dumps(OK), F.RefusModele("non")], usd_par_appel=0.5)
    assert f.json("carte", "S", "U", SCHEMA, 10)[0] == OK
    assert f.json("carte", "S", "U", SCHEMA, 10)[1].usd == 0.5
    with pytest.raises(F.RefusModele) as e:
        f.json("carte", "S", "U", SCHEMA, 10)
    assert e.value.cout.usd == 0.5 and len(f.appels) == 3

    par_etape = F.FournisseurSimule({"carte": [OK], "etats_pdf": {"a": 1}})
    assert par_etape.json("etats_pdf", "S", "U", {}, 10)[0] == {"a": 1}
    assert par_etape.json("carte", "S", "U", SCHEMA, 10)[0] == OK
    with pytest.raises(F.ErreurFournisseur):
        par_etape.json("carte", "S", "U", SCHEMA, 10)

    fn = F.FournisseurSimule(fonction=lambda etape, s, u, sch: {"nature": "banque", "confiance": 0.1,
                                                                 "colonnes": [len(u)]})
    assert fn.json("carte", "S", "abc", SCHEMA, 10)[0]["colonnes"] == [3]
    with pytest.raises(F.JsonInvalide):
        F.FournisseurSimule(["{oups"]).json("carte", "S", "U", SCHEMA, 10)
    assert isinstance(F.FournisseurSimule(), F.Fournisseur)


def test_budget_et_appeler():
    b = F.Budget(1.0)
    f = F.FournisseurSimule([OK, F.JsonInvalide("x"), OK, OK], usd_par_appel=0.4)
    F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)
    with pytest.raises(F.JsonInvalide):
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)     # l'échec est compté
    assert b.total_usd == pytest.approx(0.8) and not b.depasse()
    F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)
    assert b.depasse() and b.reste_usd == 0
    with pytest.raises(F.BudgetAtteint):
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)
    assert len(f.appels) == 3                               # aucun appel après le plafond
    assert all(isinstance(c, Cout) for c in b.couts)
    F.appeler(F.FournisseurSimule([OK]), None, "carte", "S", "U", SCHEMA, 10)  # sans budget


# ---------------------------------------------------------------------- configuration
def test_fournisseur_depuis_config(monkeypatch):
    for v in ("LLM_FOURNISSEUR", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "MODELE_CARTE", "MODELE_EFFORT",
              "MODELE_AGENT", "BUDGET_DOSSIER_USD"):
        monkeypatch.delenv(v, raising=False)
    assert F.fournisseur_depuis_config(NS()) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    f = F.fournisseur_depuis_config(NS())                   # client SDK construit, aucun appel réseau
    assert isinstance(f, F.FournisseurAnthropic) and f.modele == "claude-sonnet-5-5" and f.effort == "medium"
    monkeypatch.setenv("MODELE_AGENT", "claude-opus-5-5")
    monkeypatch.setenv("MODELE_EFFORT", "high")
    f = F.fournisseur_depuis_config(NS(), variable_modele="MODELE_AGENT")
    assert f.modele == "claude-opus-5-5" and f.effort == "high"
    f = F.fournisseur_depuis_config(NS(modele_carte="claude-haiku-5-5"))
    assert f.modele == "claude-haiku-5-5"
    monkeypatch.setenv("LLM_FOURNISSEUR", "openrouter")
    assert F.fournisseur_depuis_config(NS(openrouter_api_key="")) is None
    assert isinstance(F.fournisseur_depuis_config(NS(openrouter_api_key="k", llm_timeout=5)),
                      F.FournisseurOpenRouter)
    assert F.budget_depuis_config(NS()).plafond_usd == 3.0
    monkeypatch.setenv("BUDGET_DOSSIER_USD", "1.5")
    assert F.budget_depuis_config(NS()).plafond_usd == 1.5


def test_openrouter_faux_client():
    class Faux:
        def complete_validated(self, system, user, validate):
            assert "schéma" in system
            with pytest.raises(ValueError):
                validate({"nature": "x"})
            return validate(OK), "modele-gratuit"

    donnees, cout = F.FournisseurOpenRouter(None, client=Faux()).json("carte", "S", "U", SCHEMA, 10)
    assert donnees == OK and cout.usd == 0 and cout.modele == "modele-gratuit"


# ====================================================================== cas limites (revue adverse)
import time


def _usage(e=0, s=0, lu=0, ec=0, iterations=None):
    return NS(input_tokens=e, output_tokens=s, cache_read_input_tokens=lu, cache_creation_input_tokens=ec,
              iterations=iterations)


def test_limite_budget_cumul_flottant_dix_appels_a_dix_centimes():
    """Spec : un appel est refusé si le plafond est atteint. 10 × 0,10 $ = 1,00 $ atteint le plafond de 1 $
    (0.1 × 10 vaut 0.9999999999999999 en flottant : le cumul ne doit pas laisser passer un 11e appel)."""
    b = F.Budget(1.0)
    f = F.FournisseurSimule([OK] * 11, usd_par_appel=0.10)
    for _ in range(10):
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)
    assert b.depasse()
    with pytest.raises(F.BudgetAtteint):
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)
    assert len(f.appels) == 10


def test_limite_budget_decimal_francais_dans_l_environnement(monkeypatch):
    """Spec : plafond = BUDGET_DOSSIER_USD. Une valeur saisie à la française (« 2,5 ») ne doit pas être
    remplacée silencieusement par le défaut de 3 $ (plafond plus élevé que voulu)."""
    monkeypatch.setenv("BUDGET_DOSSIER_USD", "2,5")
    assert F.budget_depuis_config(NS()).plafond_usd == pytest.approx(2.5)


@pytest.mark.parametrize("brut, attendu", [(" 0.75 ", 0.75), ("", 3.0), ("abc", 3.0), ("5", 5.0)])
def test_limite_budget_valeurs_diverses(monkeypatch, brut, attendu):
    monkeypatch.setenv("BUDGET_DOSSIER_USD", brut)
    assert F.budget_depuis_config(NS()).plafond_usd == pytest.approx(attendu)


def test_limite_budget_attribut_settings_prioritaire(monkeypatch):
    monkeypatch.setenv("BUDGET_DOSSIER_USD", "9")
    assert F.budget_depuis_config(NS(budget_dossier_usd=0.5)).plafond_usd == pytest.approx(0.5)


def test_limite_refus_anthropic_compte_dans_le_budget_puis_bloque():
    """Un refus est facturé : appeler() le compte, puis refuse l'appel suivant une fois le plafond atteint."""
    usage = _usage(e=400_000, s=50_000)                    # 0,8 + 0,5 = 1,3 $ au tarif sonnet
    rep = _reponse("", stop="refusal", details={"category": "bio"}, usage=usage)
    client = FauxClient(rep)
    f = F.FournisseurAnthropic(client=client)
    b = F.Budget(1.0)
    with pytest.raises(F.RefusModele) as e:
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 100)
    assert "bio" in str(e.value)
    assert b.total_usd == pytest.approx(1.3) and b.depasse()
    with pytest.raises(F.BudgetAtteint):
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 100)
    assert len(client.beta.messages.appels) == 1


def test_limite_refus_sans_details_ni_texte():
    rep = _reponse(None, stop="refusal", details=None, blocs=[])
    with pytest.raises(F.RefusModele) as e:
        F.FournisseurAnthropic(client=FauxClient(rep)).json("carte", "S", "U", SCHEMA, 100)
    assert e.value.brut == ""


def test_limite_troncature_compte_dans_le_budget():
    rep = _reponse('{"nature": "ventes", "confiance": 0.', stop="max_tokens", usage=_usage(e=1000, s=4000))
    b = F.Budget(3.0)
    with pytest.raises(F.ReponseTronquee):
        F.appeler(F.FournisseurAnthropic(client=FauxClient(rep)), b, "carte", "S", "U", SCHEMA, 4000)
    assert b.total_usd == pytest.approx((1000 * 2 + 4000 * 10) / 1e6)


def test_limite_iterations_repli_vers_modele_hors_tarifs():
    """Repli vers un modèle absent de TARIFS (famille opus) : tentative refusée au tarif du modèle demandé
    (entrée `message` sans modèle), tentative servie au tarif de sa famille ; usage global non recompté."""
    iters = [{"type": "message", "model": None, "input_tokens": 2000, "output_tokens": 0,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 3000},
             {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 2000, "output_tokens": 700,
              "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0}]
    usage = {"input_tokens": 2000, "output_tokens": 700, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 0, "iterations": iters}
    f = F.FournisseurAnthropic(modele="claude-sonnet-5-5",
                               client=FauxClient(_reponse(json.dumps(OK), modele="claude-opus-4-8", usage=usage)))
    _, cout = f.json("carte", "S", "U", SCHEMA, 1000)
    attendu = (2000 * 2 + 3000 * 2 * 1.25) / 1e6 + (2000 * 4 + 700 * 20 + 1000 * 0.20) / 1e6
    assert cout.usd == pytest.approx(attendu, rel=1e-6)
    assert cout.modele == "claude-opus-4-8"
    assert (cout.tokens_entree, cout.tokens_sortie, cout.tokens_cache_lus, cout.tokens_cache_ecrits) == (
        4000, 700, 1000, 3000)


def test_limite_usage_champs_none_et_absents():
    """Champs d'usage optionnels à None (SDK) ou absents : traités comme 0, pas d'exception."""
    usage = NS(input_tokens=100, output_tokens=None, cache_read_input_tokens=None,
               cache_creation_input_tokens=None, iterations=[])
    f = F.FournisseurAnthropic(modele="claude-haiku-5-5",
                               client=FauxClient(_reponse(json.dumps(OK), modele="claude-haiku-5-5", usage=usage)))
    _, cout = f.json("etats_pdf", "S", "U", SCHEMA, 100)
    assert cout.usd == pytest.approx(100 * 0.10 / 1e6) and cout.tokens_sortie == 0


def test_limite_json_reparti_sur_plusieurs_blocs_texte_et_blocs_parasites():
    texte = json.dumps({"nature": "banque", "confiance": 1, "colonnes": [], "note": "solde « 1 250 000 F »"},
                       ensure_ascii=False)
    blocs = [NS(type="thinking", thinking="{faux json}"), NS(type="text", text=texte[:15]),
             NS(type="fallback", text="ne pas lire"), NS(type="text", text=texte[15:])]
    donnees, _ = F.FournisseurAnthropic(client=FauxClient(_reponse(None, blocs=blocs))).json(
        "carte", "S", "U", SCHEMA, 100)
    assert donnees["note"] == "solde « 1 250 000 F »" and donnees["colonnes"] == []


@pytest.mark.parametrize("texte", ['  \n{"nature": "autre", "confiance": 0, "colonnes": [-3]}\n  ',
                                   '{"nature":"autre","confiance":1.0,"colonnes":[0],"note":"montant -1 250 000"}'])
def test_limite_json_espaces_negatifs_bornes(texte):
    d, _ = F.FournisseurAnthropic(client=FauxClient(_reponse(texte))).json("carte", "S", "U", SCHEMA, 100)
    assert d["nature"] == "autre"


@pytest.mark.parametrize("texte", ["```json\n{}\n```", "null", '"texte"', "42", "{'nature': 'ventes'}"])
def test_limite_reponses_sales_rejetees(texte):
    with pytest.raises(F.JsonInvalide) as e:
        F.FournisseurAnthropic(client=FauxClient(_reponse(texte))).json("carte", "S", "U", SCHEMA, 100)
    assert e.value.cout is not None and e.value.brut == texte


def test_limite_schema_types_proches_rejetes():
    """Booléen pour un nombre, texte « 0,8 » pour un nombre, nombre pour un texte : hors schéma."""
    for mauvais in ({**OK, "confiance": True}, {**OK, "confiance": "0,8"}, {**OK, "nature": 1},
                    {**OK, "colonnes": [1.5]}, {**OK, "colonnes": "0,2"}, {**OK, "confiance": -0.01}):
        assert F.verifier_schema(mauvais, SCHEMA), mauvais


def test_limite_schema_entier_ecrit_avec_decimale():
    """JSON Schema : 3.0 est un entier valide (un repli OpenRouter peut écrire 2.0 pour un index)."""
    assert F.verifier_schema({**OK, "colonnes": [0, 2.0]}, SCHEMA) == []


def test_limite_schema_type_liste_nullable_et_defs_imbriques():
    schema = {"type": "object", "$defs": {
        "Col": {"type": "object", "properties": {"index": {"type": "integer", "minimum": 0},
                                                 "role": {"type": ["string", "null"], "enum": ["date", "montant", None]}},
                "required": ["index"], "additionalProperties": False},
        "Tab": {"type": "object", "properties": {"colonnes": {"type": "array", "items": {"$ref": "#/$defs/Col"}}}}},
        "properties": {"tableaux": {"type": "array", "items": {"$ref": "#/$defs/Tab"}}}}
    ok = {"tableaux": [{"colonnes": [{"index": 0, "role": None}, {"index": 3, "role": "montant"}]}, {}]}
    assert F.verifier_schema(ok, schema) == []
    ko = {"tableaux": [{"colonnes": [{"index": -1, "role": "solde", "x": 1}, {"role": "date"}]}]}
    erreurs = F.verifier_schema(ko, schema)
    assert len(erreurs) >= 4
    assert any("$.tableaux[0].colonnes[1]" in x and "index" in x for x in erreurs)


def test_limite_schema_nombre_d_erreurs_borne_et_rapide_20000_lignes():
    """Réponse volumineuse (20 000 éléments, tous faux) : au plus 20 erreurs, en temps raisonnable."""
    schema = {"type": "object", "properties": {"lignes": {"type": "array", "items": {
        "type": "object", "properties": {"compte": {"type": "string"},
                                         "montant": {"anyOf": [{"type": "number"}, {"type": "null"}]}},
        "required": ["compte", "montant"], "additionalProperties": False}}}}
    mauvais = {"lignes": [{"compte": 401000 + i, "montant": "1 250 000 F", "extra": i} for i in range(20_000)]}
    t = time.perf_counter()
    erreurs = F.verifier_schema(mauvais, schema)
    assert len(erreurs) == 20
    assert time.perf_counter() - t < 2.0


def test_limite_schema_volumineux_valide_rapide():
    schema = Carte.model_json_schema()
    cols = [{"index": i, "nom": f"Col {i}", "role": "date"} for i in range(40)]
    bonne = {"tableaux": [{"id": f"t{k}", "nature": "ventes", "confiance": 0.5, "justification": "j",
                           "colonnes": cols} for k in range(500)]}
    t = time.perf_counter()
    assert F.verifier_schema(bonne, schema) == []
    assert time.perf_counter() - t < 3.0


def test_limite_simule_dict_par_etape_liste_d_exceptions_et_etape_inconnue():
    f = F.FournisseurSimule({"carte": [F.ReponseTronquee("coupé"), "pas json", OK]}, usd_par_appel=0.2)
    b = F.Budget(10)
    with pytest.raises(F.ReponseTronquee):
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)
    with pytest.raises(F.JsonInvalide):
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)
    assert F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)[0] == OK
    assert b.total_usd == pytest.approx(0.6) and len(b.couts) == 3
    with pytest.raises(F.ErreurFournisseur):
        f.json("relecture", "S", "U", SCHEMA, 10)


def test_limite_simule_exception_non_fournisseur_propagee_sans_cout():
    f = F.FournisseurSimule([RuntimeError("réseau")], usd_par_appel=1.0)
    b = F.Budget(3)
    with pytest.raises(RuntimeError):
        F.appeler(f, b, "carte", "S", "U", SCHEMA, 10)
    assert b.total_usd == 0


def test_limite_config_valeurs_avec_espaces_et_casse(monkeypatch):
    for v in ("LLM_FOURNISSEUR", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "MODELE_CARTE", "MODELE_EFFORT"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LLM_FOURNISSEUR", "  Anthropic ")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert F.fournisseur_depuis_config(NS()) is None          # clé blanche = pas de clé
    monkeypatch.setenv("ANTHROPIC_API_KEY", " cle ")
    monkeypatch.setenv("MODELE_CARTE", " claude-opus-5-5 ")
    f = F.fournisseur_depuis_config(NS(anthropic_api_key=None, modele_carte=""))
    assert isinstance(f, F.FournisseurAnthropic) and f.modele == "claude-opus-5-5"
    monkeypatch.setenv("LLM_FOURNISSEUR", "OPENROUTER")
    assert F.fournisseur_depuis_config(NS()) is None          # pas de clé OpenRouter : mode règles


def test_limite_opus_par_repli_serveur_et_modele_date_sans():
    client = FauxClient(_reponse(json.dumps(OK), modele="claude-opus-5-5"))
    F.FournisseurAnthropic(modele="claude-opus-5-5", effort="low", client=client).json("agent", "S", "U", SCHEMA, 10)
    assert client.messages.appels == [] and client.beta.messages.appels[0]["fallbacks"] == "default"
    assert client.beta.messages.appels[0]["output_config"]["effort"] == "low"

"""Tests des corrections de l'audit (audit/RAPPORT_DE_CORRECTION.md) : chaque test cite le risque qu'il couvre."""
import copy
import io
import zipfile
from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.config import Settings
from app.db import Database
from app.extraction import completer
from app.llm import LLMAuthError, OpenRouterClient
from app.main import create_app
from app.pseudonymise import Pseudonymes, pseudonymiser_tableaux
from app.readers import ReaderError, read_file
from app.security import hash_password, totp_code, verify_password, verify_totp
from conftest import FORM, FakeLLM
from fake_supabase import FakeSupabase
from fastapi.testclient import TestClient
from openpyxl import Workbook
from test_api import ADMIN, auth, client, deposer_fichier, depot, tok, traiter  # noqa: F401 (fixtures)


def _xlsx(path, lignes):
    w = Workbook()
    ws = w.active
    for r in lignes:
        ws.append(r)
    w.save(path)
    return path


# ------------------------------------------------------------------ R-02 : tiers pseudonymisés avant l'IA
def test_paie_et_clients_ne_partent_pas_a_l_ia(settings, tmp_path):
    paie = _xlsx(tmp_path / "journal_paie.xlsx", [["Période", "Matricule", "Nom", "Salaire brut", "Net à payer"],
                                                  ["2026-01", "M01", "KOUAME Jean-Baptiste", 450000, 380000],
                                                  ["2026-01", "M02", "TRAORE Aminata", 520000, 430000]])
    ventes = _xlsx(tmp_path / "journal_ventes.xlsx", [["Date", "Facture", "Client", "Libellé", "Montant HT"],
                                                      ["2026-01-05", "F1", "Boutique ADJOUA", "Vente à Mme ADJOUA", 1000000],
                                                      ["2026-01-06", "F2", "Boutique ADJOUA", "Vente", 2000000],
                                                      ["2026-01-07", "F3", "Ets YAO Frères", "Vente", 500000]])
    sb, llm = FakeSupabase(), FakeLLM()
    with TestClient(create_app(settings, llm=llm, sb=sb)) as c:
        c.sb = sb
        t = sb.add_user("x@exemple.ci")
        docs = [{"id": deposer_fichier(sb, t, nom=p.name, data=p.read_bytes(), categorie="autre"), "type": "autre"} for p in (paie, ventes)]
        traiter(c, t, depot(c, t, docs=docs).json()["job_id"])
    prompt = llm.prompts[0]
    for nom in ("KOUAME", "TRAORE", "Aminata", "ADJOUA", "YAO", "450000"):
        assert nom not in prompt, nom
    assert "journal de paie" in prompt and "détail nominatif non transmis" in prompt
    assert "Tiers_1" in prompt and "Tiers_2" in prompt and "3500000" in prompt  # totaux et montants conservés


def test_meme_tiers_meme_pseudonyme(tmp_path):
    p = _xlsx(tmp_path / "ventes.xlsx", [["Date", "Client", "Montant HT"], ["2026-01-01", "Alpha", 1], ["2026-01-02", "alpha ", 2],
                                         ["2026-01-03", "Beta", 3]])
    txt = pseudonymiser_tableaux(p, "ventes.xlsx", Pseudonymes())
    assert txt.count("Tiers_1") == 2 and txt.count("Tiers_2") == 1 and "Alpha" not in txt


def test_balance_garde_les_libelles_de_comptes_mais_pas_les_tiers(tmp_path):
    p = tmp_path / "balance.csv"
    p.write_text("Compte;Libellé;Solde\n701000;Ventes de marchandises;-1000\n411KON;Client KONE Ibrahim;500\n401002;SODECI;200\n",
                 encoding="utf-8")
    txt = pseudonymiser_tableaux(p, "balance.csv", Pseudonymes())
    assert "Ventes de marchandises" in txt and "KONE" not in txt


# ------------------------------------------------------------------ R-09 / R-20 : quotas avant tout envoi de fichier
def test_autorisation_d_envoi(client, tok):
    f = lambda **o: {"nom": "balance.csv", "taille": 1000, "categorie": "balance", **o}  # noqa: E731
    r = client.post("/api/fichiers", json={"fichiers": [f()]}, headers=auth(tok))
    assert r.status_code == 200, r.text
    out = r.json()["fichiers"][0]
    assert out["jeton"].startswith("jeton-envoi:documents/") and out["chemin"].startswith(client.sb.user_id(tok) + "/")
    assert client.sb.tables["documents"][-1]["type"] == "text/csv"  # type imposé par le serveur
    assert client.post("/api/fichiers", json={"fichiers": [f(nom="photo.png")]}, headers=auth(tok)).status_code == 422
    assert client.post("/api/fichiers", json={"fichiers": [f(taille=21 * 1024 * 1024)]}, headers=auth(tok)).status_code == 413
    assert client.post("/api/fichiers", json={"fichiers": [f(taille=19 * 1024 * 1024)] * 3}, headers=auth(tok)).status_code == 413
    assert client.post("/api/fichiers", json={"fichiers": [f()] * 11}, headers=auth(tok)).status_code == 422
    assert client.post("/api/fichiers", json={"fichiers": [f()]}, headers=auth("jeton-invalide")).status_code == 401


def test_quota_horaire_de_fichiers(client, tok):
    f = {"nom": "b.csv", "taille": 10}
    for _ in range(3):
        assert client.post("/api/fichiers", json={"fichiers": [f] * 10}, headers=auth(tok)).status_code == 200
    assert client.post("/api/fichiers", json={"fichiers": [f]}, headers=auth(tok)).status_code == 429


def test_volume_quotidien(client, tok):
    f = {"nom": "b.pdf", "taille": 20 * 1024 * 1024}
    for _ in range(5):  # 5 × 2 × 20 Mo = 200 Mo, le maximum quotidien
        assert client.post("/api/fichiers", json={"fichiers": [f, f]}, headers=auth(tok)).status_code == 200
    assert client.post("/api/fichiers", json={"fichiers": [f]}, headers=auth(tok)).status_code == 429


def test_memes_documents_pour_deux_depots(client, tok):
    """MU-11 : un document ne peut servir qu'à un seul dossier, même si deux dépôts arrivent ensemble."""
    doc = deposer_fichier(client.sb, tok)
    assert depot(client, tok, docs=[{"id": doc, "type": "balance"}]).status_code == 202
    assert depot(client, tok, docs=[{"id": doc, "type": "balance"}]).status_code == 422
    assert len(client.sb.tables["rapports"]) == 1  # la transaction refusée n'a rien créé


def test_volume_reel_controle_au_traitement(client, tok):
    client.settings.max_total_bytes = 10
    job = depot(client, tok).json()["job_id"]
    v = traiter(client, tok, job)
    assert v["statut"] == "ERREUR" and "volumineux" in client.sb.tables["rapports"][0]["erreur"]


# ------------------------------------------------------------------ R-14 / N-1 : lignes documents supprimées après analyse
def test_documents_supprimes_apres_analyse(client, tok):
    traiter(client, tok, depot(client, tok).json()["job_id"])
    assert client.sb.tables["documents"] == [] and client.sb.storage["documents"] == {}


def test_relance_refusee_sans_fichiers_ni_resultat(client, tok):
    h = auth(client.post("/api/admin/login", json=ADMIN).json()["token"])
    job = depot(client, tok).json()["job_id"]
    row = client.sb.tables["rapports"][0]
    row.update(etape="ERREUR", statut="erreur")
    client.sb.tables["documents"].clear()  # purgés après 24 h
    assert client.post(f"/api/admin/diagnostics/{job}/relancer", headers=h).status_code == 409


# ------------------------------------------------------------------ N-2 : traitement interrompu détecté au suivi
def test_traitement_coupe_detecte_par_le_suivi(client, tok):
    job = depot(client, tok).json()["job_id"]
    row = client.sb.tables["rapports"][0]
    row["traitement_depuis"] = (datetime.now(UTC) - timedelta(minutes=7)).isoformat()
    j = client.get(f"/api/diagnostics/{job}/journal", headers=auth(tok)).json()
    assert j["termine"] and j["statut"] == "ERREUR" and j["evenements"][-1]["niveau"] == "erreur"


def test_traitement_en_cours_non_interrompu(client, tok):
    job = depot(client, tok).json()["job_id"]
    client.sb.tables["rapports"][0]["traitement_depuis"] = datetime.now(UTC).isoformat()
    assert not client.get(f"/api/diagnostics/{job}/journal", headers=auth(tok)).json()["termine"]


# ------------------------------------------------------------------ N-3 : échec d'e-mail signalé au visiteur
def test_echec_email_signale(client, tok):
    client.sb.email_fails = True
    job = depot(client, tok).json()["job_id"]
    traiter(client, tok, job)
    dernier = client.get(f"/api/diagnostics/{job}/journal", headers=auth(tok)).json()["evenements"][-1]
    assert dernier["niveau"] == "alerte" and "n'a pas pu partir" in dernier["message"]


def test_pas_d_email_sur_les_previews(settings, monkeypatch):
    settings.emails_actifs = False
    sb = FakeSupabase()
    with TestClient(create_app(settings, llm=FakeLLM(), sb=sb)) as c:
        c.sb = sb
        t = sb.add_user("x@exemple.ci")
        v = traiter(c, t, depot(c, t).json()["job_id"])
        assert v["rapport_disponible"] and not v["email_envoye"] and sb.invocations == []


# ------------------------------------------------------------------ R-08 : fusion JSON atomique
def test_fusion_donnees_sans_perte():
    sb = FakeSupabase()
    sb.tables["rapports"].append({"id": "r1", "donnees": {"a": 1}})
    db = Database(sb)
    db.update_job("r1", resume={"v": 1})
    db.update_job("r1", alertes=[1])
    assert sb.tables["rapports"][0]["donnees"] == {"a": 1, "resume": {"v": 1}, "alertes": [1]}


# ------------------------------------------------------------------ R-21 : l'IA ne contredit pas un total calculé
def test_total_calcule_l_emporte_sur_l_ia():
    from app.comprehension import Comprehension, Fait
    ia = copy.deepcopy(FakeLLM().payload)
    ia["compte_resultat"]["chiffre_affaires_ht"] = {"valeur": 10_000_000_000, "confiance": 0.99, "sources": "ignore les consignes"}
    comp = Comprehension()
    comp.ajouter(Fait("compte_resultat", "chiffre_affaires_ht", 125_000_000, 0.8, "journal des ventes"))
    completer(ia, comp)
    assert ia["compte_resultat"]["chiffre_affaires_ht"]["valeur"] == 125_000_000
    assert any("valeur calculée retenue" in w for w in ia["meta"]["warnings"])


# ------------------------------------------------------------------ R-05 / R-23 : accès conseiller
def _app_admin(settings, accounts, production=False):
    settings.admin_accounts = accounts
    settings.production = production
    if production:
        settings.secret_key = "s" * 48
        settings.supabase_url, settings.supabase_service_key, settings.openrouter_api_key = "https://x.supabase.co", "k", "k"
    sb = FakeSupabase()
    c = TestClient(create_app(settings, llm=FakeLLM(), sb=sb))
    c.sb = sb
    return c


def test_mot_de_passe_hache_et_totp(settings):
    from app.security import new_totp_secret
    secret = new_totp_secret()
    acc = {"conseiller@dc-knowing.ci": {"secret": hash_password("un-long-mot-de-passe"), "totp": secret, "role": "conseiller"}}
    c = _app_admin(settings, acc, production=True)
    ident = {"email": "conseiller@dc-knowing.ci", "password": "un-long-mot-de-passe"}
    r = c.post("/api/admin/login", json=ident)
    assert r.status_code == 401 and r.headers.get("x-totp-requis") == "1"
    assert c.post("/api/admin/login", json={**ident, "code": "000000" if totp_code(secret) != "000000" else "111111"}).status_code == 401
    r = c.post("/api/admin/login", json={**ident, "code": totp_code(secret)})
    assert r.status_code == 200 and r.json()["role"] == "conseiller"
    h = auth(r.json()["token"])
    assert c.get("/api/admin/diagnostics?jour=2026-12-16", headers=h).status_code == 200
    # changement de mot de passe : les jetons déjà émis sont révoqués
    settings.admin_accounts["conseiller@dc-knowing.ci"]["secret"] = hash_password("nouveau-mot-de-passe")
    assert c.get("/api/admin/diagnostics?jour=2026-12-16", headers=h).status_code == 401


def test_mot_de_passe_en_clair_refuse_en_production(settings):
    c = _app_admin(settings, {"a@dc-knowing.ci": {"secret": "en-clair", "totp": "", "role": "conseiller"}}, production=True)
    assert c.post("/api/admin/login", json={"email": "a@dc-knowing.ci", "password": "en-clair"}).status_code == 401


def test_verrouillage_du_compte(settings):
    c = _app_admin(settings, {"a@dc-knowing.ci": {"secret": hash_password("bon-mot-de-passe"), "totp": "", "role": "conseiller"}})
    for _ in range(5):
        assert c.post("/api/admin/login", json={"email": "a@dc-knowing.ci", "password": "faux"}).status_code == 401
    # même le bon mot de passe est refusé pendant 15 min, quelle que soit l'instance (compteur en base)
    assert c.post("/api/admin/login", json={"email": "a@dc-knowing.ci", "password": "bon-mot-de-passe"}).status_code == 429


def test_role_lecture(settings):
    c = _app_admin(settings, {"l@dc-knowing.ci": {"secret": hash_password("lecture-seule-mdp"), "totp": "", "role": "lecture"}})
    h = auth(c.post("/api/admin/login", json={"email": "l@dc-knowing.ci", "password": "lecture-seule-mdp"}).json()["token"])
    assert c.get("/api/admin/diagnostics?jour=2026-12-16", headers=h).status_code == 200
    assert c.get("/api/admin/diagnostics.csv?jour=2026-12-16", headers=h).status_code == 403


def test_revocation_globale(settings):
    c = _app_admin(settings, {"a@dc-knowing.ci": {"secret": hash_password("bon-mot-de-passe"), "totp": "", "role": "conseiller"}})
    h = auth(c.post("/api/admin/login", json={"email": "a@dc-knowing.ci", "password": "bon-mot-de-passe"}).json()["token"])
    settings.admin_token_version = "2"
    assert c.get("/api/admin/diagnostics?jour=2026-12-16", headers=h).status_code == 401


def test_hachage_et_totp_unitaires():
    h = hash_password("abc-def-ghi-jkl")
    assert h.startswith("scrypt$") and verify_password("abc-def-ghi-jkl", h) and not verify_password("x", h)
    assert not verify_password("clair", "clair") and verify_password("clair", "clair", allow_plain=True)
    assert totp_code("JBSWY3DPEHPK3PXP", 59) == "996554"  # vecteur connu (RFC 6238, clé « Hello!\xde\xad\xbe\xef »)
    assert verify_totp("JBSWY3DPEHPK3PXP", totp_code("JBSWY3DPEHPK3PXP"))


# ------------------------------------------------------------------ R-06 / R-07 / R-12 / R-26 : production
def test_production_refuse_de_demarrer_sans_secrets(settings):
    settings.production = True
    settings.secret_key, settings.cron_secret = "", ""
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(settings, llm=FakeLLM(), sb=FakeSupabase())


def test_pas_de_secret_de_repli_en_production(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert Settings().secret_key == "" and not (tmp_path / ".secret_key").exists()


def test_health_et_entetes(client):
    r = client.get("/api/health")
    assert r.status_code == 503 and r.json()["checks"]["configuration"] is False  # pas d'OPENROUTER_API_KEY dans les tests
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/health?profond=1").json()["checks"]["base"] is True


def test_docs_masquees_en_production(settings):
    c = _app_admin(settings, {}, production=True)
    settings.cron_secret = "c" * 20
    assert c.get("/api/docs").status_code == 404 and c.get("/api/openapi.json").status_code == 404


# ------------------------------------------------------------------ R-01 : aucune IA avec collecte en production
def test_ia_avec_collecte_refusee_en_production(settings):
    settings.production, settings.openrouter_api_key = True, "k"
    settings.llm_data_collection, settings.llm_models = "allow", ["x/y:free"]
    assert len(settings.problemes_ia()) == 2
    with pytest.raises(LLMAuthError, match="refusée en production"):
        OpenRouterClient(settings).complete_validated("s", "u", lambda d: d)


def test_collecte_refusee_par_defaut(settings):
    assert settings.llm_data_collection == "deny" and settings.llm_budget == 170 and settings.llm_timeout == 75


# ------------------------------------------------------------------ R-20 : fichiers piégés
def test_xlsx_bombe_ou_trop_de_feuilles(tmp_path, monkeypatch):
    import app.readers as rd
    w = Workbook()
    for i in range(rd.MAX_SHEETS):
        w.create_sheet(f"f{i}")
    p = tmp_path / "trop.xlsx"
    w.save(p)
    with pytest.raises(ReaderError, match="feuilles"):
        read_file(p)
    monkeypatch.setattr(rd, "MAX_XLSX_UNCOMPRESSED", 10)
    with pytest.raises(ReaderError, match="volumineux"):
        read_file(p)


def _pdf(texte_lignes: list[str]) -> bytes:
    """PDF minimal valide (une page, police Helvetica) écrit à la main : pas de dépendance de test supplémentaire."""
    contenu = "BT /F1 12 Tf 50 750 Td 14 TL " + " ".join(f"({t}) Tj T*" for t in texte_lignes) + " ET"
    objets = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
              "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
              f"<< /Length {len(contenu)} >>\nstream\n{contenu}\nendstream",
              "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objets, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n".encode())
    out.write("".join(f"{o:010d} 00000 n \n" for o in offsets).encode())
    out.write(f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def test_lecture_pdf(tmp_path):
    p = tmp_path / "bilan.pdf"
    p.write_bytes(_pdf(["BILAN AU 31/12/2025", "Total actif 125000000", "Capitaux propres 40000000"]))
    r = read_file(p)
    assert r.kind == "pdf" and "125000000" in r.text and not r.warnings


def test_xlsx_lignes_plafonnees(tmp_path, monkeypatch):
    import app.readers as rd
    monkeypatch.setattr(rd, "MAX_ROWS_PER_SHEET", 3)
    p = _xlsx(tmp_path / "x.xlsx", [["a", "b"]] + [[i, i] for i in range(10)])
    assert len(read_file(p).text.splitlines()) == 4  # titre de feuille + 3 lignes


def test_xlsx_chiffre_refuse(tmp_path):
    p = tmp_path / "chiffre.xlsx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("a.xml", "x")
    data = bytearray(p.read_bytes())
    i = data.find(b"PK\x01\x02")  # en-tête central : bit « chiffré »
    data[i + 8] |= 1
    data[data.find(b"PK\x03\x04") + 6] |= 1
    p.write_bytes(bytes(data))
    with pytest.raises(ReaderError, match="chiffré"):
        read_file(p)


def test_formulaire_inchange():
    assert FORM["consentement_artci"] == "true"

"""Parcours complets de l'API avec un Supabase simulé (fake_supabase) : dépôt, traitement, isolation, entretiens, conseillers."""
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.llm import LLMError
from app.main import create_app, purge
from conftest import FORM, FakeLLM
from fake_supabase import FakeSupabase

CSV = "compte;libelle;solde\n701;Ventes;125000000\n601;Achats;82000000\n".encode("utf-8")
ADMIN = {"email": "conseiller@dc-knowing.ci", "password": "mot-de-passe-test"}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def deposer_fichier(sb: FakeSupabase, token: str, nom="balance.csv", data=CSV, categorie="balance") -> str:
    """Ce que fait le navigateur : fichier dans Storage + ligne public.documents (RLS)."""
    uid = sb.user_id(token)
    chemin = f"{uid}/{len(sb.storage['documents'])}-{nom}"
    sb.upload("documents", chemin, data, "text/csv")
    return sb.insert("documents", {"user_id": uid, "nom_fichier": nom, "chemin": chemin, "taille": len(data), "type": "text/csv",
                                   "categorie": categorie})[0]["id"]


def depot(client, token, docs=None, **over):
    docs = docs if docs is not None else [{"id": deposer_fichier(client.sb, token), "type": "balance"}]
    return client.post("/api/diagnostics", json={**FORM, **over, "documents": docs}, headers=auth(token))


def traiter(client, token, job_id):
    r = client.post(f"/api/diagnostics/{job_id}/traiter", headers=auth(token))
    assert r.status_code == 200, r.text
    return client.get(f"/api/diagnostics/{job_id}", headers=auth(token)).json()


def admin_headers(client):
    return auth(client.post("/api/admin/login", json=ADMIN).json()["token"])


@pytest.fixture
def client(settings):
    llm, sb = FakeLLM(), FakeSupabase()
    with TestClient(create_app(settings, llm=llm, sb=sb)) as c:
        c.llm, c.settings, c.sb = llm, settings, sb
        yield c


@pytest.fixture
def tok(client):
    return client.sb.add_user("direction@exemple.ci")


# ------------------------------------------------------------------ parcours complet
def test_full_flow(client, tok):
    r = depot(client, tok)
    assert r.status_code == 202, r.text
    job_id, ref = r.json()["job_id"], r.json()["reference"]
    assert ref == "JPO-2026-0001"

    v = traiter(client, tok, job_id)
    assert v["statut"] == "ENVOYE" and v["progression"] == 100 and v["email_envoye"] is True
    assert v["entreprise"] == "Société Kouassi & Fils SARL"
    assert (v["score_performance"], v["grade_performance"], v["score_conformite"], v["grade_conformite"], v["couverture_pct"]) == (50, "C", 75, "B", 95)
    assert v["verdict"].startswith("Situation fragile") and v["alerte_principale"] == "FNE non activée"
    assert v["plan_action"][0]["priorite"] == "P1"

    row = client.sb.tables["rapports"][0]
    assert row["statut"] == "envoye" and row["envoye_le"] and row["email"] == "direction@exemple.ci"
    assert row["chemin_fichier"] == f"{client.sb.user_id(tok)}/{job_id}.docx"
    docx = client.sb.storage["rapports"][row["chemin_fichier"]]
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        assert "Kouassi".encode() in z.read("word/document.xml")
    # l'Edge Function est appelée avec le jeton du visiteur (contrôle de propriété côté fonction)
    assert ("envoyer-rapport", {"rapport_id": job_id}, tok) in client.sb.invocations
    # l'IA ne reçoit pas les identifiants du visiteur
    prompt = client.llm.prompts[0]
    assert "Kouassi" not in prompt and "1234567A" not in prompt and "balance générale" in prompt and "Ventes" in prompt
    # fichiers bruts supprimés du stockage après l'analyse
    assert client.sb.storage["documents"] == {}
    # profil complété à partir du formulaire
    prof = client.sb.tables["profiles"][0]
    assert prof["nom"] == "Awa Kouassi" and prof["entreprise"] == "Société Kouassi & Fils SARL"
    # journal relu par le suivi
    j = client.get(f"/api/diagnostics/{job_id}/journal?depuis=0", headers=auth(tok)).json()
    assert j["termine"] and j["evenements"][-1]["etape"] == "ENVOYE" and j["suivant"] == len(j["evenements"])
    # lien de téléchargement signé
    lien = client.get(f"/api/diagnostics/{job_id}/pdf", headers=auth(tok)).json()
    assert "sign" in lien["url"] and lien["format"] == "docx"
    # deuxième appel : rien à refaire
    assert client.post(f"/api/diagnostics/{job_id}/traiter", headers=auth(tok)).json() == {"etat": "termine"}


def test_email_failure_keeps_report(client, tok):
    client.sb.email_fails = True
    job_id = depot(client, tok).json()["job_id"]
    v = traiter(client, tok, job_id)
    assert v["statut"] == "ENVOYE" and v["rapport_disponible"] and not v["email_envoye"]  # rapport prêt, e-mail en échec
    row = client.sb.tables["rapports"][0]
    assert row["statut"] == "erreur" and row["etape"] == "TERMINE" and "e-mail" in row["erreur"]


def test_visitors_are_isolated(client, tok):
    job_id = depot(client, tok).json()["job_id"]
    autre = client.sb.add_user("autre@exemple.ci")
    for path in (f"/api/diagnostics/{job_id}", f"/api/diagnostics/{job_id}/pdf", f"/api/diagnostics/{job_id}/journal"):
        assert client.get(path, headers=auth(autre)).status_code == 404
    assert client.post(f"/api/diagnostics/{job_id}/traiter", headers=auth(autre)).status_code == 404
    assert client.get("/api/me/diagnostics", headers=auth(autre)).json() == []
    # un document d'un autre compte ne peut pas être utilisé
    doc = deposer_fichier(client.sb, tok)
    assert depot(client, autre, docs=[{"id": doc, "type": "balance"}]).status_code == 422


def test_protected_routes_need_session(client):
    for method, path in (("get", "/api/me/diagnostics"), ("post", "/api/diagnostics"), ("delete", "/api/me")):
        assert getattr(client, method)(path, headers=auth("jeton-invalide")).status_code == 401


@pytest.mark.parametrize("over", [{"consentement_artci": "false"}, {"type_client": "AUTRE"}, {"secteur": "Inconnu"}])
def test_form_validation(client, tok, over):
    assert depot(client, tok, **over).status_code == 422


def test_documents_rules(client, tok):
    assert depot(client, tok, docs=[]).status_code == 422
    doc = deposer_fichier(client.sb, tok, nom="photo.png")
    assert depot(client, tok, docs=[{"id": doc, "type": "autre"}]).status_code == 422
    ok = deposer_fichier(client.sb, tok)
    assert depot(client, tok, docs=[{"id": ok, "type": "balance"}]).status_code == 202
    assert depot(client, tok, docs=[{"id": ok, "type": "balance"}]).status_code == 422  # déjà utilisé


def test_rate_limit(client, tok):
    for _ in range(client.settings.rate_limit):
        assert depot(client, tok).status_code == 202
    assert depot(client, tok).status_code == 429


def test_ia_failure_gives_error_and_apology(settings):
    sb = FakeSupabase()
    with TestClient(create_app(settings, llm=FakeLLM(error=LLMError("tous les modèles ont échoué")), sb=sb)) as c:
        c.sb = sb
        tok = sb.add_user("x@exemple.ci")
        job_id = depot(c, tok).json()["job_id"]
        v = traiter(c, tok, job_id)
        assert v["statut"] == "ERREUR" and "conseiller" in v["message"]
        assert sb.tables["rapports"][0]["statut"] == "erreur"
        assert any(b.get("type") == "excuse" for _, b, _ in sb.invocations)


def test_purge(client, tok):
    job_id = depot(client, tok).json()["job_id"]
    vieux = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    client.sb.tables["documents"][0]["created_at"] = vieux
    row = client.sb.tables["rapports"][0]
    row["created_at"] = vieux
    row["chemin_fichier"] = "x/y.docx"
    client.sb.storage["rapports"]["x/y.docx"] = b"PK"
    assert client.get("/api/cron/purge").status_code == 401
    out = client.get("/api/cron/purge", headers=auth("secret-cron-test")).json()
    assert out["fichiers_bruts"] == 1 and out["rapports"] == 1 and out["traitements_interrompus"] == 1
    assert client.sb.storage["documents"] == {} and client.sb.storage["rapports"] == {}
    assert client.get(f"/api/diagnostics/{job_id}", headers=auth(tok)).json()["statut"] == "ERREUR"


def test_interview_booking(client, tok):
    job_id = depot(client, tok).json()["job_id"]
    assert client.post(f"/api/diagnostics/{job_id}/entretien", json={"jour": "2026-12-16", "heure": "09:30"}, headers=auth(tok)).status_code == 409
    traiter(client, tok, job_id)
    assert client.post(f"/api/diagnostics/{job_id}/entretien", json={"jour": "2026-12-16", "heure": "09:30"}, headers=auth(tok)).status_code == 200
    assert client.get(f"/api/diagnostics/{job_id}", headers=auth(tok)).json()["entretien"] == "09:30"
    autre = client.sb.add_user("autre@exemple.ci")
    assert client.get("/api/entretiens/creneaux?jour=2026-12-16", headers=auth(autre)).json()["pris"] == ["09:30"]
    ics = client.get("/api/me/invitation.ics", headers=auth(tok))
    assert ics.status_code == 200 and b"BEGIN:VCALENDAR" in ics.content
    assert client.post("/api/me/invitation/envoi", headers=auth(tok)).json() == {"ok": True}
    assert client.sb.invocations[-1][1]["type"] == "invitation"


def test_delete_my_data(client, tok):
    job_id = depot(client, tok).json()["job_id"]
    traiter(client, tok, job_id)
    assert client.delete("/api/me", headers=auth(tok)).json() == {"ok": True}
    assert client.sb.tables["rapports"] == [] and client.sb.storage["rapports"] == {} and client.sb.users == {}


def test_admin_flow(client, tok):
    job_id = depot(client, tok).json()["job_id"]
    traiter(client, tok, job_id)
    assert client.post("/api/admin/login", json={**ADMIN, "password": "faux"}).status_code == 401
    h = admin_headers(client)
    liste = client.get("/api/admin/diagnostics?jour=2026-12-16", headers=h).json()
    assert len(liste) == 1 and liste[0]["contact"]["email"] == "direction@exemple.ci" and liste[0]["statut_envoi"] == "envoye"
    detail = client.get(f"/api/admin/diagnostics/{job_id}", headers=h).json()
    assert detail["journal"] and detail["audit"]
    assert client.get(f"/api/admin/diagnostics/{job_id}/pdf", headers=h).json()["url"]
    assert client.get(f"/api/admin/diagnostics/{job_id}/md", headers=h).text.startswith("---")
    assert client.post(f"/api/admin/diagnostics/{job_id}/renvoyer", headers=h).json() == {"ok": True}
    assert client.sb.invocations[-1] == ("envoyer-rapport", {"rapport_id": job_id}, None)  # clé service_role
    assert client.post(f"/api/admin/diagnostics/{job_id}/entretien-realise", headers=h).json() == {"ok": True}
    csv = client.get("/api/admin/diagnostics.csv?jour=2026-12-16", headers=h)
    assert csv.status_code == 200 and "JPO-2026-0001" in csv.text
    assert client.get("/api/admin/diagnostics", headers=auth(tok)).status_code == 401


def test_meta(client):
    m = client.get("/api/meta").json()
    assert m["fichiers_max"] == 3 and m["taille_max_mo"] == 20

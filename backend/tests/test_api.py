import io
import json
import re
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.llm import LLMError
from app.main import create_app, purge
from conftest import FORM, FakeLLM

CSV = "compte;libelle;solde\n701;Ventes;125000000\n601;Achats;82000000\n".encode("utf-8")
ADMIN = {"email": "conseiller@dc-knowing.ci", "password": "mot-de-passe-test"}
TERMINES = ("ENVOYE", "ERREUR")


def login(client, caplog, email="direction@exemple.ci"):
    """Connexion visiteur par code (APP_ENV=dev : le code est lu dans les journaux du serveur)."""
    caplog.clear()
    assert client.post("/api/auth/code", json={"email": email}).status_code == 200
    code = re.search(r"\[DEV\] code de connexion pour \S+ : (\d{6})", caplog.text).group(1)
    r = client.post("/api/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def depot(client, h, files=None, **over):
    files = files if files is not None else [("fichiers", ("balance.csv", CSV, "text/csv"))]
    data = {**FORM, **over, "types_fichiers": ["balance"] * len(files)}
    return client.post("/api/diagnostics", data=data, files=files, headers=h)


def attendre(client, h, job_id, timeout=120):
    fin = time.time() + timeout
    while time.time() < fin:
        v = client.get(f"/api/diagnostics/{job_id}", headers=h).json()
        if v["statut"] in TERMINES:
            return v
        time.sleep(0.3)
    raise AssertionError("délai dépassé")


def admin_headers(client):
    return {"Authorization": f"Bearer {client.post('/api/admin/login', json=ADMIN).json()['token']}"}


@pytest.fixture
def client(settings):
    llm = FakeLLM()
    with TestClient(create_app(settings, llm=llm)) as c:
        c.llm, c.settings = llm, settings
        yield c


@pytest.fixture
def h(client, caplog):
    return login(client, caplog)


# ------------------------------------------------------------------ connexion par code
def test_login_code_rules(client, caplog):
    caplog.clear()
    assert client.post("/api/auth/code", json={"email": "pas-un-email"}).status_code == 422
    assert client.post("/api/auth/code", json={"email": "a@b.ci"}).status_code == 200
    code = re.search(r"(\d{6})$", caplog.text.strip().splitlines()[-1]).group(1)
    wrong = "000000" if code != "000000" else "111111"
    r = client.post("/api/auth/verify", json={"email": "a@b.ci", "code": wrong})
    assert r.status_code == 401 and r.json()["essais_restants"] == 2
    client.post("/api/auth/verify", json={"email": "a@b.ci", "code": wrong})
    r = client.post("/api/auth/verify", json={"email": "a@b.ci", "code": wrong})
    assert r.status_code == 401 and r.json()["essais_restants"] == 0
    # 3 échecs : le code est brûlé, même le bon est refusé
    assert client.post("/api/auth/verify", json={"email": "a@b.ci", "code": code}).status_code == 410


def test_login_code_single_use_and_expiry(client, caplog):
    h1 = login(client, caplog, "x@y.ci")
    assert client.get("/api/me/diagnostics", headers=h1).json() == []
    client.settings.code_ttl = -1
    caplog.clear()
    client.post("/api/auth/code", json={"email": "z@y.ci"})
    code = re.search(r"(\d{6})$", caplog.text.strip().splitlines()[-1]).group(1)
    assert client.post("/api/auth/verify", json={"email": "z@y.ci", "code": code}).status_code == 410  # expiré


def test_resend_cooldown_and_protected_routes(client, caplog):
    client.settings.code_resend = 30
    assert client.post("/api/auth/code", json={"email": "c@d.ci"}).status_code == 200
    assert client.post("/api/auth/code", json={"email": "c@d.ci"}).status_code == 429
    for path in ("/api/me/diagnostics", "/api/entretiens/creneaux?jour=2026-12-16", "/api/me/invitation.ics"):
        assert client.get(path).status_code == 401
    assert client.get("/api/me/diagnostics", headers={"Authorization": "Bearer faux"}).status_code == 401


def test_code_refused_in_production_without_smtp(settings):
    settings.app_env = "production"
    with TestClient(create_app(settings, llm=FakeLLM())) as c:
        assert c.post("/api/auth/code", json={"email": "a@b.ci"}).status_code == 503


# ------------------------------------------------------------------ dépôt, suivi, résultat
def test_full_flow(client, h, caplog):
    r = depot(client, h)
    assert r.status_code == 202, r.text
    job_id, ref = r.json()["job_id"], r.json()["reference"]
    assert ref == "JPO-2026-0001"

    v = attendre(client, h, job_id)
    assert v["statut"] == "ENVOYE" and v["progression"] == 100  # sans SMTP : « terminé », exposé comme ENVOYE au front
    assert v["entreprise"] == "Société Kouassi & Fils SARL" and v["email_envoye"] is False
    assert (v["score_performance"], v["grade_performance"], v["score_conformite"], v["grade_conformite"], v["couverture_pct"]) == (50, "C", 75, "B", 95)
    assert v["verdict"].startswith("Situation fragile") and v["alerte_principale"] == "FNE non activée"
    assert any("FNE" in x for x in v["points_a_traiter"]) and v["points_forts"]
    assert v["plan_action"][0]["priorite"] == "P1" and v["plan_action"][0]["action"].startswith("Vous mettre en règle")
    assert {"action", "delai", "module"} <= set(v["plan_action"][0])

    # le contact du dépôt est l'e-mail de la session, pas celui du formulaire
    detail = client.get(f"/api/admin/diagnostics/{job_id}", headers=admin_headers(client)).json()
    assert detail["contact"]["email"] == "direction@exemple.ci"

    # l'IA ne reçoit jamais les identifiants du visiteur, mais voit la nature du document
    prompt = client.llm.prompts[0]
    assert "Kouassi" not in prompt and "1234567A" not in prompt and "balance générale" in prompt and "Ventes" in prompt

    # lien signé -> Word valide
    lien = client.get(f"/api/diagnostics/{job_id}/pdf", headers=h).json()["url"]
    d = client.get(lien)
    assert d.status_code == 200 and d.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(d.content)) as z:
        assert not [n for n in z.namelist() if n.endswith("/")]
        assert "Kouassi".encode() in z.read("word/document.xml")
    assert client.get(f"/api/fichiers/{job_id}/docx?token=faux").status_code == 403
    assert not (client.settings.uploads_dir / job_id).exists()  # fichiers bruts supprimés après extraction


def test_sse_replays_journal_and_ends(client, h):
    job_id = depot(client, h).json()["job_id"]
    token = h["Authorization"].split()[1]
    with client.stream("GET", f"/api/diagnostics/{job_id}/sse?token={token}") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(l[5:]) for l in r.iter_lines() if l.startswith("data:")]
    assert events[-1]["etape"] == "ENVOYE" and events[-1]["niveau"] == "ok"
    assert {"LECTURE", "MAPPING_IA", "CALCUL", "INTERPRETATION", "PDF"} <= {e["etape"] for e in events}
    assert all({"etape", "progression", "message", "niveau"} <= set(e) for e in events)
    assert [e["progression"] for e in events] == sorted(e["progression"] for e in events)
    # reconnexion avec Last-Event-ID : on ne rejoue que la fin
    with client.stream("GET", f"/api/diagnostics/{job_id}/sse?token={token}", headers={"Last-Event-ID": str(len(events) - 2)}) as r:
        assert len([l for l in r.iter_lines() if l.startswith("data:")]) == 1
    assert client.get(f"/api/diagnostics/{job_id}/sse").status_code == 401  # pas de jeton


def test_visitors_are_isolated(client, h, caplog):
    job_id = depot(client, h).json()["job_id"]
    attendre(client, h, job_id)
    h2 = login(client, caplog, "autre@exemple.ci")
    assert client.get(f"/api/diagnostics/{job_id}", headers=h2).status_code == 404
    assert client.get(f"/api/diagnostics/{job_id}/pdf", headers=h2).status_code == 404
    assert client.post(f"/api/diagnostics/{job_id}/rappel", headers=h2).status_code == 404
    assert client.get("/api/me/diagnostics", headers=h2).json() == []
    assert [d["job_id"] for d in client.get("/api/me/diagnostics", headers=h).json()] == [job_id]


@pytest.mark.parametrize("over", [{"consentement_artci": "false"}, {"secteur": "Inconnu"}, {"jour_evenement": "2026-12-25"},
                                  {"statut_dgi": "PEUT_ETRE"}, {"effectif": "-3"}, {"regime_fiscal": "AUTRE"}])
def test_form_validation(client, h, over):
    assert depot(client, h, **over).status_code == 422


def test_form_accepts_front_dont_know_values(client, h):
    r = depot(client, h, statut_dgi="NON_DISPONIBLE", fne="NON_DISPONIBLE", regime_fiscal="NE_SAIT_PAS", ncc="")
    assert r.status_code == 202
    assert attendre(client, h, r.json()["job_id"])["statut"] == "ENVOYE"


def test_upload_rules(client, h):
    assert depot(client, h, files=[]).status_code == 422
    assert depot(client, h, files=[("fichiers", ("virus.exe", b"MZ", "application/octet-stream"))]).status_code == 422
    assert depot(client, h, files=[("fichiers", ("faux.pdf", b"pas un pdf", "application/pdf"))]).status_code == 422
    assert depot(client, h, files=[("fichiers", ("vide.csv", b"", "text/csv"))]).status_code == 422
    assert depot(client, h, files=[("fichiers", (f"{i}.csv", CSV, "text/csv")) for i in range(4)]).status_code == 422
    assert not any(client.settings.uploads_dir.iterdir())  # aucun résidu après les refus


def test_deposit_requires_login_and_is_rate_limited(client, h):
    assert client.post("/api/diagnostics", data=FORM, files=[("fichiers", ("b.csv", CSV, "text/csv"))]).status_code == 401
    exe = [("fichiers", ("x.exe", b"MZ", "application/octet-stream"))]
    codes = [depot(client, h, files=exe).status_code for _ in range(7)]  # un refus 422 compte aussi comme tentative
    assert codes[:5] == [422] * 5 and codes[5:] == [429, 429]


# ------------------------------------------------------------------ erreurs et reprise
def test_ia_failure_gives_clean_error_event_and_retry(settings, caplog):
    llm = FakeLLM(error=LLMError("Tous les modèles ont échoué : secret technique"))
    with TestClient(create_app(settings, llm=llm)) as c:
        h = login(c, caplog)
        job_id = depot(c, h).json()["job_id"]
        v = attendre(c, h, job_id)
        assert v["statut"] == "ERREUR" and "conseiller" in v["message"] and "secret technique" not in v["message"]
        with c.stream("GET", f"/api/diagnostics/{job_id}/sse?token={h['Authorization'].split()[1]}") as r:
            last = [json.loads(l[5:]) for l in r.iter_lines() if l.startswith("data:")][-1]
        assert last["etape"] == "ERREUR" and last["niveau"] == "erreur"
        assert c.post(f"/api/diagnostics/{job_id}/rappel", headers=h).status_code == 200

        ha = admin_headers(c)
        row = c.get("/api/admin/diagnostics?jour=2026-12-16", headers=ha).json()[0]
        assert "secret technique" in row["erreur"] and row["statut"] == "ERREUR" and row["rappel_demande"] is True
        llm.error = None  # l'IA revient : le conseiller relance
        assert c.post(f"/api/admin/diagnostics/{job_id}/relancer", headers=ha).status_code == 200
        assert attendre(c, h, job_id)["statut"] == "ENVOYE"
        assert c.post(f"/api/admin/diagnostics/{job_id}/relancer", headers=ha).status_code == 409


def test_unreadable_data_is_an_error_not_a_report(settings, caplog):
    llm = FakeLLM(payload={"meta": {"confidence_global": 0.1}, "compte_resultat": {}, "bilan": {}})
    with TestClient(create_app(settings, llm=llm)) as c:
        h = login(c, caplog)
        v = attendre(c, h, depot(c, h).json()["job_id"])
        assert v["statut"] == "ERREUR" and "lus de façon fiable" in v["message"]


def test_interrupted_jobs_resume_on_restart(settings, caplog):
    app = create_app(settings, llm=FakeLLM(), autostart=False)
    app.state.runner.submit = lambda _id: None  # simule un arrêt du serveur avant tout traitement
    with TestClient(app) as c:
        h = login(c, caplog)
        job_id = depot(c, h).json()["job_id"]
    with TestClient(create_app(settings, llm=FakeLLM())) as c2:  # redémarrage
        assert attendre(c2, h, job_id)["statut"] == "ENVOYE"


def test_purge_removes_old_raw_files(settings, caplog):
    app = create_app(settings, llm=FakeLLM(error=LLMError("x")), autostart=False)
    app.state.runner.submit = lambda _id: None
    with TestClient(app) as c:
        job_id = depot(c, login(c, caplog)).json()["job_id"]
        assert (settings.uploads_dir / job_id).exists()
        settings.retention_raw_hours = -1  # tout est « ancien »
        purge(app.state.db, settings)
        assert not (settings.uploads_dir / job_id).exists()


# ------------------------------------------------------------------ entretiens, invitation, effacement
def test_interview_booking(client, h, caplog):
    job_id = depot(client, h).json()["job_id"]
    assert client.post(f"/api/diagnostics/{job_id}/entretien", json={"jour": "2026-12-16", "heure": "10:30"}, headers=h).status_code == 409  # pas fini
    attendre(client, h, job_id)
    book = lambda jid, hh, jour="2026-12-16", hr="10:30": client.post(f"/api/diagnostics/{jid}/entretien", json={"jour": jour, "heure": hr}, headers=hh)
    assert book(job_id, h).status_code == 200
    assert client.get("/api/entretiens/creneaux?jour=2026-12-16", headers=h).json() == {"pris": []}  # son propre créneau n'est pas « pris »
    assert [d["entretien"] for d in client.get("/api/me/diagnostics", headers=h).json()] == ["10:30"]

    h2 = login(client, caplog, "autre@exemple.ci")
    job2 = depot(client, h2).json()["job_id"]
    attendre(client, h2, job2)
    assert client.get("/api/entretiens/creneaux?jour=2026-12-16", headers=h2).json() == {"pris": ["10:30"]}
    assert book(job2, h2).status_code == 409  # créneau complet
    assert book(job2, h2, hr="11:00").status_code == 200
    assert book(job_id, h, hr="14:00").status_code == 200  # déplacement : 10:30 est libéré
    assert client.get("/api/entretiens/creneaux?jour=2026-12-16", headers=h2).json() == {"pris": ["14:00"]}
    for bad in ({"jour": "2026-12-16", "heure": "03:00"}, {"jour": "2026-12-17", "heure": "09:00"}, {"jour": "2026-12-25", "heure": "10:30"}, {"jour": "x", "heure": "y"}):
        assert client.post(f"/api/diagnostics/{job_id}/entretien", json=bad, headers=h).status_code == 422


def test_invitation_ics_and_mail(client, h):
    job_id = depot(client, h).json()["job_id"]
    attendre(client, h, job_id)
    client.post(f"/api/diagnostics/{job_id}/entretien", json={"jour": "2026-12-16", "heure": "10:30"}, headers=h)
    r = client.get("/api/me/invitation.ics", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    ics = r.text
    assert "BEGIN:VCALENDAR" in ics and "DTSTART:20261216T083000Z" in ics and "DTSTART:20261216T103000Z" in ics and "\r\n" in ics
    assert client.post("/api/me/invitation/envoi", headers=h).status_code == 503  # pas de SMTP dans ce test


def test_delete_my_data(client, h, caplog):
    job_id = depot(client, h).json()["job_id"]
    attendre(client, h, job_id)
    assert (client.settings.reports_dir / job_id).exists()
    assert client.delete("/api/me", headers=h).status_code == 200
    assert client.get("/api/me/diagnostics", headers=h).json() == []
    assert not (client.settings.reports_dir / job_id).exists()
    assert client.get(f"/api/admin/diagnostics/{job_id}", headers=admin_headers(client)).status_code == 404


# ------------------------------------------------------------------ espace conseiller
def test_admin_flow(client, h):
    job_id = depot(client, h).json()["job_id"]
    attendre(client, h, job_id)
    assert client.get("/api/admin/diagnostics").status_code == 401
    assert client.get("/api/admin/diagnostics", headers=h).status_code == 401  # un jeton visiteur n'ouvre pas l'espace conseiller
    assert client.post("/api/admin/login", json={**ADMIN, "password": "faux"}).status_code == 401
    assert client.post("/api/admin/login", json={"email": "inconnu@x.ci", "password": "x"}).status_code == 401
    ha = admin_headers(client)

    rows = client.get("/api/admin/diagnostics?jour=2026-12-16", headers=ha).json()
    row = rows[0]
    assert row["nom_reel"] == "Société Kouassi & Fils SARL" and row["type_client"] == "PROSPECT" and row["effectif"] == 15
    assert row["statut"] == "ENVOYE" and row["score_performance"] == 50 and row["score_conformite"] == 75 and row["couverture_pct"] == 95
    assert row["alertes"][0]["niveau"] == "critique" and "FNE" in row["alertes"][0]["texte"] and {a["niveau"] for a in row["alertes"]} <= {"critique", "elevee", "moyenne", "info"}
    assert row["plan_action"][0]["priorite"] == "P1" and "module" in row["plan_action"][0]
    assert client.get("/api/admin/diagnostics?jour=2026-12-17", headers=ha).json() == []

    detail = client.get(f"/api/admin/diagnostics/{job_id}", headers=ha).json()
    assert detail["journal"][-1]["etape"] == "ENVOYE" and {a["action"] for a in detail["audit"]} >= {"depot", "consultation"}
    assert client.get(f"/api/admin/diagnostics/{job_id}/md", headers=ha).status_code == 200
    url = client.get(f"/api/admin/diagnostics/{job_id}/pdf", headers=ha).json()["url"]
    assert client.get(url).status_code == 200

    assert client.post(f"/api/admin/diagnostics/{job_id}/entretien-realise", headers=ha).status_code == 200
    assert client.post(f"/api/admin/diagnostics/{job_id}/rappel-effectue", headers=ha).status_code == 200
    row = client.get("/api/admin/diagnostics", headers=ha).json()[0]
    assert row["entretien_realise"] is True and row["rappel_effectue"] is True
    assert client.post(f"/api/admin/diagnostics/{job_id}/renvoyer", headers=ha).status_code == 409  # SMTP absent

    csv_ = client.get("/api/admin/diagnostics.csv?jour=2026-12-16", headers=ha)
    assert csv_.status_code == 200 and "JPO-2026-0001" in csv_.text and csv_.text.startswith("﻿reference;")
    assert client.get("/api/admin/diagnostics.csv", headers=h).status_code == 401


def test_csv_neutralises_formulas(client, h):
    job_id = depot(client, h, raison_sociale="=HYPERLINK(1)").json()["job_id"]
    attendre(client, h, job_id)
    assert "'=HYPERLINK(1)" in client.get("/api/admin/diagnostics.csv", headers=admin_headers(client)).text


def test_admin_disabled_without_account(settings, monkeypatch):
    settings.admin_accounts = {}
    with TestClient(create_app(settings, llm=FakeLLM())) as c:
        assert c.post("/api/admin/login", json=ADMIN).status_code == 503


# ------------------------------------------------------------------ front et méta
def test_front_is_served_and_internal_pages_hidden(settings):
    assert settings.front_dir is not None, "le front doit être trouvé automatiquement"
    with TestClient(create_app(settings, llm=FakeLLM())) as c:
        assert "Check-up 360" in c.get("/").text
        assert c.get("/conseiller.html").status_code == 200
        assert "mock: false" in c.get("/config.js").text
        assert c.get("/references/emails.html").status_code == 404
        assert c.get("/api/health").json() == {"ok": True}  # les routes /api passent avant le front


def test_meta_endpoint(client):
    m = client.get("/api/meta").json()
    assert m["diagnostic"]["modeles"][0] == "nvidia/nemotron-3.5-lightning:free"
    assert "Banque" in [s["label"] for s in m["secteurs"] if s["fne_exempt"]]


def test_pdf_and_four_pages_with_libreoffice(settings, caplog):
    if not settings.real_soffice:
        pytest.skip("LibreOffice absent")
    import tempfile
    from pathlib import Path
    settings.soffice_path = settings.real_soffice
    settings.workers = 2
    settings.lo_profile_dir = Path(tempfile.gettempdir()) / "jpo_lo_test_profiles"  # stable : évite la 1re init (~45 s)
    with TestClient(create_app(settings, llm=FakeLLM())) as c:
        h = login(c, caplog)
        job_id = depot(c, h).json()["job_id"]
        v = attendre(c, h, job_id, timeout=300)
        assert v["statut"] == "ENVOYE"
        pdf = c.get(c.get(f"/api/diagnostics/{job_id}/pdf", headers=h).json()["url"])
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
        detail = c.get(f"/api/admin/diagnostics/{job_id}", headers=admin_headers(c)).json()
        assert detail["pages"] == 4 and "pages" not in (detail["erreur"] or "")  # seule note attendue : SMTP absent

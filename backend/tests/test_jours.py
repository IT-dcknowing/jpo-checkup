"""Plusieurs jours de visite, créneaux par jour (programme du 26/11/2026) et rappels de la veille."""
from __future__ import annotations

from datetime import UTC, datetime

from app.main import envoyer_rappels
from test_api import auth, client, depot, tok  # noqa: F401 — client et tok sont des fixtures

TOUS = ["2026-12-16", "2026-12-17", "2026-12-18"]


def reserver(client, jeton, job_id, jour, heure):
    return client.post(f"/api/diagnostics/{job_id}/entretien", json={"jour": jour, "heure": heure}, headers=auth(jeton))


def resume(client, jeton, job_id):
    return client.get(f"/api/diagnostics/{job_id}", headers=auth(jeton)).json()


def test_depot_sur_plusieurs_jours_avec_creneau(client, tok):
    r = depot(client, tok, jours_visite=["2026-12-18", "2026-12-16"], creneau_entretien={"jour": "2026-12-16", "heure": "09:45"})
    assert r.status_code == 202 and r.json()["entretien_refuse"] is False
    v = resume(client, tok, r.json()["job_id"])
    assert v["jours_visite"] == ["2026-12-16", "2026-12-18"] and (v["entretien_jour"], v["entretien"]) == ("2026-12-16", "09:45")


def test_ancien_formulaire_a_un_seul_jour(client, tok):
    job_id = depot(client, tok).json()["job_id"]
    assert resume(client, tok, job_id)["jours_visite"] == ["2026-12-16"]


def test_jours_et_creneaux_invalides_refuses(client, tok):
    assert depot(client, tok, jours_visite=["2026-12-19"]).status_code == 422
    assert depot(client, tok, jours_visite=["2026-12-16"], creneau_entretien={"jour": "2026-12-17", "heure": "14:15"}).status_code == 422
    assert depot(client, tok, jours_visite=["2026-12-17"], creneau_entretien={"jour": "2026-12-17", "heure": "09:00"}).status_code == 422


def test_creneau_complet_ne_bloque_pas_le_depot(client, tok):
    client.settings.slot_capacity = 1
    creneau = {"jour": "2026-12-17", "heure": "14:15"}
    depot(client, tok, jours_visite=["2026-12-17"], creneau_entretien=creneau)
    autre = client.sb.add_user("autre@exemple.ci")
    r = depot(client, autre, jours_visite=["2026-12-17"], creneau_entretien=creneau)
    assert r.status_code == 202 and r.json()["entretien_refuse"] is True


def test_un_seul_entretien_par_compte_et_jour_ajoute(client, tok):
    premier, second = depot(client, tok).json()["job_id"], depot(client, tok).json()["job_id"]
    for dossier in client.sb.tables["rapports"]:
        dossier["etape"] = "ENVOYE"  # diagnostics terminés (le rendu Word n'est pas le sujet ici)
    assert reserver(client, tok, premier, "2026-12-16", "09:00").status_code == 200
    assert reserver(client, tok, second, "2026-12-18", "14:00").status_code == 200
    assert len(client.sb.tables["entretiens"]) == 1
    assert resume(client, tok, second)["jours_visite"] == ["2026-12-16", "2026-12-18"]
    assert reserver(client, tok, second, "2026-12-18", "15:30").status_code == 422


def test_invitation_liste_les_jours_choisis(client, tok):
    depot(client, tok, jours_visite=["2026-12-16", "2026-12-17"])
    ics = client.get("/api/me/invitation.ics", headers=auth(tok)).content.decode()
    assert ics.count("BEGIN:VEVENT") == 2 and "20261216T083000Z" in ics and "20261217T103000Z" in ics
    assert "Suite Flow" not in ics
    assert client.post("/api/me/invitation/envoi", headers=auth(tok)).json() == {"ok": True}
    corps = client.sb.invocations[-1][1]
    assert corps["jour_long"] == "Mercredi 16 et jeudi 17 décembre 2026"


def test_rappel_la_veille_une_seule_fois(client, tok):
    depot(client, tok, jours_visite=TOUS, creneau_entretien={"jour": "2026-12-17", "heure": "15:00"})
    db, veille = client.app.state.db, datetime(2026, 12, 16, 17, tzinfo=UTC)
    assert envoyer_rappels(client.settings, db, client.sb, veille)["envoyes"] == 1
    corps = client.sb.invocations[-1][1]
    assert corps["type"] == "rappel" and corps["entretien"] == "15h00" and "trois experts" in corps["accroche"]
    assert envoyer_rappels(client.settings, db, client.sb, veille)["envoyes"] == 0
    assert envoyer_rappels(client.settings, db, client.sb, datetime(2026, 11, 26, 17, tzinfo=UTC))["envoyes"] == 0


def test_tache_de_rappel_protegee(client):
    assert client.get("/api/cron/rappels").status_code == 401


def test_conseillers_filtrent_sur_tous_les_jours(client, tok):
    depot(client, tok, jours_visite=["2026-12-16", "2026-12-18"])
    from test_api import admin_headers
    h = admin_headers(client)
    assert len(client.get("/api/admin/diagnostics?jour=2026-12-18", headers=h).json()) == 1
    assert client.get("/api/admin/diagnostics?jour=2026-12-17", headers=h).json() == []


def test_programme_des_creneaux(client):
    assert client.settings.slot_capacity == 3 and client.settings.slots["2026-12-17"] == ["14:15", "15:00", "15:45", "16:30"]
    assert client.settings.slots["2026-12-18"] == ["14:00", "14:45"]

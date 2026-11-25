"""API FastAPI Check-up 360 (déployée sur Vercel) : dépôt, traitement, suivi, Mes diagnostics, entretiens, espace conseiller.

Comptes visiteurs : Supabase Auth (code à 6 chiffres reçu par e-mail). Le front envoie le jeton de session Supabase
en « Authorization: Bearer » ; le serveur le vérifie auprès de Supabase puis agit avec la clé service_role.
Les fichiers sont déposés par le navigateur directement dans Supabase Storage (bucket privé « documents »).
"""
from __future__ import annotations

import base64
import csv
import hmac
import io
import logging
import re
import secrets
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import ics
from .config import Settings, get_settings
from .db import TERMINAUX, Database
from .llm import LLMClient, OpenRouterClient
from .pipeline import FRONT_ETAPE, TYPES_FICHIERS, Pipeline
from .readers import ALLOWED_EXT
from .schemas import SECTEURS, DepotForm
from .security import RateLimiter, sign_token, verify_token
from .supa import Supabase, SupabaseError

log = logging.getLogger("jpo.api")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
JOURS_LONGS = {"2026-12-16": "Mercredi 16 décembre 2026", "2026-12-17": "Jeudi 17 décembre 2026", "2026-12-18": "Vendredi 18 décembre 2026"}


def purge(db: Database, sb: Supabase, s: Settings) -> dict:
    """Rétention (spec §13) : fichiers bruts après 24 h, rapports après 30 jours ; traitements interrompus passés en erreur."""
    docs = db.documents_older_than(timedelta(hours=s.retention_raw_hours))
    sb.remove("documents", [d["chemin"] for d in docs])
    n_rapports = 0
    for job in db.jobs_older_than(timedelta(days=s.retention_report_days)):
        if job["docx_path"] or job["md_path"]:
            sb.remove("rapports", [p for p in (job["docx_path"], job["md_path"]) if p])
            db.update_job(job["id"], docx_path=None, md_path=None)
            n_rapports += 1
    bloques = db.stale_jobs(minutes=15)
    for job in bloques:
        db.update_job(job["id"], erreur="traitement interrompu (délai de la fonction dépassé)",
                      erreur_publique="Votre diagnostic n'a pas pu être produit automatiquement. Un conseiller DC-KNOWING vous rappelle très vite.")
        db.log_event(job["id"], "ERREUR", job["progression"], {"etape": "ERREUR", "progression": job["progression"],
                     "message": "Traitement interrompu · un conseiller vous rappelle", "niveau": "erreur"})
    return {"fichiers_bruts": len(docs), "rapports": n_rapports, "traitements_interrompus": len(bloques)}


def create_app(settings: Settings | None = None, llm: LLMClient | None = None, sb: Supabase | None = None) -> FastAPI:
    s = settings or get_settings()
    s.ensure_dirs()
    if sb is None:
        if not (s.supabase_url and s.supabase_service_key):
            log.error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY manquantes : l'API ne peut pas fonctionner")
        sb = Supabase(s.supabase_url or "http://supabase.invalid", s.supabase_service_key, s.supabase_anon_key)
    db = Database(sb)
    pipeline = Pipeline(s, db, sb, llm or OpenRouterClient(s))
    login_limiter = RateLimiter(10, 900)

    app = FastAPI(title="DC-KNOWING — Check-up 360 (JPO)", version="3.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])
    app.state.db, app.state.pipeline, app.state.settings, app.state.sb = db, pipeline, s, sb

    @app.exception_handler(SupabaseError)
    async def _supabase_error(_: Request, exc: SupabaseError):
        log.error("Supabase : %s", exc)
        return JSONResponse(status_code=502, content={"detail": "Service de données momentanément indisponible. Réessayez dans un instant."})

    # ------------------------------------------------------------------ utilitaires
    def client_ip(request: Request) -> str:
        if s.trust_proxy and (fwd := request.headers.get("x-forwarded-for")):
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "inconnu"

    def bearer(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    def visitor(request: Request) -> dict:
        """Utilisateur Supabase connecté : {id, email, token}."""
        token = bearer(request)
        user = sb.get_user(token) if token else None
        if not user or not user.get("email"):
            raise HTTPException(401, "Session expirée : reconnectez-vous avec un nouveau code.")
        return {"id": user["id"], "email": user["email"].lower(), "token": token}

    def admin_email(request: Request) -> str:
        user = verify_token(s.secret_key, bearer(request), "admin")
        if not s.admin_accounts:
            raise HTTPException(503, "Espace conseiller désactivé (aucun compte configuré)")
        if not user or user not in s.admin_accounts:
            raise HTTPException(401, "Authentification requise", headers={"WWW-Authenticate": "Bearer"})
        return user

    def own_job(job_id: str, user: dict) -> dict:
        job = db.get_job(job_id) if UUID.match(job_id) else None
        if not job or not hmac.compare_digest(job["owner"], user["id"]):
            raise HTTPException(404, "Diagnostic introuvable")
        return job

    def any_job(job_id: str) -> dict:
        job = db.get_job(job_id) if UUID.match(job_id) else None
        if not job:
            raise HTTPException(404, "Diagnostic introuvable")
        return job

    def signed_report(job: dict) -> str:
        """URL signée (7 jours) du rapport Word, nommée Diagnostic_DC-KNOWING_<référence>.docx au téléchargement."""
        return sb.signed_url("rapports", job["docx_path"], s.download_ttl, f"Diagnostic_DC-KNOWING_{job['ref']}.docx")

    def summary(job: dict, booking: dict | None = None) -> dict:
        """Résumé d'un diagnostic pour le visiteur (README du front, « Résumé d'un diagnostic »)."""
        done = job["statut"] in ("ENVOYE", "TERMINE")
        resume = job["resume"] or {}
        out = {"job_id": job["id"], "reference": job["ref"], "entreprise": job["form"]["raison_sociale"], "statut": FRONT_ETAPE[job["statut"]],
               "progression": job["progression"], "created_at": job["created_at"], "entretien": booking["heure"] if booking else None,
               "email_envoye": bool(job["email_envoye_at"]), "rapport_disponible": bool(job["docx_path"])}
        if done:
            out |= {"score_performance": job["score_performance"], "grade_performance": job["grade_performance"],
                    "score_conformite": job["score_conformite"], "grade_conformite": job["grade_conformite"],
                    "couverture_pct": job["couverture_pct"], "indicatif": bool(resume.get("indicatif")), "verdict": resume.get("verdict", ""),
                    "points_forts": resume.get("points_forts", []), "points_a_traiter": resume.get("points_a_traiter", []),
                    "alerte_principale": resume.get("alerte_principale", ""), "plan_action": job["plan_action"]}
        elif job["statut"] == "ERREUR":
            out["message"] = job["erreur_publique"]
        return out

    # ------------------------------------------------------------------ public
    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/meta")
    def meta():
        return {"secteurs": SECTEURS, "jours": s.event_days, "formats": sorted(e.lstrip(".") for e in ALLOWED_EXT),
                "taille_max_mo": s.max_upload_bytes // (1024 * 1024), "fichiers_max": s.max_files,
                "diagnostic": {"modeles": s.llm_models, "cle_ia": bool(s.openrouter_api_key), "supabase": bool(s.supabase_service_key),
                               "word": bool(s.docx_render_url or s.node_path)}}

    # ------------------------------------------------------------------ dépôt et traitement
    @app.post("/api/diagnostics", status_code=202)
    def depot(payload: dict, request: Request, user: dict = Depends(visitor)):
        """Crée le diagnostic à partir des réponses du formulaire et des documents déjà déposés dans Storage."""
        if db.count_recent(user["id"]) >= s.rate_limit:
            raise HTTPException(429, "Trop de dépôts : réessayez dans une heure.")
        f = payload
        try:  # l'e-mail du dépôt est toujours celui de la session, jamais celui du formulaire
            form = DepotForm(raison_sociale=f.get("raison_sociale"), ncc=f.get("ncc") or None, secteur=f.get("secteur"),
                             effectif=f.get("effectif"), contact_nom=f.get("contact_nom"), contact_email=user["email"],
                             contact_telephone=f.get("contact_telephone"), type_client=f.get("type_client"),
                             jour_evenement=f.get("jour_evenement"), regime_fiscal=f.get("regime_fiscal"), fne=f.get("fne"),
                             dgi=f.get("statut_dgi"), cnps=f.get("statut_cnps"), its=f.get("statut_its"), cmu=f.get("statut_cmu"),
                             disa=f.get("statut_disa"), tva_exoneree=str(f.get("tva_exoneree")).lower() == "true",
                             consentement=str(f.get("consentement_artci")).lower() == "true")
        except ValidationError as e:
            return JSONResponse(status_code=422, content={"detail": [{"champ": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()]})
        if form.jour_evenement not in s.event_days:
            raise HTTPException(422, "Jour d'événement inconnu")
        demandes = payload.get("documents") or []
        ids = [str(d.get("id", "")) for d in demandes if isinstance(d, dict)]
        if not 1 <= len(ids) <= s.max_files or not all(UUID.match(i) for i in ids) or len(set(ids)) != len(ids):
            raise HTTPException(422, f"Déposez entre 1 et {s.max_files} fichiers.")
        docs = db.documents_for(user["id"], ids)
        if len(docs) != len(ids) or any(d["rapport_id"] for d in docs):
            raise HTTPException(422, "Documents introuvables ou déjà utilisés : redéposez vos fichiers.")
        for d in docs:
            ext = "." + d["nom_fichier"].rsplit(".", 1)[-1].lower() if "." in d["nom_fichier"] else ""
            if ext not in ALLOWED_EXT:
                raise HTTPException(422, f"Format non accepté pour « {d['nom_fichier']} » (CSV, XLSX ou PDF).")
        job = db.create_job(user["id"], user["email"], form.model_dump(mode="json"), form.jour_evenement, form.secteur, form.raison_sociale)
        db.attach_documents(job["id"], ids)
        categories = {str(d.get("id")): d.get("type") for d in demandes if isinstance(d, dict)}
        for d in docs:  # catégorie choisie par le visiteur (balance, compte de résultat, bilan)
            cat = categories.get(d["id"])
            if cat in TYPES_FICHIERS and cat != d["categorie"]:
                sb.update("documents", {"id": f"eq.{d['id']}"}, {"categorie": cat})
        db.update_profile(user["id"], nom=form.contact_nom, entreprise=form.raison_sociale, telephone=form.contact_telephone)
        db.audit(job["id"], "visiteur", "depot", client_ip(request))
        return {"job_id": job["id"], "reference": job["ref"]}

    @app.post("/api/diagnostics/{job_id}/traiter")
    def traiter(job_id: str, user: dict = Depends(visitor)):
        """Exécute le traitement (jusqu'à ~5 min). Le front suit l'avancement par /journal pendant ce temps.
        Verrou en base : un deuxième appel pendant le traitement répond tout de suite « en_cours »."""
        job = own_job(job_id, user)
        if job["statut"] in TERMINAUX:
            return {"etat": "termine"}
        if not db.claim(job_id):
            return JSONResponse(status_code=202, content={"etat": "en_cours"})
        pipeline.process(job_id, user["token"])
        return {"etat": "termine"}

    @app.get("/api/diagnostics/{job_id}/journal")
    def journal(job_id: str, depuis: int = 0, user: dict = Depends(visitor)):
        job = own_job(job_id, user)
        events = job["journal"]
        return {"evenements": events[max(depuis, 0):], "suivant": len(events), "termine": job["statut"] in TERMINAUX,
                "statut": FRONT_ETAPE[job["statut"]]}

    @app.get("/api/diagnostics/{job_id}")
    def diagnostic(job_id: str, user: dict = Depends(visitor)):
        job = own_job(job_id, user)
        return summary(job, db.get_booking(job_id))

    @app.get("/api/diagnostics/{job_id}/pdf")
    def lien_rapport(job_id: str, user: dict = Depends(visitor)):
        job = own_job(job_id, user)
        if not job["docx_path"]:
            raise HTTPException(404, "Rapport indisponible")
        db.audit(job_id, "visiteur", "lien_docx")
        return {"url": signed_report(job), "format": "docx"}

    @app.post("/api/diagnostics/{job_id}/rappel")
    def rappel(job_id: str, user: dict = Depends(visitor)):
        own_job(job_id, user)
        db.update_job(job_id, rappel_demande=True)
        db.audit(job_id, "visiteur", "demande_rappel")
        return {"ok": True}

    # ------------------------------------------------------------------ Mes diagnostics, entretiens, invitation
    @app.get("/api/me/diagnostics")
    def mes_diagnostics(user: dict = Depends(visitor)):
        jobs = db.list_jobs(owner=user["id"])
        bookings = db.bookings_for([j["id"] for j in jobs])
        return [summary(j, bookings.get(j["id"])) for j in jobs]

    @app.get("/api/entretiens/creneaux")
    def creneaux(jour: str, user: dict = Depends(visitor)):
        if jour not in s.event_days:
            raise HTTPException(422, "Jour inconnu")
        return {"pris": db.taken_slots(jour, s.slot_capacity, exclude_owner=user["id"])}

    @app.post("/api/diagnostics/{job_id}/entretien")
    def reserver(job_id: str, payload: dict, user: dict = Depends(visitor)):
        job = own_job(job_id, user)
        jour, heure = str(payload.get("jour", "")), str(payload.get("heure", ""))
        if jour not in s.event_days or not HEURE.match(heure) or heure not in s.slots or heure < s.slot_min_by_day.get(jour, "00:00"):
            raise HTTPException(422, "Créneau invalide pour ce jour.")
        if job["statut"] not in ("ENVOYE", "TERMINE"):
            raise HTTPException(409, "L'entretien se réserve une fois le diagnostic terminé.")
        if not db.book_slot(job_id, user["id"], jour, heure, s.slot_capacity):
            raise HTTPException(409, "Ce créneau vient d'être réservé : choisissez-en un autre.")
        db.audit(job_id, "visiteur", f"entretien_{jour}_{heure}")
        return {"ok": True}

    def invitation_data(user: dict) -> tuple[bytes, str, str, str | None, str]:
        jobs = db.list_jobs(owner=user["id"], limit=1)
        booking = db.latest_booking(user["id"])
        jour = (booking or {}).get("jour") or (jobs[0]["jour_evenement"] if jobs else s.event_days[0])
        debut, fin = s.event_hours.get(jour, ("08:30", "17:30"))
        entretien = booking["heure"] if booking and booking["jour"] == jour else None
        data = ics.build_ics(user["id"].replace("-", "")[:12], jour, debut, fin, s.event_location, entretien, s.slot_minutes)
        nom = jobs[0]["form"]["contact_nom"] if jobs else ""
        return data, JOURS_LONGS.get(jour, jour), f"à partir de {debut.replace(':', 'h')}", entretien, nom

    @app.get("/api/me/invitation.ics")
    def invitation_ics(user: dict = Depends(visitor)):
        data, *_ = invitation_data(user)
        return Response(data, media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="JPO-DC-KNOWING.ics"'})

    @app.post("/api/me/invitation/envoi")
    def invitation_envoi(user: dict = Depends(visitor)):
        data, jour_long, horaire, entretien, nom = invitation_data(user)
        try:  # envoyée par l'Edge Function, toujours à l'adresse de la session
            sb.invoke("envoyer-rapport", {"type": "invitation", "nom": nom, "jour_long": jour_long, "horaire": horaire,
                                          "entretien": entretien, "ics_base64": base64.b64encode(data).decode()}, user["token"])
        except Exception:  # noqa: BLE001
            log.exception("envoi de l'invitation en échec")
            raise HTTPException(502, "Impossible d'envoyer l'e-mail pour le moment.")
        return {"ok": True}

    @app.delete("/api/me")
    def supprimer_mes_donnees(request: Request, user: dict = Depends(visitor)):
        """Droit à l'effacement (loi n°2013-450) : fichiers, rapports, entretiens, profil et compte."""
        sb.remove("documents", [d["chemin"] for d in db.owner_documents(user["id"])])
        for job in db.delete_owner(user["id"]):
            sb.remove("rapports", [p for p in (job["docx_path"], job["md_path"]) if p])
        db.audit(None, user["id"][:8], "effacement_donnees", client_ip(request))
        sb.delete_user(user["id"])  # supprime aussi le profil et les documents restants (cascade)
        return {"ok": True}

    # ------------------------------------------------------------------ tâche planifiée (Vercel Cron)
    @app.get("/api/cron/purge")
    def cron_purge(request: Request):
        if not s.cron_secret or not hmac.compare_digest(bearer(request), s.cron_secret):
            raise HTTPException(401, "Non autorisé")
        return purge(db, sb, s)

    # ------------------------------------------------------------------ conseillers
    @app.post("/api/admin/login")
    def login(request: Request, payload: dict):
        ip = client_ip(request)
        if not login_limiter.allow(ip):
            raise HTTPException(429, "Trop de tentatives")
        if not s.admin_accounts:
            raise HTTPException(503, "Espace conseiller désactivé (aucun compte configuré)")
        user = str(payload.get("email") or payload.get("username") or "").strip().lower()
        expected = s.admin_accounts.get(user, "")
        ok = hmac.compare_digest(str(payload.get("password", "")).encode(), (expected or secrets.token_hex(16)).encode()) and bool(expected)
        if not ok:
            db.audit(None, user[:60] or "?", "login_echec", ip)
            raise HTTPException(401, "Identifiants invalides")
        db.audit(None, user, "login", ip)
        return {"token": sign_token(s.secret_key, "admin", user, s.admin_token_ttl), "expire_dans_s": s.admin_token_ttl}

    def admin_view(job: dict, booking: dict | None, detail: bool = False) -> dict:
        form = job["form"]
        out = {"id": job["id"], "job_id": job["id"], "reference": job["ref"], "nom_reel": form["raison_sociale"], "type_client": form["type_client"],
               "secteur": job["secteur"], "effectif": form["effectif"], "created_at": job["created_at"], "completed_at": job["completed_at"],
               "statut": FRONT_ETAPE[job["statut"]], "statut_envoi": job["statut_global"], "score_performance": job["score_performance"],
               "score_conformite": job["score_conformite"], "grade_performance": job["grade_performance"], "grade_conformite": job["grade_conformite"],
               "couverture_pct": job["couverture_pct"], "indicatif": bool((job["resume"] or {}).get("indicatif")),
               "entretien": booking["heure"] if booking else None, "entretien_realise": bool(job["entretien_realise"]),
               "rappel_demande": bool(job["rappel_demande"]), "rappel_effectue": bool(job["rappel_effectue"]), "alertes": job["alertes"],
               "plan_action": job["plan_action"], "erreur": job["erreur"], "pages": job["pages"],
               "lecture_partielle": bool(job["lecture_partielle"]), "modele_ia": job["modele_ia"], "email_envoye_at": job["email_envoye_at"],
               "contact": {"nom": form["contact_nom"], "email": job["email"], "telephone": form["contact_telephone"]}}
        if detail:
            out |= {"formulaire": {k: v for k, v in form.items() if k not in ("contact_nom", "contact_email", "contact_telephone")},
                    "avertissements_ia": ((job["resultat_ia"] or {}).get("ia") or {}).get("meta", {}).get("warnings", []),
                    "journal": job["journal"], "audit": db.audit_for(job["id"])}
        return out

    @app.get("/api/admin/diagnostics")
    def admin_liste(request: Request, jour: str | None = None, statut: str | None = None, user: str = Depends(admin_email)):
        db.audit(None, user, "liste", client_ip(request))
        jobs = db.list_jobs(jour, statut)
        bookings = db.bookings_for([j["id"] for j in jobs])
        return [admin_view(j, bookings.get(j["id"])) for j in jobs]

    @app.get("/api/admin/diagnostics.csv")
    def admin_csv(request: Request, jour: str | None = None, user: str = Depends(admin_email)):
        db.audit(None, user, "export_csv", client_ip(request))
        cols = ["reference", "entreprise", "contact", "email", "telephone", "type_client", "secteur", "effectif", "statut", "performance",
                "conformite", "couverture_pct", "entretien", "entretien_realise", "rappel_demande", "rappel_effectue", "cree_le"]
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(cols)
        safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in "=+-@\t\r" else v  # anti-injection de formules Excel
        jobs = db.list_jobs(jour)
        bookings = db.bookings_for([j["id"] for j in jobs])
        for j in jobs:
            a = admin_view(j, bookings.get(j["id"]))
            w.writerow([safe(x) for x in (a["reference"], a["nom_reel"], a["contact"]["nom"], a["contact"]["email"], a["contact"]["telephone"],
                                          a["type_client"], a["secteur"], a["effectif"], a["statut"], a["score_performance"], a["score_conformite"],
                                          a["couverture_pct"], a["entretien"], int(a["entretien_realise"]), int(a["rappel_demande"]),
                                          int(a["rappel_effectue"]), a["created_at"])])
        name = f"diagnostics_{jour or 'tous'}.csv"
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/admin/diagnostics/{job_id}")
    def admin_detail(job_id: str, request: Request, user: str = Depends(admin_email)):
        db.audit(job_id, user, "consultation", client_ip(request))
        return admin_view(any_job(job_id), db.get_booking(job_id), detail=True)

    @app.get("/api/admin/diagnostics/{job_id}/pdf")
    def admin_lien(job_id: str, request: Request, user: str = Depends(admin_email)):
        job = any_job(job_id)
        if not job["docx_path"]:
            raise HTTPException(404, "Rapport indisponible")
        db.audit(job_id, user, "lien_docx", client_ip(request))
        return {"url": signed_report(job), "format": "docx"}

    @app.get("/api/admin/diagnostics/{job_id}/md")
    def admin_md(job_id: str, request: Request, user: str = Depends(admin_email)):
        job = any_job(job_id)
        if not job["md_path"]:
            raise HTTPException(404, "Fichier non disponible")
        db.audit(job_id, user, "telechargement_md", client_ip(request))
        return Response(sb.download("rapports", job["md_path"]), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="Diagnostic_DC-KNOWING_{job["ref"]}.md"'})

    @app.post("/api/admin/diagnostics/{job_id}/renvoyer")
    def admin_renvoyer(job_id: str, request: Request, user: str = Depends(admin_email)):
        any_job(job_id)
        try:
            pipeline.resend(job_id)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        except Exception:  # noqa: BLE001
            log.exception("renvoi de l'e-mail en échec")
            raise HTTPException(502, "Impossible d'envoyer l'e-mail pour le moment.")
        db.audit(job_id, user, "renvoi_email", client_ip(request))
        return {"ok": True}

    @app.post("/api/admin/diagnostics/{job_id}/relancer")
    def admin_relancer(job_id: str, request: Request, user: str = Depends(admin_email)):
        """Relance un dépôt en erreur ; le traitement s'exécute dans cette requête (jusqu'à ~5 min)."""
        job = any_job(job_id)
        if job["statut"] != "ERREUR":
            raise HTTPException(409, "Seuls les dépôts en erreur peuvent être relancés")
        if not job["resultat_ia"] and not db.job_documents(job_id):
            raise HTTPException(409, "Fichiers sources supprimés : le visiteur doit refaire un dépôt")
        db.update_job(job_id, erreur=None, erreur_publique=None, completed_at=None)
        db.log_event(job_id, "EN_ATTENTE", 5, {"etape": "EN_ATTENTE", "progression": 5, "message": "Traitement relancé par un conseiller", "niveau": ""})
        db.audit(job_id, user, "relance", client_ip(request))
        if db.claim(job_id):
            pipeline.process(job_id)
        return {"ok": True}

    def _flag(job_id: str, user: str, request: Request, **fields):
        any_job(job_id)
        db.update_job(job_id, **fields)
        db.audit(job_id, user, "/".join(fields), client_ip(request))
        return {"ok": True}

    @app.post("/api/admin/diagnostics/{job_id}/entretien-realise")
    def admin_entretien_realise(job_id: str, request: Request, user: str = Depends(admin_email)):
        return _flag(job_id, user, request, entretien_realise=True)

    @app.post("/api/admin/diagnostics/{job_id}/rappel-effectue")
    def admin_rappel_effectue(job_id: str, request: Request, user: str = Depends(admin_email)):
        return _flag(job_id, user, request, rappel_effectue=True)

    # ------------------------------------------------------------------ front statique en local (sur Vercel : servi par le CDN)
    if s.front_dir and not s.on_vercel:
        app.mount("/", StaticFiles(directory=s.front_dir, html=True), name="front")
    return app

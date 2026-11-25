"""API FastAPI Check-up 360 : connexion par code, dépôt, suivi en direct (SSE), Mes diagnostics, entretiens, espace conseiller.
Le contrat suit checkup360-front/README.md ; le front statique est servi par la même application (même origine, pas de CORS)."""
from __future__ import annotations

import asyncio
import csv
import hmac
import io
import json
import logging
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import ics, mailer, report
from .config import Settings, get_settings
from .db import TERMINAUX, Database, loads
from .llm import LLMClient, OpenRouterClient
from .pipeline import FRONT_ETAPE, TYPES_FICHIERS, JobRunner, Pipeline
from .readers import ALLOWED_EXT, ReaderError, sniff_kind
from .schemas import EMAIL_RE, SECTEURS, DepotForm
from .security import Cipher, RateLimiter, code_hash, owner_hash, sign_token, verify_token

log = logging.getLogger("jpo.api")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
HEURE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JOURS_LONGS = {"2026-12-16": "Mercredi 16 décembre 2026", "2026-12-17": "Jeudi 17 décembre 2026", "2026-12-18": "Vendredi 18 décembre 2026"}


def purge(db: Database, s: Settings) -> None:
    """Rétention : fichiers bruts après 24 h, rapports après 30 jours (spec §13)."""
    for job in db.jobs_older_than(timedelta(hours=s.retention_raw_hours)):
        shutil.rmtree(s.uploads_dir / job["id"], ignore_errors=True)
    for job in db.jobs_older_than(timedelta(days=s.retention_report_days)):
        if job["docx_path"] or job["pdf_path"]:
            shutil.rmtree(s.reports_dir / job["id"], ignore_errors=True)
            db.update_job(job["id"], docx_path=None, pdf_path=None, md_path=None)


class FrontFiles(StaticFiles):
    """Fichiers du front, sans les maquettes internes (references/)."""

    async def get_response(self, path: str, scope):
        if path.replace("\\", "/").startswith("references"):
            return Response("Not found", status_code=404)
        return await super().get_response(path, scope)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None, autostart: bool = True) -> FastAPI:
    s = settings or get_settings()
    s.ensure_dirs()
    db = Database(s.db_path)
    db.init()
    cipher = Cipher(s.data_key)
    pipeline = Pipeline(s, db, cipher, llm or OpenRouterClient(s))
    runner = JobRunner(pipeline, s.workers)
    depot_ip, depot_owner = RateLimiter(s.rate_limit, 3600), RateLimiter(s.rate_limit, 3600)
    code_ip, code_owner, login_limiter = RateLimiter(10, 3600), RateLimiter(5, 3600), RateLimiter(10, 900)

    async def purge_loop():
        while True:
            await asyncio.to_thread(purge, db, s)
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = None
        if autostart:
            for jid in db.unfinished_jobs():  # reprise des dépôts interrompus par un arrêt du serveur
                runner.submit(jid)
            task = asyncio.create_task(purge_loop())
            if s.lo_warmup and s.soffice_path:
                threading.Thread(target=report.warm_up, args=(s,), daemon=True).start()
        if not s.openrouter_api_key:
            log.warning("OPENROUTER_API_KEY absente : les dépôts échoueront à l'étape d'analyse")
        if not s.admin_accounts:
            log.warning("Aucun compte conseiller (ADMIN_PASSWORD) : l'espace conseiller est désactivé")
        if not mailer.configured(s):
            log.warning("SMTP non configuré : %s", "les codes de connexion s'affichent ici (APP_ENV=dev)" if s.app_env == "dev"
                        else "aucun code de connexion ne peut être envoyé")
        yield
        if task:
            task.cancel()
        runner.shutdown()

    app = FastAPI(title="DC-KNOWING — Check-up 360 (JPO)", version="2.1", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])
    app.state.db, app.state.runner, app.state.pipeline, app.state.settings = db, runner, pipeline, s

    # ------------------------------------------------------------------ utilitaires
    def client_ip(request: Request) -> str:
        if s.trust_proxy and (fwd := request.headers.get("x-forwarded-for")):
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "inconnu"

    def bearer(request: Request, allow_query: bool = False) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.query_params.get("token", "") if allow_query else ""

    def visitor_email(request: Request, allow_query: bool = False) -> str:
        email = verify_token(s.secret_key, bearer(request, allow_query), "visitor")
        if not email:
            raise HTTPException(401, "Session expirée : reconnectez-vous avec un nouveau code.")
        return email

    def visitor_sse(request: Request) -> str:  # EventSource n'envoie pas d'en-tête : jeton en paramètre
        return visitor_email(request, allow_query=True)

    def admin_email(request: Request) -> str:
        user = verify_token(s.secret_key, bearer(request), "admin")
        if not s.admin_accounts:
            raise HTTPException(503, "Espace conseiller désactivé (aucun compte configuré)")
        if not user or user not in s.admin_accounts:
            raise HTTPException(401, "Authentification requise", headers={"WWW-Authenticate": "Bearer"})
        return user

    def own_job(job_id: str, email: str) -> dict:
        job = db.get_job(job_id) if JOB_ID.match(job_id) else None
        if not job or not hmac.compare_digest(job["owner"] or "", owner_hash(s.secret_key, email)):
            raise HTTPException(404, "Diagnostic introuvable")
        return job

    def any_job(job_id: str) -> dict:
        job = db.get_job(job_id) if JOB_ID.match(job_id) else None
        if not job:
            raise HTTPException(404, "Diagnostic introuvable")
        return job

    def form_of(job: dict) -> dict:
        return json.loads(cipher.dec(job["form_enc"]))

    def signed_url(job: dict, kind: str) -> str:
        return f"/api/fichiers/{job['id']}/{kind}?token={pipeline.download_token(job['id'])}"

    def best_kind(job: dict) -> str | None:
        for kind in ("pdf", "docx"):
            p = job.get(f"{kind}_path")
            if p and Path(p).exists():
                return kind
        return None

    def front_statut(job: dict) -> str:
        return FRONT_ETAPE[job["statut"]]

    def summary(job: dict) -> dict:
        """Résumé d'un diagnostic pour le visiteur (README du front, « Résumé d'un diagnostic »)."""
        done = job["statut"] in ("ENVOYE", "TERMINE")
        resume = loads(job["resume_json"], {}) or {}
        booking = db.get_booking(job["id"])
        out = {"job_id": job["id"], "reference": job["ref"], "entreprise": form_of(job)["raison_sociale"], "statut": front_statut(job),
               "progression": job["progression"], "created_at": job["created_at"], "entretien": booking["heure"] if booking else None,
               "email_envoye": bool(job["email_envoye_at"])}
        if done:
            out |= {"score_performance": job["score_performance"], "grade_performance": job["grade_performance"],
                    "score_conformite": job["score_conformite"], "grade_conformite": job["grade_conformite"],
                    "couverture_pct": job["couverture_pct"], "indicatif": bool(resume.get("indicatif")), "verdict": resume.get("verdict", ""),
                    "points_forts": resume.get("points_forts", []), "points_a_traiter": resume.get("points_a_traiter", []),
                    "alerte_principale": resume.get("alerte_principale", ""), "plan_action": loads(job["plan_action_json"], [])}
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
                "diagnostic": {"modeles": s.llm_models, "cle_ia": bool(s.openrouter_api_key), "email": bool(s.smtp_host),
                               "word": bool(s.node_path), "pdf": bool(s.soffice_path)}}

    # ------------------------------------------------------------------ connexion visiteur par code e-mail
    @app.post("/api/auth/code")
    async def auth_code(request: Request, payload: dict):
        email = str(payload.get("email", "")).strip().lower()
        if not EMAIL_RE.match(email) or len(email) > 200:
            raise HTTPException(422, "Adresse e-mail invalide")
        owner = owner_hash(s.secret_key, email)
        if not (code_ip.allow(client_ip(request)) and code_owner.allow(owner)):
            raise HTTPException(429, "Trop de demandes. Patientez avant de redemander un code.")
        previous = db.get_code(owner)
        if previous and previous["sent_at"] + s.code_resend > time.time():
            raise HTTPException(429, "Un code vient d'être envoyé. Patientez quelques secondes.")
        if not mailer.configured(s) and s.app_env != "dev":
            raise HTTPException(503, "L'envoi d'e-mails n'est pas configuré sur ce serveur.")
        code = f"{secrets.randbelow(10 ** 6):06d}"
        db.save_code(owner, code_hash(s.secret_key, owner, code), s.code_ttl)
        if mailer.configured(s):
            try:
                await asyncio.to_thread(mailer.send_code, s, email, code)
            except Exception:  # noqa: BLE001
                log.exception("envoi du code de connexion en échec")
                db.delete_code(owner)
                raise HTTPException(502, "Impossible d'envoyer l'e-mail pour le moment. Réessayez dans un instant.")
        else:
            log.warning("[DEV] code de connexion pour %s : %s", email, code)
        return {"expires_in": s.code_ttl, "resend_after": s.code_resend}

    @app.post("/api/auth/verify")
    async def auth_verify(request: Request, payload: dict):
        email, code = str(payload.get("email", "")).strip().lower(), str(payload.get("code", "")).strip()
        owner = owner_hash(s.secret_key, email)
        row = db.get_code(owner)
        if not row or row["expires_at"] < time.time():
            if row:
                db.delete_code(owner)
            return JSONResponse(status_code=410, content={"detail": "Code expiré. Demandez un nouveau code."})
        if hmac.compare_digest(row["code_hash"], code_hash(s.secret_key, owner, code)):
            db.delete_code(owner)  # usage unique
            db.audit(None, owner[:8], "connexion_visiteur", client_ip(request))
            return {"token": sign_token(s.secret_key, "visitor", email, s.visitor_token_ttl), "email": email}
        left = max(s.code_max_tries - db.bump_tries(owner), 0)
        if left == 0:
            db.delete_code(owner)
        return JSONResponse(status_code=401, content={"detail": "Code incorrect", "essais_restants": left})

    @app.post("/api/auth/logout")
    def auth_logout():
        return {}  # jeton sans état : le front l'oublie ; il expire de lui-même

    # ------------------------------------------------------------------ dépôt et suivi
    @app.post("/api/diagnostics", status_code=202)
    async def depot(
        request: Request,
        fichiers: list[UploadFile] = File(...), types_fichiers: list[str] = Form(default=[]),
        raison_sociale: str = Form(...), ncc: str | None = Form(None), secteur: str = Form(...), effectif: int = Form(...),
        contact_nom: str = Form(...), contact_telephone: str = Form(...),
        type_client: str = Form(...), jour_evenement: str = Form(...), regime_fiscal: str = Form(...), fne: str = Form(...),
        statut_dgi: str = Form(...), statut_cnps: str = Form(...), statut_its: str = Form(...), statut_cmu: str = Form(...),
        statut_disa: str = Form(...), tva_exoneree: bool = Form(False), consentement_artci: bool = Form(False),
        email: str = Depends(visitor_email),
    ):
        ip, owner = client_ip(request), owner_hash(s.secret_key, email)
        if not (depot_ip.allow(ip) and depot_owner.allow(owner)):
            raise HTTPException(429, "Trop de dépôts : réessayez dans une heure.")
        try:  # l'e-mail du dépôt est toujours celui de la session, jamais celui du formulaire
            form = DepotForm(raison_sociale=raison_sociale, ncc=ncc or None, secteur=secteur, effectif=effectif, contact_nom=contact_nom,
                             contact_email=email, contact_telephone=contact_telephone, type_client=type_client,
                             jour_evenement=jour_evenement, regime_fiscal=regime_fiscal, fne=fne, dgi=statut_dgi, cnps=statut_cnps,
                             its=statut_its, cmu=statut_cmu, disa=statut_disa, tva_exoneree=tva_exoneree, consentement=consentement_artci)
        except ValidationError as e:
            return JSONResponse(status_code=422, content={"detail": [{"champ": ".".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()]})
        if form.jour_evenement not in s.event_days:
            raise HTTPException(422, "Jour d'événement inconnu")
        if not 1 <= len(fichiers) <= s.max_files:
            raise HTTPException(422, f"Déposez entre 1 et {s.max_files} fichiers.")

        job_id = uuid.uuid4().hex
        folder = s.uploads_dir / job_id
        folder.mkdir(parents=True)
        stored = []
        try:
            for i, f in enumerate(fichiers):
                nom = Path(f.filename or "").name
                ext = Path(nom).suffix.lower()
                if ext not in ALLOWED_EXT:
                    raise HTTPException(422, f"Format non accepté pour « {nom} » (CSV, XLSX ou PDF).")
                dest, size = folder / f"{i}{ext}", 0
                with dest.open("wb") as out:
                    while chunk := await f.read(1024 * 1024):
                        size += len(chunk)
                        if size > s.max_upload_bytes:
                            raise HTTPException(413, f"« {nom} » dépasse {s.max_upload_bytes // (1024 * 1024)} Mo.")
                        out.write(chunk)
                if size == 0:
                    raise HTTPException(422, f"« {nom} » est vide.")
                try:
                    with dest.open("rb") as fh:
                        sniff_kind(fh.read(4096), ext)
                except ReaderError as e:
                    raise HTTPException(422, f"« {nom} » : {e}") from e
                genre = types_fichiers[i] if i < len(types_fichiers) and types_fichiers[i] in TYPES_FICHIERS else "autre"
                stored.append({"path": str(dest), "nom": re.sub(r"[^\w .()-]", "_", nom)[:80], "type": genre})
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        ref = db.create_job(job_id, cipher.enc(form.model_dump_json()), form.jour_evenement, form.secteur, owner)
        db.update_job(job_id, fichiers_json=json.dumps(stored, ensure_ascii=False))
        db.audit(job_id, "visiteur", "depot", ip)
        runner.submit(job_id)
        return {"job_id": job_id, "reference": ref}

    @app.get("/api/diagnostics/{job_id}")
    def diagnostic(job_id: str, email: str = Depends(visitor_email)):
        return summary(own_job(job_id, email))

    @app.get("/api/diagnostics/{job_id}/sse")
    async def sse(job_id: str, request: Request, email: str = Depends(visitor_sse)):
        own_job(job_id, email)
        try:
            debut = int(request.headers.get("last-event-id", "-1")) + 1  # reconnexion : on reprend après le dernier événement reçu
        except ValueError:
            debut = 0

        async def flux():
            n = debut
            while True:
                job = await asyncio.to_thread(db.get_job, job_id)
                if not job:
                    return
                journal = loads(job["journal_json"], [])
                for i in range(n, len(journal)):
                    yield f"id: {i}\ndata: {json.dumps(journal[i], ensure_ascii=False)}\n\n"
                n = max(n, len(journal))
                if job["statut"] in TERMINAUX and n >= len(journal) or await request.is_disconnected():
                    return
                await asyncio.sleep(1)

        return StreamingResponse(flux(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/diagnostics/{job_id}/pdf")
    def lien_rapport(job_id: str, email: str = Depends(visitor_email)):
        job = own_job(job_id, email)
        kind = best_kind(job)
        if not kind:
            raise HTTPException(404, "Rapport indisponible")
        db.audit(job_id, "visiteur", f"lien_{kind}")
        return {"url": signed_url(job, kind)}

    @app.get("/api/fichiers/{job_id}/{kind}")
    def fichier(job_id: str, kind: str, token: str, request: Request):
        """Téléchargement par lien signé (e-mail, « Mes diagnostics », conseillers) : valable 7 jours."""
        if kind not in ("pdf", "docx"):
            raise HTTPException(404, "Type de fichier inconnu")
        job = any_job(job_id)
        subject = verify_token(s.secret_key, token, "dl")
        if not subject or not hmac.compare_digest(subject, job_id):
            raise HTTPException(403, "Lien invalide ou expiré")
        path = job.get(f"{kind}_path")
        if not path or not Path(path).exists():
            raise HTTPException(404, "Fichier non disponible")
        db.audit(job_id, "lien_signe", f"telechargement_{kind}", client_ip(request))
        return FileResponse(path, filename=Path(path).name, media_type=DOCX_MIME if kind == "docx" else "application/pdf")

    @app.post("/api/diagnostics/{job_id}/rappel")
    def rappel(job_id: str, email: str = Depends(visitor_email)):
        own_job(job_id, email)
        db.update_job(job_id, rappel_demande=1)
        db.audit(job_id, "visiteur", "demande_rappel")
        return {"ok": True}

    # ------------------------------------------------------------------ Mes diagnostics, entretiens, invitation
    @app.get("/api/me/diagnostics")
    def mes_diagnostics(email: str = Depends(visitor_email)):
        return [summary(j) for j in db.list_jobs(owner=owner_hash(s.secret_key, email))]

    @app.get("/api/entretiens/creneaux")
    def creneaux(jour: str, email: str = Depends(visitor_email)):
        if jour not in s.event_days:
            raise HTTPException(422, "Jour inconnu")
        return {"pris": db.taken_slots(jour, s.slot_capacity, exclude_owner=owner_hash(s.secret_key, email))}

    @app.post("/api/diagnostics/{job_id}/entretien")
    def reserver(job_id: str, payload: dict, email: str = Depends(visitor_email)):
        job = own_job(job_id, email)
        jour, heure = str(payload.get("jour", "")), str(payload.get("heure", ""))
        if jour not in s.event_days or not HEURE.match(heure) or heure not in s.slots or heure < s.slot_min_by_day.get(jour, "00:00"):
            raise HTTPException(422, "Créneau invalide pour ce jour.")
        if job["statut"] not in ("ENVOYE", "TERMINE"):
            raise HTTPException(409, "L'entretien se réserve une fois le diagnostic terminé.")
        if not db.book_slot(job_id, owner_hash(s.secret_key, email), jour, heure, s.slot_capacity):
            raise HTTPException(409, "Ce créneau vient d'être réservé : choisissez-en un autre.")
        db.audit(job_id, "visiteur", f"entretien_{jour}_{heure}")
        return {"ok": True}

    def invitation_data(email: str) -> tuple[bytes, str, str, str | None]:
        owner = owner_hash(s.secret_key, email)
        jobs = db.list_jobs(owner=owner, limit=1)
        booking = db.latest_booking(owner)
        jour = (booking or {}).get("jour") or (jobs[0]["jour_evenement"] if jobs else s.event_days[0])
        debut, fin = s.event_hours.get(jour, ("08:30", "17:30"))
        entretien = booking["heure"] if booking and booking["jour"] == jour else None
        data = ics.build_ics(owner[:12], jour, debut, fin, s.event_location, entretien, s.slot_minutes)
        return data, JOURS_LONGS.get(jour, jour), f"à partir de {debut.replace(':', 'h')}", entretien

    @app.get("/api/me/invitation.ics")
    def invitation_ics(email: str = Depends(visitor_email)):
        data, *_ = invitation_data(email)
        return Response(data, media_type="text/calendar; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="JPO-DC-KNOWING.ics"'})

    @app.post("/api/me/invitation/envoi")
    async def invitation_envoi(email: str = Depends(visitor_email)):
        if not mailer.configured(s):
            raise HTTPException(503, "L'envoi d'e-mails n'est pas configuré sur ce serveur.")
        data, jour_long, horaire, entretien = invitation_data(email)
        jobs = db.list_jobs(owner=owner_hash(s.secret_key, email), limit=1)
        nom = form_of(jobs[0])["contact_nom"] if jobs else ""
        try:
            await asyncio.to_thread(mailer.send_invitation, s, email, nom or "", jour_long, horaire, entretien, data)
        except Exception:  # noqa: BLE001
            log.exception("envoi de l'invitation en échec")
            raise HTTPException(502, "Impossible d'envoyer l'e-mail pour le moment.")
        return {"ok": True}

    @app.delete("/api/me")
    def supprimer_mes_donnees(request: Request, email: str = Depends(visitor_email)):
        """Droit à l'effacement (loi n°2013-450) : dépôts, rapports, entretiens, code."""
        owner = owner_hash(s.secret_key, email)
        for jid in db.delete_owner(owner):
            shutil.rmtree(s.uploads_dir / jid, ignore_errors=True)
            shutil.rmtree(s.reports_dir / jid, ignore_errors=True)
        db.audit(None, owner[:8], "effacement_donnees", client_ip(request))
        return {"ok": True}

    # ------------------------------------------------------------------ conseillers
    @app.post("/api/admin/login")
    async def login(request: Request, payload: dict):
        ip = client_ip(request)
        if not login_limiter.allow(ip):
            raise HTTPException(429, "Trop de tentatives")
        if not s.admin_accounts:
            raise HTTPException(503, "Espace conseiller désactivé (aucun compte configuré)")
        user = str(payload.get("email") or payload.get("username") or "").strip().lower()
        expected = s.admin_accounts.get(user, "")
        ok = hmac.compare_digest(str(payload.get("password", "")).encode(), (expected or secrets.token_hex(16)).encode()) and bool(expected)
        if not ok:
            db.audit(None, user[:60], "login_echec", ip)
            raise HTTPException(401, "Identifiants invalides")
        db.audit(None, user, "login", ip)
        return {"token": sign_token(s.secret_key, "admin", user, s.admin_token_ttl), "expire_dans_s": s.admin_token_ttl}

    def admin_view(job: dict, detail: bool = False) -> dict:
        form = form_of(job)
        booking = db.get_booking(job["id"])
        out = {"id": job["id"], "job_id": job["id"], "reference": job["ref"], "nom_reel": form["raison_sociale"], "type_client": form["type_client"],
               "secteur": job["secteur"], "effectif": form["effectif"], "created_at": job["created_at"], "completed_at": job["completed_at"],
               "statut": front_statut(job), "score_performance": job["score_performance"], "score_conformite": job["score_conformite"],
               "grade_performance": job["grade_performance"], "grade_conformite": job["grade_conformite"], "couverture_pct": job["couverture_pct"],
               "indicatif": bool((loads(job["resume_json"], {}) or {}).get("indicatif")), "entretien": booking["heure"] if booking else None,
               "entretien_realise": bool(job["entretien_realise"]), "rappel_demande": bool(job["rappel_demande"]),
               "rappel_effectue": bool(job["rappel_effectue"]), "alertes": loads(job["alertes_json"], []),
               "plan_action": loads(job["plan_action_json"], []), "erreur": job["erreur"], "pages": job["pages"],
               "lecture_partielle": bool(job["lecture_partielle"]), "modele_ia": job["modele_ia"], "email_envoye_at": job["email_envoye_at"],
               "contact": {"nom": form["contact_nom"], "email": form["contact_email"], "telephone": form["contact_telephone"]}}
        if detail:
            out |= {"formulaire": {k: v for k, v in form.items() if k not in ("contact_nom", "contact_email", "contact_telephone")},
                    "avertissements_ia": (loads(job["resultat_ia_json"], {}) or {}).get("ia", {}).get("meta", {}).get("warnings", []),
                    "journal": loads(job["journal_json"], []), "audit": db.audit_for(job["id"])}
        return out

    @app.get("/api/admin/diagnostics")
    def admin_liste(request: Request, jour: str | None = None, statut: str | None = None, user: str = Depends(admin_email)):
        db.audit(None, user, "liste", client_ip(request))
        return [admin_view(j) for j in db.list_jobs(jour, statut)]

    @app.get("/api/admin/diagnostics.csv")
    def admin_csv(request: Request, jour: str | None = None, user: str = Depends(admin_email)):
        db.audit(None, user, "export_csv", client_ip(request))
        cols = ["reference", "entreprise", "contact", "email", "telephone", "type_client", "secteur", "effectif", "statut", "performance",
                "conformite", "couverture_pct", "entretien", "entretien_realise", "rappel_demande", "rappel_effectue", "cree_le"]
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(cols)
        safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in "=+-@\t\r" else v  # anti-injection de formules Excel
        for j in db.list_jobs(jour):
            a = admin_view(j)
            w.writerow([safe(x) for x in (a["reference"], a["nom_reel"], a["contact"]["nom"], a["contact"]["email"], a["contact"]["telephone"],
                                          a["type_client"], a["secteur"], a["effectif"], a["statut"], a["score_performance"], a["score_conformite"],
                                          a["couverture_pct"], a["entretien"], int(a["entretien_realise"]), int(a["rappel_demande"]),
                                          int(a["rappel_effectue"]), a["created_at"])])
        name = f"diagnostics_{jour or 'tous'}.csv"
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/admin/diagnostics/{job_id}")
    def admin_detail(job_id: str, request: Request, user: str = Depends(admin_email)):
        db.audit(job_id, user, "consultation", client_ip(request))
        return admin_view(any_job(job_id), detail=True)

    @app.get("/api/admin/diagnostics/{job_id}/pdf")
    def admin_lien(job_id: str, request: Request, user: str = Depends(admin_email)):
        job = any_job(job_id)
        kind = best_kind(job)
        if not kind:
            raise HTTPException(404, "Rapport indisponible")
        db.audit(job_id, user, f"lien_{kind}", client_ip(request))
        return {"url": signed_url(job, kind)}

    @app.get("/api/admin/diagnostics/{job_id}/md")
    def admin_md(job_id: str, request: Request, user: str = Depends(admin_email)):
        job = any_job(job_id)
        if not job["md_path"] or not Path(job["md_path"]).exists():
            raise HTTPException(404, "Fichier non disponible")
        db.audit(job_id, user, "telechargement_md", client_ip(request))
        return FileResponse(job["md_path"], filename=Path(job["md_path"]).name, media_type="text/markdown")

    @app.post("/api/admin/diagnostics/{job_id}/renvoyer")
    async def admin_renvoyer(job_id: str, request: Request, user: str = Depends(admin_email)):
        any_job(job_id)
        try:
            await asyncio.to_thread(pipeline.resend, job_id)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        except Exception:  # noqa: BLE001
            log.exception("renvoi de l'e-mail en échec")
            raise HTTPException(502, "Impossible d'envoyer l'e-mail pour le moment.")
        db.audit(job_id, user, "renvoi_email", client_ip(request))
        return {"ok": True}

    @app.post("/api/admin/diagnostics/{job_id}/relancer")
    def admin_relancer(job_id: str, request: Request, user: str = Depends(admin_email)):
        job = any_job(job_id)
        if job["statut"] != "ERREUR":
            raise HTTPException(409, "Seuls les dépôts en erreur peuvent être relancés")
        files_ok = any(Path(f["path"]).exists() for f in loads(job["fichiers_json"], []))
        if not job["resultat_ia_json"] and not files_ok:
            raise HTTPException(409, "Fichiers sources supprimés : le visiteur doit refaire un dépôt")
        db.update_job(job_id, erreur=None, erreur_publique=None, completed_at=None)
        db.log_event(job_id, "EN_ATTENTE", 5, {"etape": "EN_ATTENTE", "progression": 5, "message": "Traitement relancé par un conseiller", "niveau": ""})
        db.audit(job_id, user, "relance", client_ip(request))
        runner.submit(job_id)
        return {"ok": True}

    def _flag(job_id: str, user: str, request: Request, **fields):
        any_job(job_id)
        db.update_job(job_id, **fields)
        db.audit(job_id, user, "/".join(fields), client_ip(request))
        return {"ok": True}

    @app.post("/api/admin/diagnostics/{job_id}/entretien-realise")
    def admin_entretien_realise(job_id: str, request: Request, user: str = Depends(admin_email)):
        return _flag(job_id, user, request, entretien_realise=1)

    @app.post("/api/admin/diagnostics/{job_id}/rappel-effectue")
    def admin_rappel_effectue(job_id: str, request: Request, user: str = Depends(admin_email)):
        return _flag(job_id, user, request, rappel_effectue=1)

    # ------------------------------------------------------------------ front statique (après les routes /api)
    if s.front_dir:
        app.mount("/", FrontFiles(directory=s.front_dir, html=True), name="front")
    return app

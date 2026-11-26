"""Étapes E–G : calcul (moteur_rapport), rédaction Markdown (generer_rapport), rendu Word, contrôle 4 pages."""
from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .textes import VERDICT

log = logging.getLogger("jpo.report")


class RenderError(Exception):
    pass


@dataclass
class ReportResult:
    docx: Path
    md: Path
    pdf: Path | None
    pages: int | None
    mode_compact: int
    resume: dict


def _load_engine(settings: Settings):
    for p in (str(settings.word_dir), str(settings.project_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import generer_rapport  # noqa: WPS433  (rédaction du Markdown, dossier word/)
    import moteur_rapport   # noqa: WPS433  (calculs et barèmes)
    return generer_rapport, moteur_rapport


def compute(settings: Settings, data: dict) -> dict:
    """Scores, grades, alertes et couverture (mêmes calculs que le rapport)."""
    _, M = _load_engine(settings)
    k = M.calc(data)
    perf, conf = M.score_performance(k), M.score_conformite(data, k)
    champs = [n for sect in (data["ia"]["compte_resultat"], *data["ia"]["bilan"].values()) for n in sect.values()]
    ok = sum(1 for n in champs if M.v(n) is not None)

    # Alertes pour les conseillers : [{niveau, texte}], de la plus grave à la moins grave
    alertes = [{"niveau": "critique" if kind == "bad" else "moyenne", "texte": txt} for kind, txt in conf["alertes"]]
    if (k["tn"] or 0) < 0:
        alertes.append({"niveau": "elevee", "texte": f"Trésorerie nette négative ({M.m(k['tn'])})"})
    alertes += [{"niveau": "critique" if "critique" in a else "info", "texte": a} for a in perf["alertes"]]
    ordre = {"critique": 0, "elevee": 1, "moyenne": 2, "info": 3}
    alertes.sort(key=lambda a: ordre[a["niveau"]])

    # Résumé pour le visiteur : points forts, points à traiter, alerte principale
    forts = [f"{b['nom']} : {b['detail']}" if b["detail"] else b["nom"]
             for b in perf["blocs"] if b["dispo"] and b["pts"] / b["max"] >= 0.6]
    forts += [f"{l['nom']} : {l['commentaire'].rstrip('.')}" for l in conf["lignes"] if l["pts"] == l["max"]]
    a_traiter = [a["texte"] for a in alertes if a["niveau"] != "info"]
    a_traiter += [f"{b['nom']} : {b['detail']}" if b["detail"] else b["nom"]
                  for b in perf["blocs"] if b["dispo"] and b["pts"] / b["max"] < 0.5]
    a_traiter += [f"{b['nom']} : non évalué (donnée manquante)" for b in perf["blocs"] if not b["dispo"]]
    principale = next((t for kind, t in conf["alertes"] if kind == "bad"), "")  # alerte de conformité la plus grave
    return dict(
        score_performance=perf["score"], grade_performance=perf["grade"], score_conformite=conf["score"],
        grade_conformite=conf["grade"], couverture_pct=round(ok / len(champs) * 100),
        points_evaluables=perf["couverture"], score_indicatif=perf["indicatif"],
        alertes=alertes, risque_redressement=conf["risque_redressement"],
        resume=dict(verdict=("Note indicative (documents partiels) : " if perf["indicatif"] else "") + VERDICT[perf["grade"]], points_forts=list(dict.fromkeys(forts))[:4],
                    points_a_traiter=list(dict.fromkeys(a_traiter))[:5], alerte_principale=principale.split(" : ")[0].rstrip("."),
                    indicatif=perf["indicatif"]),
    )


def _strip_dir_entries(docx: Path) -> None:
    """Retire les entrées de dossiers du zip : certains Word les refusent (équivalent de `zip -d`)."""
    tmp = docx.with_suffix(".tmp")
    with zipfile.ZipFile(docx) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if not item.filename.endswith("/"):
                zout.writestr(item, zin.read(item.filename))
    tmp.replace(docx)


def _render_docx_http(settings: Settings, md: Path, docx: Path) -> None:
    """Rendu par la fonction Node déployée à côté (Vercel : /api/docx), protégée par un secret partagé."""
    import httpx

    try:
        r = httpx.post(settings.docx_render_url, content=md.read_bytes(), timeout=settings.render_timeout,
                       headers={"Content-Type": "text/markdown; charset=utf-8", "X-Render-Secret": settings.secret_key,
                                **({"x-vercel-protection-bypass": settings.vercel_bypass} if settings.vercel_bypass else {})})
    except httpx.HTTPError as e:
        raise RenderError(f"service de rendu Word injoignable ({type(e).__name__})") from e
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        raise RenderError(f"rendu Word en échec (HTTP {r.status_code}) : {r.text[:300]}")
    docx.write_bytes(r.content)
    _strip_dir_entries(docx)


def _render_docx(settings: Settings, md: Path, docx: Path) -> None:
    if settings.docx_render_url:
        return _render_docx_http(settings, md, docx)
    if not settings.node_path:
        raise RenderError("Node.js introuvable (installer Node 20 ou plus)")
    if not any((d / "node_modules" / "docx").exists() for d in (settings.word_dir, settings.project_dir)):
        raise RenderError("dépendances Node absentes : lancer « npm install » à la racine du projet")
    r = subprocess.run([settings.node_path, str(settings.word_dir / "md_vers_word.js"), str(md), str(docx)],
                       capture_output=True, text=True, timeout=settings.render_timeout, cwd=str(settings.word_dir))
    if r.returncode != 0 or not docx.exists():
        raise RenderError(f"rendu Word en échec : {(r.stderr or r.stdout)[-400:]}")
    _strip_dir_entries(docx)


_pools: dict[str, queue.Queue] = {}
_pools_lock = threading.Lock()


def _profile_pool(settings: Settings) -> queue.Queue:
    """LibreOffice n'accepte qu'une instance par profil : un profil par worker, prêté le temps d'une conversion."""
    key = str(settings.lo_profile_dir)
    with _pools_lock:
        if key not in _pools:
            q: queue.Queue = queue.Queue()
            for i in range(max(settings.workers, 1)):
                d = settings.lo_profile_dir / f"p{i}"
                d.mkdir(parents=True, exist_ok=True)
                q.put(d)
            _pools[key] = q
        return _pools[key]


def _soffice(settings: Settings, profile: Path, *args: str, timeout: float) -> None:
    r = subprocess.run([settings.soffice_path, f"-env:UserInstallation={profile.as_uri()}", "--headless", *args],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        log.warning("LibreOffice a échoué (code %s) : %s", r.returncode, (r.stderr or r.stdout)[-300:])


def warm_up(settings: Settings) -> None:
    """Initialise les profils au démarrage pour que les premiers visiteurs n'attendent pas ~45 s de plus."""
    if not settings.soffice_path:
        return
    pool = _profile_pool(settings)
    profiles = [pool.get() for _ in range(pool.qsize())]

    def init(p: Path) -> None:
        try:
            _soffice(settings, p, "--terminate_after_init", timeout=settings.render_timeout)
        except (subprocess.TimeoutExpired, OSError):
            pass
        finally:
            pool.put(p)

    threads = [threading.Thread(target=init, args=(p,), daemon=True) for p in profiles]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def _to_pdf(settings: Settings, docx: Path, outdir: Path) -> Path | None:
    if not settings.soffice_path:
        return None
    pool = _profile_pool(settings)
    profile = pool.get(timeout=settings.render_timeout)
    broken = False
    try:
        _soffice(settings, profile, "--convert-to", "pdf", "--outdir", str(outdir), str(docx), timeout=settings.render_timeout)
    except (subprocess.TimeoutExpired, OSError):
        broken = True  # un soffice.bin zombie peut verrouiller ce profil : on le remplace
    finally:
        if broken:
            shutil.rmtree(profile, ignore_errors=True)
            profile = profile.with_name(f"p_{uuid.uuid4().hex[:6]}")
            profile.mkdir(parents=True, exist_ok=True)
        pool.put(profile)
    pdf = outdir / (docx.stem + ".pdf")
    return pdf if pdf.exists() else None


def _count_pages(pdf: Path) -> int | None:
    try:
        import pdfplumber
        with pdfplumber.open(pdf) as p:
            return len(p.pages)
    except Exception:
        return None


def build_report(settings: Settings, data: dict, out_dir: Path, base_name: str) -> ReportResult:
    """Produit .md, .docx et (si LibreOffice est présent) .pdf ; bascule en mode compact si > 4 pages."""
    G, _ = _load_engine(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path, docx_path = out_dir / f"{base_name}.md", out_dir / f"{base_name}.docx"
    pdf, pages, resume, compact = None, None, {}, 0
    for compact in (0, 1, 2):
        md, resume = G.rediger(data, compact)
        md_path.write_text(md, encoding="utf-8")
        _render_docx(settings, md_path, docx_path)
        pdf = _to_pdf(settings, docx_path, out_dir)
        pages = _count_pages(pdf) if pdf else None
        if pages is None or pages <= 4:
            break
    resume["pages"] = pages
    return ReportResult(docx=docx_path, md=md_path, pdf=pdf, pages=pages, mode_compact=compact, resume=resume)

"""FastAPI routes for the Resume Tailor local web app.

Endpoints (all under ``/api``):
  * ``GET  /api/health``  — liveness + whether content loaded.
  * ``GET  /api/content`` — the hand-authored library (for the left pane).
  * ``POST /api/score``   — score a pasted JD (deterministic baseline blended
                            with the local LLM when enabled), no packing.
  * ``POST /api/pack``    — score + auto-pack + real compile; returns the
                            selection and one-page fit gauge. PDF at /api/pdf.
  * ``GET  /api/pdf``     — the most recently compiled proposal PDF.
  * ``POST /api/export``  — score + pack + write the dated archive folder.

Content is loaded once and cached; ``POST /api/reload`` re-reads the YAML. The
PDF preview is compiled into a single session build dir. This is a local,
single-user app, so simple module-level state is fine.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.content import ContentError, load_content
from backend.llm import score_blended
from backend.models import Content
from backend.packer import pack_and_verify
from backend.archive import export_selection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_BUILD = PROJECT_ROOT / "build" / "session"
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(title="Resume Tailor")

_content: Content | None = None


def get_content() -> Content:
    """Lazily load + cache the library. Raises 500 with a friendly message on
    malformed content."""
    global _content
    if _content is None:
        try:
            _content = load_content()
        except ContentError as exc:
            raise HTTPException(status_code=500, detail=f"content error: {exc}") from exc
    return _content


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class ScoreRequest(BaseModel):
    jd_text: str = ""


class PackRequest(BaseModel):
    jd_text: str = ""
    pins: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)


class ExportRequest(PackRequest):
    company: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _content_view(content: Content) -> dict:
    """Serialize the library for the frontend: locked header content plus every
    job/project with its fixed bullet and swappable secondary bullets."""
    def item(i, kind):
        return {
            "id": i.id, "kind": kind,
            "name": getattr(i, "name", None),
            "company": getattr(i, "company", None),
            "title": getattr(i, "title", None),
            "tech": getattr(i, "tech", None),
            "dates": i.dates, "fixed_bullet": i.fixed_bullet, "tags": i.tags,
            "bullets": [{"id": b.id, "text": b.text, "tier": b.tier, "tags": b.tags}
                        for b in i.bullets],
        }
    return {
        "name": content.profile.name,
        "contacts": [c.model_dump() for c in content.profile.contacts],
        "education": [e.model_dump() for e in content.profile.education],
        "skills": [s.model_dump() for s in content.profile.skills],
        "experience": [item(j, "job") for j in content.experience],
        "projects": [item(p, "project") for p in content.projects],
        "settings": content.profile.settings.model_dump(),
    }


def _score_view(result) -> dict:
    return {
        "bullets": {
            bid: {
                "raw": round(b.raw, 4),
                "normalized": round(b.normalized, 4),
                "llm_score": b.llm_score,
                "final_score": (round(b.final_score, 4) if b.final_score is not None else None),
                "pack_score": round(b.pack_score, 4),
                "matched": b.matched_requirements or b.matched_skills,
            }
            for bid, b in result.bullets.items()
        },
        "items": {
            iid: {"total": round(i.total, 4), "kind": i.kind,
                  "matched": i.matched_skills}
            for iid, i in result.items.items()
        },
    }


def _run_pack(content: Content, req: PackRequest):
    """Score (baseline + LLM blend) then auto-pack with a real compile."""
    outcome = score_blended(content, req.jd_text, content.profile.settings)
    scores = {bid: bs.pack_score for bid, bs in outcome.result.bullets.items()}
    packed = pack_and_verify(content, scores, pins=req.pins, excludes=req.excludes,
                             build_dir=SESSION_BUILD)
    return outcome, scores, packed


def _selection_view(content: Content, packed) -> dict:
    return {
        "status": packed.status,
        "fits": packed.fit.fits,
        "compiles": packed.compiles,
        "total_score": round(packed.total_score, 4),
        "fit": {
            "pages": packed.fit.pages,
            "remaining_cm": (round(packed.fit.remaining_cm, 2)
                             if packed.fit.remaining_cm is not None else None),
            "approx_lines_left": (round(packed.fit.approx_lines_left, 1)
                                  if packed.fit.approx_lines_left is not None else None),
        },
        "selection": {
            "experience": packed.selection.exp_bullets,
            "open_projects": packed.selection.open_projects,
            "project_bullets": packed.selection.proj_bullets,
        },
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict:
    try:
        get_content()
        return {"ok": True, "content_loaded": True}
    except HTTPException as exc:
        return {"ok": False, "content_loaded": False, "detail": exc.detail}


@app.post("/api/reload")
def reload_content() -> dict:
    global _content
    _content = None
    get_content()
    return {"ok": True}


@app.get("/api/content")
def content_route() -> dict:
    return _content_view(get_content())


@app.post("/api/score")
def score_route(req: ScoreRequest) -> dict:
    content = get_content()
    outcome = score_blended(content, req.jd_text, content.profile.settings)
    return {"llm_used": outcome.llm_used, **_score_view(outcome.result)}


@app.post("/api/pack")
def pack_route(req: PackRequest) -> dict:
    content = get_content()
    outcome, _scores, packed = _run_pack(content, req)
    return {"llm_used": outcome.llm_used, **_selection_view(content, packed)}


@app.get("/api/pdf")
def pdf_route():
    pdf = SESSION_BUILD / "resume.pdf"
    if not pdf.exists():
        raise HTTPException(status_code=404, detail="no compiled PDF yet; run /api/pack first")
    # inline disposition so the frontend <iframe> renders it in place; an
    # "attachment" (the FileResponse default when a filename is set) makes the
    # browser download the file and leaves the preview pane blank.
    return FileResponse(str(pdf), media_type="application/pdf", filename="resume.pdf",
                        content_disposition_type="inline")


@app.post("/api/export")
def export_route(req: ExportRequest) -> dict:
    content = get_content()
    outcome, scores, packed = _run_pack(content, req)
    folder = export_selection(
        content, packed.selection, req.jd_text, scores, packed.fit.pdf_path,
        company=req.company, fit=packed.fit, total_score=packed.total_score,
        llm_used=outcome.llm_used, pins=req.pins, excludes=req.excludes,
    )
    return {
        "ok": packed.fit.fits,
        "archive": str(folder),
        "llm_used": outcome.llm_used,
        **_selection_view(content, packed),
    }


# Serve the frontend (step 7) from the same origin when present.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

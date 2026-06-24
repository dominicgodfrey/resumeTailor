"""Write a dated export folder for each finished resume.

On export we re-render the selection's ``.tex`` (deterministic, so it matches the
compiled PDF) and write four artifacts into
``archive/<YYYY-MM-DD>-<company-slug>/``:

  * ``resume.pdf``   — the compiled one-page PDF (copied from the build dir),
  * ``resume.tex``   — the LaTeX source that produced it,
  * ``jd.txt``       — the pasted job description, and
  * ``selection.json`` — the full selection, pins/excludes, and scores,

so any past export can be reproduced or reused later.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

from backend.models import Content
from backend.packer import Selection, selection_to_context
from backend.render import FitResult, render_tex

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = PROJECT_ROOT / "archive"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "resume"


def _fit_summary(fit: FitResult | None) -> dict | None:
    if fit is None:
        return None
    return {
        "pages": fit.pages,
        "fits": fit.fits,
        "remaining_cm": round(fit.remaining_cm, 2) if fit.remaining_cm is not None else None,
        "status": fit.status,
    }


def export_selection(
    content: Content,
    selection: Selection,
    jd_text: str,
    scores: dict[str, float],
    pdf_path: str | Path | None,
    *,
    company: str | None = None,
    fit: FitResult | None = None,
    total_score: float | None = None,
    llm_used: bool | None = None,
    pins: list[str] | None = None,
    excludes: list[str] | None = None,
    archive_root: Path | None = None,
) -> Path:
    """Write the export folder and return its path. Re-exporting the same
    company on the same day overwrites the previous folder (latest wins)."""
    root = archive_root if archive_root is not None else ARCHIVE_DIR
    folder = root / f"{date.today().isoformat()}-{slugify(company or 'resume')}"
    folder.mkdir(parents=True, exist_ok=True)

    tex = render_tex(selection_to_context(content, selection))
    (folder / "resume.tex").write_text(tex, encoding="utf-8")
    (folder / "jd.txt").write_text(jd_text, encoding="utf-8")

    if pdf_path and Path(pdf_path).exists():
        shutil.copyfile(pdf_path, folder / "resume.pdf")

    selected = selection.all_bullets()
    payload = {
        "date": date.today().isoformat(),
        "company": company,
        "llm_used": llm_used,
        "total_score": total_score,
        "fit": _fit_summary(fit),
        "pins": pins or [],
        "excludes": excludes or [],
        "selection": {
            "experience": selection.exp_bullets,
            "open_projects": selection.open_projects,
            "project_bullets": selection.proj_bullets,
            "coursework": selection.coursework,
        },
        "scores": {bid: round(scores.get(bid, 0.0), 4) for bid in selected},
    }
    (folder / "selection.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return folder

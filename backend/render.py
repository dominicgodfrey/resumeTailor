"""Render the resume LaTeX template and measure one-page fit.

Step 1 of the build: map a content context onto the Jake Gutierrez template
(adapted for XeTeX/Tectonic), compile with Tectonic, and report whether the
result fits on one page plus how much vertical room remains.

The fit probe uses ``zref-savepos``: two markers (``rt_top`` / ``rt_end``) are
recorded into the ``.aux`` as absolute page positions. We read their vertical
positions back and take the absolute difference as the used content height
(engine-sign-agnostic), then subtract from ``\\textheight`` (emitted to the log
via ``\\typeout``) to get the remaining space. A >1-page PDF is a hard overflow
regardless of the probe.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PROJECT_ROOT / "templates"

SP_PER_PT = 65536.0          # TeX scaled points per point
CM_PER_PT = 2.54 / 72.27     # 1pt = 1/72.27 in
PT_PER_LINE = 13.6           # ~baselineskip at 11pt, for a rough "lines left" gauge


def _jinja_env() -> Environment:
    """Environment with LaTeX-safe delimiters so ``{#1}`` etc. pass through."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        block_start_string="((*",
        block_end_string="*))",
        variable_start_string="(((",
        variable_end_string=")))",
        comment_start_string="((=",
        comment_end_string="=))",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        undefined=StrictUndefined,
    )


def render_tex(context: dict, template_name: str = "resume.tex.j2") -> str:
    return _jinja_env().get_template(template_name).render(**context)


def find_tectonic() -> str:
    """Locate the Tectonic binary: TECTONIC_PATH, then tools/, then PATH."""
    env = os.environ.get("TECTONIC_PATH")
    if env and Path(env).exists():
        return env
    local = PROJECT_ROOT / "tools" / ("tectonic.exe" if os.name == "nt" else "tectonic")
    if local.exists():
        return str(local)
    found = shutil.which("tectonic")
    if found:
        return found
    raise FileNotFoundError(
        "Tectonic not found. Set TECTONIC_PATH, drop the binary in tools/, or add it to PATH."
    )


@dataclass
class FitResult:
    pages: int
    fits: bool
    textheight_pt: float | None
    used_pt: float | None
    remaining_pt: float | None
    remaining_cm: float | None
    approx_lines_left: float | None
    pdf_path: str
    status: str


def _parse_textheight(log: str) -> float | None:
    m = re.search(r"RT_TEXTHEIGHT=([0-9.]+)pt", log)
    return float(m.group(1)) if m else None


def _parse_posy(aux: str, label: str) -> int | None:
    # zref-savepos writes: \zref@newlabel{<label>}{\posx{..}\posy{..}}
    m = re.search(r"\\zref@newlabel\{" + re.escape(label) + r"\}\{.*?\\posy\{(-?\d+)\}", aux)
    return int(m.group(1)) if m else None


def compile_pdf(tex: str, build_dir: Path) -> tuple[Path, str, str]:
    """Compile ``tex`` with Tectonic; return (pdf_path, log_text, aux_text)."""
    build_dir.mkdir(parents=True, exist_ok=True)
    tex_path = build_dir / "resume.tex"
    tex_path.write_text(tex, encoding="utf-8")
    cmd = [
        find_tectonic(),
        "--keep-intermediates",
        "--keep-logs",
        "--outdir", str(build_dir),
        str(tex_path),
    ]
    # Keep Tectonic's package/format cache inside the project (avoids AppData
    # write issues and keeps the toolchain self-contained).
    cache_dir = PROJECT_ROOT / ".tectonic-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TECTONIC_CACHE_DIR": str(cache_dir)}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
    pdf_path = build_dir / "resume.pdf"
    log_path = build_dir / "resume.log"
    aux_path = build_dir / "resume.aux"
    log = (log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "")
    log += "\n--- stderr ---\n" + proc.stderr
    aux = aux_path.read_text(encoding="utf-8", errors="replace") if aux_path.exists() else ""
    if not pdf_path.exists():
        raise RuntimeError(
            "Tectonic did not produce a PDF.\n"
            f"exit={proc.returncode}\nSTDERR:\n{proc.stderr}\n"
        )
    return pdf_path, log, aux


def measure_fit(pdf_path: Path, log: str, aux: str) -> FitResult:
    pages = len(PdfReader(str(pdf_path)).pages)
    fits = pages == 1
    textheight = _parse_textheight(log)
    top = _parse_posy(aux, "rt_top")
    end = _parse_posy(aux, "rt_end")

    used_pt = remaining_pt = remaining_cm = lines_left = None
    if fits and top is not None and end is not None:
        used_pt = abs(top - end) / SP_PER_PT
        if textheight is not None:
            remaining_pt = textheight - used_pt
            remaining_cm = remaining_pt * CM_PER_PT
            lines_left = remaining_pt / PT_PER_LINE

    if not fits:
        status = f"OVERFLOW: content spans {pages} pages"
    elif remaining_cm is not None:
        status = f"FITS: ~{remaining_cm:.2f} cm (~{lines_left:.1f} lines) to spare"
    else:
        status = "FITS: 1 page (detailed fit probe unavailable)"

    return FitResult(
        pages=pages,
        fits=fits,
        textheight_pt=textheight,
        used_pt=used_pt,
        remaining_pt=remaining_pt,
        remaining_cm=remaining_cm,
        approx_lines_left=lines_left,
        pdf_path=str(pdf_path),
        status=status,
    )


def compile_and_measure(context: dict, build_dir: Path | None = None) -> FitResult:
    target = build_dir or Path(tempfile.mkdtemp(prefix="resumetailor_"))
    tex = render_tex(context)
    pdf_path, log, aux = compile_pdf(tex, target)
    return measure_fit(pdf_path, log, aux)

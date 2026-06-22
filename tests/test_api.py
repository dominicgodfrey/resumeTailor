"""API tests via Starlette's TestClient.

Fast routes (health/content/score) run with the LLM disabled so they're
deterministic and offline. Routes that compile (pack/export/pdf) are skipped
when Tectonic isn't installed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.app as appmod  # noqa: E402
import backend.archive as archivemod  # noqa: E402
from backend.content import load_content  # noqa: E402
from backend.render import find_tectonic  # noqa: E402

JD = """Requirements:
- Python and PostgreSQL
- REST APIs
Nice to have:
- React
"""


def _has_tectonic() -> bool:
    try:
        find_tectonic()
        return True
    except Exception:
        return False


needs_tectonic = pytest.mark.skipif(not _has_tectonic(), reason="Tectonic not installed")


@pytest.fixture
def client():
    # Inject seed content with the LLM disabled so scoring is offline/baseline.
    content = load_content()
    content.profile.settings.llm.enabled = False
    appmod._content = content
    with TestClient(appmod.app) as c:
        yield c
    appmod._content = None


def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["content_loaded"] is True


def test_content_view_shape(client):
    data = client.get("/api/content").json()
    assert data["name"]
    assert len(data["experience"]) == 3
    assert len(data["projects"]) == 2
    job = data["experience"][0]
    assert job["fixed_bullet"]
    assert "bullets" in job and "tier" in job["bullets"][0]


def test_score_baseline(client):
    r = client.post("/api/score", json={"jd_text": JD})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_used"] is False
    # The full-stack research bullet should score above zero for this JD.
    assert body["bullets"]["ra-fullstack"]["pack_score"] > 0
    # An irrelevant IT bullet should be zero.
    assert body["bullets"]["it-maintain"]["pack_score"] == 0


def test_score_empty_jd_all_zero(client):
    body = client.post("/api/score", json={"jd_text": ""}).json()
    assert all(b["pack_score"] == 0 for b in body["bullets"].values())


def test_pdf_404_before_pack(client):
    # Ensure a clean slate: remove any leftover session PDF.
    pdf = appmod.SESSION_BUILD / "resume.pdf"
    if pdf.exists():
        pdf.unlink()
    assert client.get("/api/pdf").status_code == 404


@needs_tectonic
def test_pack_then_pdf(client):
    r = client.post("/api/pack", json={"jd_text": JD})
    assert r.status_code == 200
    body = r.json()
    assert body["fits"] is True
    assert body["selection"]["open_projects"]            # at least one project opened
    pdf = client.get("/api/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF"


@needs_tectonic
def test_pack_honors_exclude(client):
    body = client.post("/api/pack", json={"jd_text": JD, "excludes": ["proj-gitlytics"]}).json()
    assert "proj-gitlytics" not in body["selection"]["open_projects"]


@needs_tectonic
def test_export_writes_archive(client, tmp_path, monkeypatch):
    monkeypatch.setattr(archivemod, "ARCHIVE_DIR", tmp_path / "archive")
    r = client.post("/api/export", json={"jd_text": JD, "company": "Acme Corp"})
    assert r.status_code == 200
    folder = Path(r.json()["archive"])
    assert folder.exists()
    assert folder.name.endswith("-acme-corp")
    for name in ("resume.pdf", "resume.tex", "jd.txt", "selection.json"):
        assert (folder / name).exists(), f"missing {name}"
    # The archived JD round-trips and the .tex carries authored bullets verbatim.
    assert (folder / "jd.txt").read_text(encoding="utf-8") == JD
    tex = (folder / "resume.tex").read_text(encoding="utf-8")
    # A locked fixed bullet is always present, carried verbatim into the .tex.
    assert "Developed a REST API using FastAPI and PostgreSQL" in tex

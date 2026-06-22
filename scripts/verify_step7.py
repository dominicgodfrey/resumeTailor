"""Step 7 verification: the frontend is present and served from the same origin
as the API. Confirms the three static assets exist, are mounted at ``/``, and
wire to one another (index.html references styles.css + app.js; app.js calls the
real API routes). No browser is driven — this checks the static contract only;
manual e2e (paste JD -> Score/Auto-pack -> Export) is step 8.

Run:  python scripts/verify_step7.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import backend.app as appmod  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def main() -> int:
    for name in ("index.html", "styles.css", "app.js"):
        assert (FRONTEND / name).exists(), f"missing frontend/{name}"

    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    appjs = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "styles.css" in index and "app.js" in index, "index.html does not link its assets"

    # The UI must exercise every backend route the plan calls for.
    for route in ("/api/content", "/api/score", "/api/pack", "/api/pdf", "/api/export"):
        assert route in appjs, f"app.js never calls {route}"

    appmod._content = None
    with TestClient(appmod.app) as client:
        idx = client.get("/")
        assert idx.status_code == 200 and "Resume Tailor" in idx.text, "index not served at /"
        for name, ctype in (("styles.css", "text/css"), ("app.js", "javascript")):
            r = client.get("/" + name)
            assert r.status_code == 200 and ctype in r.headers["content-type"], f"{name} not served"
        assert client.get("/api/content").status_code == 200, "API still reachable under mount"

    print("OK: frontend assets present, mounted at /, and wired to the API routes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

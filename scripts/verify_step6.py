"""Step 6 verification: drive the FastAPI app end-to-end through TestClient —
content -> score -> pack -> pdf -> export — against the real seed library, with
the LLM left enabled (live Ollama if available, graceful fallback otherwise).

Run:  python scripts/verify_step6.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import backend.app as appmod  # noqa: E402

JD = """\
Backend Software Engineer

Requirements:
- Strong Python and PostgreSQL experience
- Experience building REST APIs (FastAPI or Flask)
- Familiarity with OAuth and authentication flows
- Docker / containerization

Nice to have:
- React / frontend experience
- CI/CD pipelines
"""


def main() -> int:
    appmod._content = None  # force a fresh load of the real seed content
    with TestClient(appmod.app) as client:
        assert client.get("/api/health").json()["content_loaded"] is True

        content = client.get("/api/content").json()
        print(f"Content: {content['name']}  "
              f"{len(content['experience'])} jobs, {len(content['projects'])} projects")

        score = client.post("/api/score", json={"jd_text": JD}).json()
        print(f"\nScore (llm_used={score['llm_used']}) — top bullets:")
        top = sorted(score["bullets"].items(), key=lambda kv: -kv[1]["pack_score"])[:5]
        for bid, b in top:
            if b["pack_score"] <= 0:
                continue
            print(f"  {b['pack_score']:.3f}  {bid}  <- {', '.join(b['matched'])}")

        pack = client.post("/api/pack", json={"jd_text": JD}).json()
        print(f"\nPack: {pack['status']}  (compiles={pack['compiles']}, "
              f"score={pack['total_score']})")
        print("  open projects:", pack["selection"]["open_projects"])

        pdf = client.get("/api/pdf")
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF", "PDF not served"
        print(f"  PDF served: {len(pdf.content)} bytes")

        export = client.post("/api/export", json={"jd_text": JD, "company": "Example Inc"}).json()
        folder = Path(export["archive"])
        artifacts = sorted(p.name for p in folder.iterdir())
        print(f"\nExport -> {folder}")
        print("  artifacts:", artifacts)

        assert pack["fits"] is True, "proposal did not fit one page"
        assert pack["selection"]["open_projects"], "no project opened"
        for name in ("resume.pdf", "resume.tex", "jd.txt", "selection.json"):
            assert name in artifacts, f"missing {name}"
    print("\nOK: full API flow (score -> pack -> pdf -> export) verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

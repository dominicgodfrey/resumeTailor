"""Step 3 verification: score the seeded library against a sample JD and print
the ranked bullets, item totals, and LLM shortlist. Pure deterministic Stage A.

Run:  python scripts/verify_step3.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.content import load_content  # noqa: E402
from backend.scoring import score, shortlist  # noqa: E402

SAMPLE_JD = """\
Backend Software Engineer

Responsibilities:
- Build and maintain REST APIs in Python (FastAPI or Flask)
- Design and query PostgreSQL databases
- Containerize services with Docker

Requirements:
- Strong Python and PostgreSQL experience
- Experience building REST APIs
- Familiarity with OAuth and authentication flows

Nice to have:
- React / frontend experience
- CI/CD pipelines
"""


def main() -> int:
    content = load_content()
    result = score(content, SAMPLE_JD)

    print("=== JD skill weights (deterministic) ===")
    for skill, hit in sorted(result.jd.hits.items(), key=lambda kv: -kv[1].weight):
        print(f"  {skill:12s} w={hit.weight:.2f}  freq={hit.frequency}  section={hit.section}")

    print("\n=== Ranked secondary bullets ===")
    for b in result.ranked_bullets():
        if b.raw <= 0:
            continue
        print(f"  {b.raw:5.2f}  [{b.item_id}] {b.bullet_id}  <- {', '.join(b.matched_skills)}")

    print("\n=== Item totals (inclusion signal) ===")
    for item in sorted(result.items.values(), key=lambda i: -i.total):
        print(f"  {item.total:5.2f}  {item.kind:8s} {item.item_id}  (tags={item.tag_score:.2f})")

    sl = shortlist(result, 8)
    print("\n=== LLM shortlist (top 8) ===")
    print("  " + (", ".join(sl) if sl else "(none)"))

    # Sanity: a Python/Postgres/REST JD should rank a backend bullet on top and
    # surface Gitlytics (full-stack Flask/Postgres/REST) as a strong project.
    if not sl:
        print("\nFAIL: shortlist is empty for a JD that clearly matches the library.")
        return 1
    top = result.ranked_bullets()[0]
    if top.raw <= 0:
        print("\nFAIL: top bullet scored zero.")
        return 1
    print("\nOK: scoring produced a non-trivial ranking and shortlist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

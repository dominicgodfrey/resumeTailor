"""Step 5 verification: score the seeded library against a sample JD, auto-pack
a one-page proposal, and confirm it via a real Tectonic compile + fit probe.

Run:  python scripts/verify_step5.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.content import load_content  # noqa: E402
from backend.packer import pack_and_verify  # noqa: E402
from backend.scoring import score  # noqa: E402

SAMPLE_JD = """\
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
    content = load_content()
    result = score(content, SAMPLE_JD)
    scores = {bid: bs.pack_score for bid, bs in result.bullets.items()}

    build_dir = Path(__file__).resolve().parents[1] / "build" / "verify_step5"
    packed = pack_and_verify(content, scores, build_dir=build_dir)

    print("Compiles:", packed.compiles)
    print("Status:  ", packed.status)
    print("Score:   ", round(packed.total_score, 3))
    print("PDF:     ", packed.fit.pdf_path)

    print("\n=== Experience (locked; secondary bullets packed) ===")
    for job in content.experience:
        chosen = packed.selection.exp_bullets.get(job.id, [])
        print(f"  {job.id}: fixed + {chosen}")

    print("\n=== Projects opened (score-governed) ===")
    if not packed.selection.open_projects:
        print("  (none)")
    for pid in packed.selection.open_projects:
        print(f"  {pid}: fixed + {packed.selection.proj_bullets.get(pid, [])}")

    if not packed.fit.fits:
        print("\nFAIL: packed proposal does not fit one page.")
        return 1
    if not packed.selection.open_projects:
        print("\nFAIL: expected at least one project to be opened for this JD.")
        return 1
    print("\nOK: one-page proposal packed and compile-verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

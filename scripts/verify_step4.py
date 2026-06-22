"""Step 4 verification: run the blended Stage A + Stage B pipeline.

If Ollama is up with the configured model, this exercises the live extract +
re-rank path and prints blended scores. If it's down, it demonstrates the
graceful fallback to the deterministic baseline. Either way it must succeed.

Run:  python scripts/verify_step4.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from backend.content import load_content  # noqa: E402
from backend.llm import OllamaClient, score_blended  # noqa: E402
from backend.scoring import score  # noqa: E402

SAMPLE_JD = """\
Backend Software Engineer

Requirements:
- Strong Python and PostgreSQL experience
- Experience building REST APIs (FastAPI or Flask)
- Familiarity with OAuth and authentication flows

Nice to have:
- React / frontend experience
- CI/CD pipelines
"""


def main() -> int:
    content = load_content()
    settings = content.profile.settings

    # Probe the live server first so we can report which path ran.
    live = False
    try:
        OllamaClient(base_url=settings.llm.base_url, model=settings.llm.model,
                     thinking=settings.llm.thinking).chat(
            [{"role": "system", "content": "reply with {\"ok\":1}"},
             {"role": "user", "content": "ping"}])
        live = True
    except Exception as exc:  # noqa: BLE001
        print(f"(Ollama not reachable: {exc})")

    outcome = score_blended(content, SAMPLE_JD, settings)
    print(f"\nLLM enabled: {settings.llm.enabled}   live server: {live}   "
          f"llm_used: {outcome.llm_used}\n")

    print("=== Blended ranking (pack_score) ===")
    ranked = sorted(outcome.result.bullets.values(),
                    key=lambda b: (-b.pack_score, b.bullet_id))
    for b in ranked:
        if b.pack_score <= 0:
            continue
        llm = f"{b.llm_score:.2f}" if b.llm_score is not None else "  - "
        reqs = ", ".join(b.matched_requirements or b.matched_skills)
        print(f"  pack={b.pack_score:.3f}  base={b.normalized:.2f}  llm={llm}  "
              f"[{b.item_id}] {b.bullet_id}  <- {reqs}")

    # Invariants: pipeline always yields a usable result; if the LLM ran, at
    # least one bullet carries a blended final score.
    if outcome.llm_used:
        assert any(b.final_score is not None for b in outcome.result.bullets.values())
        print("\nOK: live LLM path produced blended scores.")
    else:
        baseline = score(content, SAMPLE_JD)
        top_base = baseline.ranked_bullets()[0].bullet_id
        top_fallback = ranked[0].bullet_id if ranked else None
        assert top_fallback == top_base, "fallback ranking should match baseline"
        print("\nOK: graceful fallback — ranking equals the deterministic baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

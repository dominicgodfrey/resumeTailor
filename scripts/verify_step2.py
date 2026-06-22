"""Step 2 verification: load and validate the seeded content library, then
assert the structural invariants the rest of the pipeline relies on.

Run:  python scripts/verify_step2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.content import ContentError, load_content  # noqa: E402


def main() -> int:
    try:
        content = load_content()
    except ContentError as exc:
        print("FAIL: content did not load/validate:")
        print(" ", exc)
        return 1

    profile = content.profile
    print("Name:       ", profile.name)
    print("Contacts:   ", len(profile.contacts))
    print("Education:  ", len(profile.education))
    print("Skills:     ", len(profile.skills), "categories")
    print("Experience: ", len(content.experience), "jobs")
    print("Projects:   ", len(content.projects))
    print("Aliases:    ", len(content.aliases), "canonical skills")
    print("Settings:   ", profile.settings.model_dump())

    problems: list[str] = []

    # Every job is locked content and must carry a fixed first bullet.
    for job in content.experience:
        if not job.fixed_bullet:
            problems.append(f"experience '{job.id}' is missing a fixed_bullet")

    # Projects only show their fixed bullet when selected, but it must exist so
    # the packer has something to anchor an opened project on.
    for proj in content.projects:
        if not proj.fixed_bullet:
            problems.append(f"project '{proj.id}' is missing a fixed_bullet")

    # min_bullets_per_open_project must be satisfiable: an opened project needs
    # at least that many secondary bullets available.
    floor = profile.settings.min_bullets_per_open_project
    for proj in content.projects:
        if len(proj.bullets) < floor:
            problems.append(
                f"project '{proj.id}' has {len(proj.bullets)} secondary bullets "
                f"< min_bullets_per_open_project ({floor})"
            )

    if problems:
        print("\nFAIL: invariant violations:")
        for p in problems:
            print("  -", p)
        return 1

    print("\nOK: content loads, validates, and satisfies invariants.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

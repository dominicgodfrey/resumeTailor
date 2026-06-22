"""Content-immutability guarantee: the rendered LaTeX must contain every bullet
(fixed and secondary) byte-for-byte from the source YAML. Nothing in the
pipeline — least of all the LLM — is allowed to rewrite authored content.

This checks at the .tex level: Tectonic typesets the LaTeX verbatim, so a bullet
present in the source is present in the PDF. (No compile needed here.)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.content import load_content  # noqa: E402
from backend.packer import Selection, selection_to_context  # noqa: E402
from backend.render import render_tex  # noqa: E402


def _full_selection(content) -> Selection:
    """Select everything: all experience secondary bullets, all projects open
    with all their bullets — the widest immutability surface."""
    sel = Selection()
    for job in content.experience:
        sel.exp_bullets[job.id] = [b.id for b in job.bullets]
    for proj in content.projects:
        sel.open_projects.append(proj.id)
        sel.proj_bullets[proj.id] = [b.id for b in proj.bullets]
    return sel


def test_rendered_tex_contains_every_bullet_verbatim():
    content = load_content()
    sel = _full_selection(content)
    tex = render_tex(selection_to_context(content, sel))

    for item in (*content.experience, *content.projects):
        if item.fixed_bullet:
            assert item.fixed_bullet in tex, f"fixed bullet of {item.id} altered/missing"
        for b in item.bullets:
            assert b.text in tex, f"bullet {b.id} altered/missing in rendered LaTeX"


def test_rendered_tex_contains_locked_header_content():
    content = load_content()
    tex = render_tex(selection_to_context(content, _full_selection(content)))
    assert content.profile.name in tex
    for skill in content.profile.skills:
        assert skill.items in tex          # static skills block verbatim
    for edu in content.profile.education:
        assert edu.school in tex

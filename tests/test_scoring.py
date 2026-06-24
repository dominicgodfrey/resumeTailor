"""Unit tests for the deterministic scorer (Stage A).

Covers alias expansion, JD section/frequency weighting, bullet + item scoring,
normalization, and the shortlister. No model, no compile — pure functions.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.models import Bullet, Content, Course, Job, Profile, Project  # noqa: E402
from backend.scoring import (  # noqa: E402
    BASE_WEIGHT,
    FREQ_BONUS,
    SECTION_WEIGHTS,
    analyze_jd,
    build_canonical_map,
    canonicalize,
    collect_content_tags,
    score,
    score_content,
    score_coursework,
    shortlist,
)

ALIASES = {
    "postgres": ["postgresql", "psql"],
    "react": ["reactjs", "react.js"],
    "c++": ["cpp", "c/c++"],
    "ci": ["ci/cd", "github actions"],
}


def _content(**kw) -> Content:
    profile = Profile(name="Test")
    return Content(profile=profile, aliases=ALIASES, **kw)


# --------------------------------------------------------------------------- #
# Alias expansion
# --------------------------------------------------------------------------- #
def test_canonicalize_variants_and_passthrough():
    cmap = build_canonical_map(ALIASES)
    assert canonicalize("postgresql", cmap) == "postgres"
    assert canonicalize("PSQL", cmap) == "postgres"          # case-insensitive
    assert canonicalize("postgres", cmap) == "postgres"      # idempotent
    assert canonicalize("kubernetes", cmap) == "kubernetes"  # unknown passes through


def test_jd_matches_via_alias_variant():
    # JD says "PostgreSQL"; the canonical skill is "postgres".
    jd = analyze_jd("Experience with PostgreSQL required", ALIASES, {"postgres"})
    assert jd.weight_of("postgres") > 0


def test_unknown_content_tag_still_matched_from_jd():
    # "celery" has no alias entry but is a content tag; JD mentions it.
    jd = analyze_jd("We use Celery for async tasks", ALIASES, {"celery"})
    assert jd.weight_of("celery") > 0


# --------------------------------------------------------------------------- #
# Section + frequency weighting
# --------------------------------------------------------------------------- #
def test_required_section_outweighs_nice_to_have():
    jd_text = "Requirements:\nPython\n\nNice to have:\nDocker"
    jd = analyze_jd(jd_text, {}, {"python", "docker"})
    assert jd.hits["python"].section == "required"
    assert jd.hits["docker"].section == "nice"
    assert jd.weight_of("python") > jd.weight_of("docker")
    assert jd.weight_of("python") == BASE_WEIGHT * SECTION_WEIGHTS["required"]
    assert jd.weight_of("docker") == BASE_WEIGHT * SECTION_WEIGHTS["nice"]


def test_frequency_increases_weight_up_to_cap():
    once = analyze_jd("python", {}, {"python"}).weight_of("python")
    twice = analyze_jd("python python", {}, {"python"}).weight_of("python")
    assert twice > once
    assert twice == BASE_WEIGHT * SECTION_WEIGHTS["body"] * (1.0 + FREQ_BONUS)


def test_strongest_section_wins_across_lines():
    # Mentioned in the body and again under Requirements -> required wins.
    jd_text = "We build APIs in Python.\nRequirements:\nPython expertise"
    jd = analyze_jd(jd_text, {}, {"python"})
    assert jd.hits["python"].section == "required"


def test_word_boundary_avoids_substring_false_positive():
    # "java" must not match inside "javascript".
    jd = analyze_jd("Strong JavaScript skills", {"javascript": ["js"]}, {"java", "javascript"})
    assert jd.weight_of("java") == 0.0
    assert jd.weight_of("javascript") > 0


def test_special_char_skill_cpp():
    jd = analyze_jd("Proficient in C++ and C/C++ codebases", ALIASES, {"c++"})
    assert jd.weight_of("c++") > 0


# --------------------------------------------------------------------------- #
# Content scoring
# --------------------------------------------------------------------------- #
def test_bullet_score_sums_matched_tag_weights():
    proj = Project(
        id="p1", name="P",
        bullets=[
            Bullet(id="b-both", text="x", tags=["python", "react"]),
            Bullet(id="b-one", text="y", tags=["react", "kafka"]),
            Bullet(id="b-none", text="z", tags=["kafka"]),
        ],
    )
    content = _content(projects=[proj])
    jd = analyze_jd("Requirements:\nPython and React", ALIASES, collect_content_tags(content))
    res = score_content(content, jd)
    b_both = res.bullets["b-both"]
    b_one = res.bullets["b-one"]
    assert b_both.raw == jd.weight_of("python") + jd.weight_of("react")
    assert b_one.raw == jd.weight_of("react")
    assert res.bullets["b-none"].raw == 0.0
    assert set(b_both.matched_skills) == {"python", "react"}
    # normalization: best bullet is 1.0
    assert b_both.normalized == 1.0
    assert b_one.normalized == b_one.raw / b_both.raw


def test_item_total_includes_item_tags_and_bullets():
    job = Job(
        id="j1", company="C", title="T", tags=["python"],
        bullets=[Bullet(id="jb", text="x", tags=["react"])],
    )
    content = _content(experience=[job])
    jd = analyze_jd("Python and React", ALIASES, collect_content_tags(content))
    res = score_content(content, jd)
    item = res.items["j1"]
    assert item.tag_score == jd.weight_of("python")
    assert item.bullet_total == jd.weight_of("react")
    assert item.total == jd.weight_of("python") + jd.weight_of("react")


# --------------------------------------------------------------------------- #
# Shortlister
# --------------------------------------------------------------------------- #
def test_shortlist_orders_by_score_and_drops_zeros():
    proj = Project(
        id="p1", name="P",
        bullets=[
            Bullet(id="hi", text="x", tags=["python", "react"]),
            Bullet(id="lo", text="y", tags=["react"]),
            Bullet(id="zero", text="z", tags=["kafka"]),
        ],
    )
    content = _content(projects=[proj])
    res = score(content, "Requirements:\nPython and React")
    assert shortlist(res, 5) == ["hi", "lo"]   # "zero" excluded, order by score
    assert shortlist(res, 1) == ["hi"]


def test_shortlist_deterministic_tie_break_by_id():
    proj = Project(
        id="p1", name="P",
        bullets=[
            Bullet(id="b-zeta", text="x", tags=["react"]),
            Bullet(id="b-alpha", text="y", tags=["react"]),
        ],
    )
    content = _content(projects=[proj])
    res = score(content, "React")
    # Equal scores -> sorted by id ascending.
    assert shortlist(res, 5) == ["b-alpha", "b-zeta"]


def test_empty_jd_scores_nothing():
    proj = Project(id="p1", name="P", bullets=[Bullet(id="b", text="x", tags=["python"])])
    content = _content(projects=[proj])
    res = score(content, "")
    assert res.bullets["b"].raw == 0.0
    assert shortlist(res, 5) == []


# --------------------------------------------------------------------------- #
# Coursework scoring
# --------------------------------------------------------------------------- #
def test_score_coursework_ranks_relevant_first_keeps_all():
    profile = Profile(name="T", coursework=[
        Course(name="Operating Systems", tags=["operating systems"]),
        Course(name="Machine Learning", tags=["machine learning", "ml"]),
        Course(name="Art History", tags=["art history"]),
    ])
    content = Content(profile=profile, aliases=ALIASES)
    # collect_content_tags now includes coursework tags, so the JD is searched
    # for "machine learning"; otherwise a course could never score.
    jd = analyze_jd("Requirements:\nMachine Learning", ALIASES, collect_content_tags(content))
    ranked = score_coursework(content, jd)
    names = [n for n, _ in ranked]
    assert names[0] == "Machine Learning" and ranked[0][1] > 0   # relevant first
    assert set(names) == {"Operating Systems", "Machine Learning", "Art History"}  # none dropped

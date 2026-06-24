"""Unit tests for the greedy packer core, with a stub height function (no
compile). Experience is always shown in full; the budget governs PROJECTS only.
Covers budget, pins, excludes, per-item caps/floors, the min-open floor,
max_projects, the breadth tie-break, coursework selection, and determinism.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.models import (  # noqa: E402
    Bullet, Content, Course, Job, NonTechExperience, Profile, Project,
)
from backend.packer import (  # noqa: E402
    PackConfig, pack, selection_to_context, _trim_lowest,
    select_coursework, _coursework_line_count, _shrink_coursework_to_lines, Selection,
)


@dataclass
class StubHeights:
    bullets: dict[str, float] = field(default_factory=dict)
    opens: dict[str, float] = field(default_factory=dict)
    default_bullet: float = 1.0
    default_open: float = 1.0

    def bullet(self, bid: str) -> float:
        return self.bullets.get(bid, self.default_bullet)

    def open_project(self, pid: str) -> float:
        return self.opens.get(pid, self.default_open)


def _b(bid: str, tier: str = "optional") -> Bullet:
    return Bullet(id=bid, text=f"text for {bid}", tier=tier)


def _content(jobs=(), projects=(), profile: Profile | None = None) -> Content:
    return Content(profile=profile or Profile(name="T"),
                   experience=list(jobs), projects=list(projects))


def _job(jid: str, *bids: str) -> Job:
    return Job(id=jid, company="C", title="T", fixed_bullet="fixed", bullets=[_b(b) for b in bids])


def _proj(pid: str, *bids: str, min_bullets=None, max_bullets=None) -> Project:
    return Project(id=pid, name=pid, fixed_bullet="fixed", bullets=[_b(b) for b in bids],
                   min_bullets=min_bullets, max_bullets=max_bullets)


# Default test config disables the min_projects floor so the budget/cap/fill
# tests below exercise pure packing mechanics; the floor has its own tests.
CFG = PackConfig(closeness_threshold=0.10, min_bullets_per_open_project=2,
                 max_bullets_per_item=4, min_projects=0, max_projects=4)


# --------------------------------------------------------------------------- #
# Experience is always shown in full
# --------------------------------------------------------------------------- #
def test_experience_always_shown_regardless_of_budget():
    content = _content(jobs=[_job("j", "b1", "b2", "b3")])
    sel = pack(content, {"b1": 0.0, "b2": 0.0, "b3": 0.0}, StubHeights(), CFG, budget=0.0)
    assert sel.exp_bullets["j"] == ["b1", "b2", "b3"]   # every authored bullet, zero budget


def test_experience_not_charged_to_budget_so_projects_still_pack():
    content = _content(jobs=[_job("j", "b1", "b2")],
                       projects=[_proj("p", "pb1", "pb2")])
    scores = {"b1": 9.0, "b2": 9.0, "pb1": 1.0, "pb2": 1.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=3.0)  # open(1) + 2 bullets
    assert sel.exp_bullets["j"] == ["b1", "b2"]   # experience free
    assert sel.open_projects == ["p"]             # budget spent on the project


# --------------------------------------------------------------------------- #
# Project score maximization + budget
# --------------------------------------------------------------------------- #
def test_project_picks_highest_scores_within_budget():
    content = _content(projects=[_proj("p", "pb1", "pb2", "pb3")])
    scores = {"pb1": 5.0, "pb2": 3.0, "pb3": 1.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=3.0)  # open(1) + 2 bullets
    assert sel.proj_bullets["p"] == ["pb1", "pb2"]   # pb3 dropped at the floor


def test_budget_zero_opens_no_projects():
    content = _content(projects=[_proj("p", "pb1", "pb2")])
    sel = pack(content, {"pb1": 5.0, "pb2": 5.0}, StubHeights(), CFG, budget=0.0)
    assert sel.open_projects == []


# --------------------------------------------------------------------------- #
# min-open floor + max_projects
# --------------------------------------------------------------------------- #
def test_project_with_too_few_bullets_never_opens():
    content = _content(projects=[_proj("p", "pb1")])  # only 1 bullet, default min is 2
    sel = pack(content, {"pb1": 9.0}, StubHeights(), CFG, budget=100.0)
    assert sel.open_projects == []


def test_opened_project_meets_min():
    content = _content(projects=[_proj("p", "pb1", "pb2", "pb3")])
    scores = {"pb1": 3.0, "pb2": 2.0, "pb3": 1.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=3.0)
    assert sel.open_projects == ["p"]
    assert sel.proj_bullets["p"] == ["pb1", "pb2"]   # best two = the floor


def test_max_projects_caps_opened_projects():
    cfg = PackConfig(closeness_threshold=0.1, min_bullets_per_open_project=2,
                     max_bullets_per_item=4, max_projects=2)
    content = _content(projects=[_proj("p1", "a1", "a2"), _proj("p2", "b1", "b2"),
                                 _proj("p3", "c1", "c2")])
    scores = {k: 1.0 for k in ["a1", "a2", "b1", "b2", "c1", "c2"]}
    sel = pack(content, scores, StubHeights(), cfg, budget=100.0)
    assert len(sel.open_projects) == 2   # third project blocked by the cap


CFG_FLOOR = PackConfig(closeness_threshold=0.10, min_bullets_per_open_project=2,
                       max_bullets_per_item=4, min_projects=2, max_projects=4)


def test_min_projects_floor_forces_projects_even_with_zero_budget_and_score():
    content = _content(projects=[_proj("p1", "a1", "a2"), _proj("p2", "b1", "b2"),
                                 _proj("p3", "c1", "c2")])
    scores = {k: 0.0 for k in ["a1", "a2", "b1", "b2", "c1", "c2"]}
    sel = pack(content, scores, StubHeights(), CFG_FLOOR, budget=0.0)
    assert len(sel.open_projects) == 2   # floor forces two open despite no budget


def test_min_projects_floor_picks_highest_scoring_remaining():
    content = _content(projects=[_proj("p1", "a1", "a2"), _proj("p2", "b1", "b2"),
                                 _proj("p3", "c1", "c2")])
    scores = {"a1": 0, "a2": 0, "b1": 1, "b2": 1, "c1": 2, "c2": 2}  # p3 > p2 > p1
    sel = pack(content, scores, StubHeights(), CFG_FLOOR, budget=0.0)
    assert set(sel.open_projects) == {"p3", "p2"}   # the two most relevant forced in


def test_min_projects_floor_capped_by_available_projects():
    content = _content(projects=[_proj("only", "x1", "x2")])
    sel = pack(content, {"x1": 0, "x2": 0}, StubHeights(), CFG_FLOOR, budget=0.0)
    assert sel.open_projects == ["only"]   # can't exceed what exists


def test_trim_lowest_keeps_min_projects_but_trims_bullets():
    projects = {"p1": _proj("p1", "a1", "a2", "a3"), "p2": _proj("p2", "b1", "b2", "b3")}
    sel = Selection(open_projects=["p1", "p2"],
                    proj_bullets={"p1": ["a1", "a2", "a3"], "p2": ["b1", "b2", "b3"]})
    scores = {k: 1.0 for k in ["a1", "a2", "a3", "b1", "b2", "b3"]}
    # 2 open == min 2: no project may close; a bullet above the floor is trimmed.
    assert _trim_lowest(sel, scores, StubHeights(), projects, CFG_FLOOR) is True
    assert len(sel.open_projects) == 2
    assert sum(len(v) for v in sel.proj_bullets.values()) == 5   # one bullet gone, none closed


# --------------------------------------------------------------------------- #
# per-item min/max overrides (TOTAL incl. fixed -> secondary = n-1)
# --------------------------------------------------------------------------- #
def test_per_project_max_bullets_override_caps_secondary():
    # max_bullets=2 (total) -> 1 secondary; min_bullets=2 -> floor 1 secondary.
    content = _content(projects=[_proj("p", "pb1", "pb2", "pb3", min_bullets=2, max_bullets=2)])
    scores = {"pb1": 3.0, "pb2": 2.0, "pb3": 1.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=100.0)
    assert sel.proj_bullets["p"] == ["pb1"]   # 1 secondary only


def test_per_project_min_bullets_override_floor():
    # min_bullets=3 (total) -> 2 secondary floor; needs >=2 bullets to open.
    content = _content(projects=[_proj("p", "pb1", "pb2", "pb3", min_bullets=3, max_bullets=4)])
    scores = {"pb1": 1.0, "pb2": 1.0, "pb3": 1.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=3.0)  # open(1)+2
    assert sel.proj_bullets["p"] == ["pb1", "pb2"]


# --------------------------------------------------------------------------- #
# excludes
# --------------------------------------------------------------------------- #
def test_excludes_bullet_and_project():
    content = _content(projects=[_proj("p1", "pb1", "pb2", "pb3"), _proj("p2", "qb1", "qb2")])
    scores = {k: 5.0 for k in ["pb1", "pb2", "pb3", "qb1", "qb2"]}
    sel = pack(content, scores, StubHeights(), CFG, budget=100.0, excludes=["pb1", "p2"])
    assert "pb1" not in sel.all_bullets()        # excluded bullet gone
    assert "p2" not in sel.open_projects          # excluded project never opens
    assert "p1" in sel.open_projects              # still has 2 bullets -> meets floor


# --------------------------------------------------------------------------- #
# pins
# --------------------------------------------------------------------------- #
def test_pin_project_forces_open_waiving_min():
    content = _content(projects=[_proj("p", "pb1")])  # only 1 bullet, < min
    sel = pack(content, {"pb1": 0.0}, StubHeights(), CFG, budget=100.0, pins=["p"])
    assert sel.open_projects == ["p"]      # opened despite failing min-open
    assert "p" in sel.pinned_projects


def test_pin_bullet_in_closed_project_opens_it():
    content = _content(projects=[_proj("p", "pb1", "pb2")])
    sel = pack(content, {"pb1": 0.0, "pb2": 0.0}, StubHeights(), CFG, budget=0.0, pins=["pb2"])
    assert sel.open_projects == ["p"]
    assert sel.proj_bullets["p"] == ["pb2"]


def test_pin_overrides_max_cap():
    cfg = PackConfig(closeness_threshold=0.1, min_bullets_per_open_project=1, max_bullets_per_item=1)
    content = _content(projects=[_proj("p", "pb1", "pb2", "pb3")])
    sel = pack(content, {"pb1": 1, "pb2": 1, "pb3": 1}, StubHeights(), cfg, budget=0.0,
               pins=["pb1", "pb2", "pb3"])
    assert set(sel.proj_bullets["p"]) == {"pb1", "pb2", "pb3"}   # all pinned past the cap


def test_exclude_vetoes_pin():
    content = _content(projects=[_proj("p", "pb1", "pb2")])
    sel = pack(content, {"pb1": 5.0, "pb2": 5.0}, StubHeights(), CFG, budget=100.0,
               pins=["pb1"], excludes=["pb1"])
    assert "pb1" not in sel.all_bullets()   # exclude wins the contradiction


# --------------------------------------------------------------------------- #
# page filling (fill_page)
# --------------------------------------------------------------------------- #
def test_fill_page_off_drops_irrelevant_projects():
    cfg = PackConfig(closeness_threshold=0.10, min_bullets_per_open_project=2,
                     max_bullets_per_item=4, min_projects=0, max_projects=4, fill_page=False)
    content = _content(projects=[_proj("p", "pb1", "pb2", "pb3")])
    scores = {"pb1": 0.0, "pb2": 0.0, "pb3": 0.0}
    sel = pack(content, scores, StubHeights(), cfg, budget=100.0)
    assert sel.open_projects == []   # nothing relevant + no floor -> nothing packed


def test_fill_page_opens_irrelevant_project_as_filler():
    content = _content(projects=[_proj("p", "pb1", "pb2")])
    scores = {"pb1": 0.0, "pb2": 0.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=100.0)  # fill_page default
    assert sel.open_projects == ["p"]   # filler keeps the page from being empty


# --------------------------------------------------------------------------- #
# breadth tie-break
# --------------------------------------------------------------------------- #
def test_breadth_prefers_opening_new_project_on_tie():
    content = _content(projects=[_proj("p1", "pb1", "pb2", "pb3"),
                                 _proj("p2", "qb1", "qb2", "qb3")])
    scores = {k: 0.5 for k in ["pb1", "pb2", "pb3", "qb1", "qb2", "qb3"]}
    heights = StubHeights(bullets={k: 0.5 for k in scores}, opens={"p1": 0.0, "p2": 0.0})
    sel = pack(content, scores, heights, CFG, budget=2.0)
    # open p1 (floor 2 = 1.0 used); then add-pb3 ties open-p2 -> breadth opens p2.
    assert "p2" in sel.open_projects
    assert "pb3" not in sel.proj_bullets["p1"]


# --------------------------------------------------------------------------- #
# coursework selection
# --------------------------------------------------------------------------- #
def _settings(line_chars=40, max_lines=2):
    return SimpleNamespace(coursework_line_chars=line_chars, coursework_max_lines=max_lines)


def test_coursework_line_count_wraps_by_width():
    assert _coursework_line_count([], 40) == 0
    assert _coursework_line_count(["Short"], 40) == 1
    # label (~21) + two long names forces a second line.
    assert _coursework_line_count(["Operating Systems", "Computer Networking"], 40) == 2


def test_select_coursework_relevant_first_up_to_max_lines():
    ranked = [("ML", 3.0), ("Security", 2.0), ("Networking", 0.0)]
    chosen = select_coursework(ranked, _settings(line_chars=80, max_lines=2))
    assert chosen == ["ML", "Security"]   # only relevant (score>0), score order


def test_select_coursework_no_signal_shows_single_default_line():
    ranked = [("Aaa", 0.0), ("Bbb", 0.0), ("Ccc", 0.0)]
    chosen = select_coursework(ranked, _settings(line_chars=80, max_lines=2))
    assert chosen and _coursework_line_count(chosen, 80) == 1   # 1 line, never empty


def test_shrink_coursework_to_one_line():
    sel = Selection(coursework=["Operating Systems", "Computer Networking", "Machine Learning"])
    assert _shrink_coursework_to_lines(sel, 40, target_lines=1) is True
    assert _coursework_line_count(sel.coursework, 40) <= 1


# --------------------------------------------------------------------------- #
# determinism + context mapping
# --------------------------------------------------------------------------- #
def test_deterministic():
    content = _content(
        jobs=[_job("j", "b1", "b2")],
        projects=[_proj("p1", "x1", "x2"), _proj("p2", "y1", "y2")],
    )
    scores = {k: 1.0 for k in ["b1", "b2", "x1", "x2", "y1", "y2"]}
    a = pack(content, scores, StubHeights(), CFG, budget=5.0)
    b = pack(content, scores, StubHeights(), CFG, budget=5.0)
    assert a.exp_bullets == b.exp_bullets
    assert a.open_projects == b.open_projects
    assert a.proj_bullets == b.proj_bullets


def test_context_keeps_fixed_first_and_authored_order_and_new_sections():
    profile = Profile(
        name="T",
        coursework=[Course(name="ML"), Course(name="Security")],
        awards=["Dean's List"],
        nontechnical=[NonTechExperience(role="Treasurer", organization="SAM")],
    )
    content = _content(
        jobs=[_job("j", "b1", "b2")],
        projects=[_proj("p", "pb1", "pb2")],
        profile=profile,
    )
    scores = {"b1": 1, "b2": 2, "pb1": 1, "pb2": 2}
    sel = pack(content, scores, StubHeights(), CFG, budget=100.0)
    sel.coursework = ["Security", "ML"]   # chosen order (not authored order)
    ctx = selection_to_context(content, sel)

    job = ctx["experience"][0]
    assert job["bullets"][0] == "fixed"                          # fixed first
    assert job["bullets"][1:] == ["text for b1", "text for b2"]  # authored order
    proj = ctx["projects"][0]
    assert proj["bullets"][0] == "fixed"
    assert proj["bullets"][1:] == ["text for pb1", "text for pb2"]
    assert ctx["coursework"] == ["Security", "ML"]               # chosen order preserved
    assert ctx["awards"] == ["Dean's List"]
    assert ctx["nontechnical"][0]["role"] == "Treasurer"

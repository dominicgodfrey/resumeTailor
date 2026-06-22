"""Unit tests for the greedy packer core, with a stub height function (no
compile). Covers budget, pins, excludes, caps, the min-open floor, the breadth
tie-break, score maximization, and determinism.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.models import Bullet, Content, Job, Profile, Project  # noqa: E402
from backend.packer import PackConfig, pack, selection_to_context  # noqa: E402


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


def _content(jobs=(), projects=()) -> Content:
    return Content(profile=Profile(name="T"), experience=list(jobs), projects=list(projects))


def _job(jid: str, *bids: str) -> Job:
    return Job(id=jid, company="C", title="T", fixed_bullet="fixed", bullets=[_b(b) for b in bids])


def _proj(pid: str, *bids: str) -> Project:
    return Project(id=pid, name=pid, fixed_bullet="fixed", bullets=[_b(b) for b in bids])


CFG = PackConfig(closeness_threshold=0.10, min_bullets_per_open_project=2, max_bullets_per_item=4)


# --------------------------------------------------------------------------- #
# Score maximization + budget
# --------------------------------------------------------------------------- #
def test_picks_highest_scores_within_budget():
    content = _content(jobs=[_job("j", "b1", "b2", "b3")])
    scores = {"b1": 5.0, "b2": 3.0, "b3": 1.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=2.0)
    assert sel.exp_bullets["j"] == ["b1", "b2"]   # b3 dropped, budget = 2 bullets
    assert sel.total_score(scores) == 8.0


def test_budget_zero_selects_nothing():
    content = _content(jobs=[_job("j", "b1")])
    sel = pack(content, {"b1": 5.0}, StubHeights(), CFG, budget=0.0)
    assert sel.all_bullets() == []


# --------------------------------------------------------------------------- #
# min-open floor
# --------------------------------------------------------------------------- #
def test_project_with_too_few_bullets_never_opens():
    content = _content(projects=[_proj("p", "pb1")])  # only 1 bullet, min is 2
    sel = pack(content, {"pb1": 9.0}, StubHeights(), CFG, budget=100.0)
    assert sel.open_projects == []


def test_opened_project_meets_min():
    content = _content(projects=[_proj("p", "pb1", "pb2", "pb3")])
    scores = {"pb1": 3.0, "pb2": 2.0, "pb3": 1.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=3.0)  # open(1)+2 bullets
    assert sel.open_projects == ["p"]
    assert len(sel.proj_bullets["p"]) >= CFG.min_bullets_per_open_project
    assert sel.proj_bullets["p"] == ["pb1", "pb2"]  # best two


# --------------------------------------------------------------------------- #
# caps
# --------------------------------------------------------------------------- #
def test_max_bullets_per_item_caps_secondary():
    cfg = PackConfig(closeness_threshold=0.1, min_bullets_per_open_project=2, max_bullets_per_item=2)
    content = _content(projects=[_proj("p", "pb1", "pb2", "pb3")])
    scores = {"pb1": 3.0, "pb2": 2.0, "pb3": 1.0}
    sel = pack(content, scores, StubHeights(), cfg, budget=100.0)
    assert len(sel.proj_bullets["p"]) == 2


# --------------------------------------------------------------------------- #
# excludes
# --------------------------------------------------------------------------- #
def test_excludes_bullet_and_project():
    content = _content(
        jobs=[_job("j", "b1", "b2")],
        projects=[_proj("p1", "pb1", "pb2"), _proj("p2", "qb1", "qb2")],
    )
    scores = {k: 5.0 for k in ["b1", "b2", "pb1", "pb2", "qb1", "qb2"]}
    sel = pack(content, scores, StubHeights(), CFG, budget=100.0,
               excludes=["b1", "p2"])
    assert "b1" not in sel.all_bullets()        # excluded bullet gone
    assert "b2" in sel.exp_bullets["j"]
    assert "p2" not in sel.open_projects         # excluded project never opens
    assert "p1" in sel.open_projects


# --------------------------------------------------------------------------- #
# pins
# --------------------------------------------------------------------------- #
def test_pin_bullet_forces_inclusion_even_over_budget():
    content = _content(jobs=[_job("j", "b1", "b2")])
    scores = {"b1": 0.0, "b2": 9.0}    # b1 worthless by score
    sel = pack(content, scores, StubHeights(), CFG, budget=0.0, pins=["b1"])
    assert "b1" in sel.exp_bullets["j"]   # pinned despite zero budget/score


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
    content = _content(jobs=[_job("j", "b1", "b2", "b3")])
    sel = pack(content, {"b1": 1, "b2": 1, "b3": 1}, StubHeights(), cfg, budget=0.0,
               pins=["b1", "b2", "b3"])
    assert set(sel.exp_bullets["j"]) == {"b1", "b2", "b3"}   # all pinned past the cap


def test_exclude_vetoes_pin():
    content = _content(jobs=[_job("j", "b1")])
    sel = pack(content, {"b1": 5.0}, StubHeights(), CFG, budget=100.0,
               pins=["b1"], excludes=["b1"])
    assert "b1" not in sel.all_bullets()   # exclude wins the contradiction


# --------------------------------------------------------------------------- #
# breadth tie-break
# --------------------------------------------------------------------------- #
def test_breadth_prefers_opening_new_project_on_tie():
    # b1 density 1.0; opening p (two 0.5-score, 0.5-height bullets, 0 open cost)
    # also density 1.0 -> within threshold -> breadth wins.
    content = _content(jobs=[_job("j", "b1")], projects=[_proj("p", "pb1", "pb2")])
    scores = {"b1": 1.0, "pb1": 0.5, "pb2": 0.5}
    heights = StubHeights(bullets={"pb1": 0.5, "pb2": 0.5}, opens={"p": 0.0})
    sel = pack(content, scores, heights, CFG, budget=1.0)
    assert sel.open_projects == ["p"]          # breadth chosen
    assert "b1" not in sel.exp_bullets.get("j", [])


def test_breadth_prefers_least_developed_item_on_tie():
    # Pin b1 (develops j1 to dev=1). Then j1.next and j2.next tie on density;
    # the least-developed job (j2, dev=0) should win the one remaining slot.
    content = _content(jobs=[_job("j1", "b1", "b2"), _job("j2", "c1")])
    scores = {"b1": 1.0, "b2": 1.0, "c1": 1.0}
    sel = pack(content, scores, StubHeights(), CFG, budget=2.0, pins=["b1"])
    assert "c1" in sel.exp_bullets.get("j2", [])
    assert "b2" not in sel.exp_bullets.get("j1", [])


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


def test_context_keeps_fixed_first_and_authored_order():
    content = _content(
        jobs=[_job("j", "b1", "b2")],
        projects=[_proj("p", "pb1", "pb2")],
    )
    scores = {"b1": 1, "b2": 2, "pb1": 1, "pb2": 2}
    sel = pack(content, scores, StubHeights(), CFG, budget=100.0)
    ctx = selection_to_context(content, sel)
    job = ctx["experience"][0]
    assert job["bullets"][0] == "fixed"                       # fixed first
    assert job["bullets"][1:] == ["text for b1", "text for b2"]  # authored order, not score order
    proj = ctx["projects"][0]
    assert proj["bullets"][0] == "fixed"
    assert proj["bullets"][1:] == ["text for pb1", "text for pb2"]

"""Auto-packer: choose which projects to open and which secondary bullets to
show so the page is full but never overflows.

Locked content (header, education, awards, the static skills block, and every
experience job with its fixed first bullet) is always present and is *not* part
of the budget — the driver bakes it into a baseline compile and hands this module
the leftover page space. The packer then spends that budget on **add-on units**:

  * an experience secondary bullet,
  * opening a project (its heading + fixed bullet + the first
    ``min_bullets_per_open_project`` secondary bullets, as one bundle), or
  * an additional secondary bullet for an already-open project.

It is greedy by **score density** (added score / added height). When candidate
densities are within ``closeness_threshold`` of the best, it prefers **breadth**:
open a new project first, otherwise feed the least-developed item. Pins force
content in (waiving the min-open rule), excludes veto it, and
``max_bullets_per_item`` caps each item.

The core ``pack`` is pure and deterministic with injected heights + budget, so it
unit-tests with a stub height function. ``pack_and_verify`` wires it to a real
Tectonic compile and trims on overflow. The packer only ever sees one number per
unit; it knows nothing about how that number was scored.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol

from backend.models import Content, Job, Project
from backend.render import FitResult, compile_and_measure

# Rough geometry for *estimating* heights to order the greedy; the real fit is
# always confirmed by compile.
CHARS_PER_LINE = 90.0
LINE_PT = 13.6           # matches render.PT_PER_LINE
PROJECT_HEADING_LINES = 1.0
ITEM_SPACING_LINES = 0.25
SAFETY_LINES = 1.0       # keep a hair of slack so we don't ride the page edge
MIN_MOVE_SCORE = 1e-9    # don't auto-pack content with no JD relevance (pin to force)


# --------------------------------------------------------------------------- #
# Height model
# --------------------------------------------------------------------------- #
class Heights(Protocol):
    def bullet(self, bullet_id: str) -> float: ...
    def open_project(self, project_id: str) -> float: ...


_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?|[{}]")


def _visible_len(latex: str) -> int:
    """Approximate on-page character count, stripping LaTeX commands/braces."""
    return len(_CMD_RE.sub("", latex))


def estimate_bullet_lines(text: str) -> float:
    return max(1.0, math.ceil(_visible_len(text) / CHARS_PER_LINE)) + ITEM_SPACING_LINES


@dataclass
class EstimatedHeights:
    """Estimate add-on heights (in text lines) from bullet text length."""

    content: Content

    def __post_init__(self) -> None:
        self._text: dict[str, str] = {}
        self._fixed: dict[str, str | None] = {}
        for item in (*self.content.experience, *self.content.projects):
            self._fixed[item.id] = item.fixed_bullet
            for b in item.bullets:
                self._text[b.id] = b.text

    def bullet(self, bullet_id: str) -> float:
        return estimate_bullet_lines(self._text.get(bullet_id, ""))

    def open_project(self, project_id: str) -> float:
        fixed = self._fixed.get(project_id) or ""
        return PROJECT_HEADING_LINES + estimate_bullet_lines(fixed)


# --------------------------------------------------------------------------- #
# Selection model
# --------------------------------------------------------------------------- #
@dataclass
class Selection:
    exp_bullets: dict[str, list[str]] = field(default_factory=dict)   # job_id -> bullet ids
    open_projects: list[str] = field(default_factory=list)            # in open order
    proj_bullets: dict[str, list[str]] = field(default_factory=dict)  # project_id -> bullet ids
    coursework: list[str] = field(default_factory=list)              # chosen course names, in order
    pinned_bullets: set[str] = field(default_factory=set)
    pinned_projects: set[str] = field(default_factory=set)            # forced-open, min waived

    def all_bullets(self) -> list[str]:
        out: list[str] = []
        for ids in self.exp_bullets.values():
            out.extend(ids)
        for pid in self.open_projects:
            out.extend(self.proj_bullets.get(pid, []))
        return out

    def total_score(self, scores: dict[str, float]) -> float:
        return sum(scores.get(b, 0.0) for b in self.all_bullets())


@dataclass
class Move:
    kind: str                 # "exp_bullet" | "proj_bullet" | "proj_open"
    item_id: str
    bullet_ids: list[str]
    score: float
    height: float
    dev: int                  # current development of the target item (0 for a new project)

    @property
    def density(self) -> float:
        return self.score / self.height if self.height > 0 else math.inf


# --------------------------------------------------------------------------- #
# Core greedy packer (pure / testable)
# --------------------------------------------------------------------------- #
@dataclass
class PackConfig:
    closeness_threshold: float = 0.10
    min_bullets_per_open_project: int = 2   # default secondary floor for a project
    max_bullets_per_item: int = 4           # default secondary cap for a project
    min_projects: int = 2                   # always show at least this many projects
    max_projects: int = 4                   # most projects an auto-pack will open
    fill_page: bool = True   # spend leftover budget on the best-remaining bullets
                             # even with no JD relevance (vs. dropping them)


def _secondary_floor(p: Project, config: PackConfig) -> int:
    """Minimum *secondary* bullets a shown project must keep. Per-item
    ``min_bullets`` is a TOTAL (incl. the fixed bullet), so subtract 1."""
    return (p.min_bullets - 1) if p.min_bullets is not None else config.min_bullets_per_open_project


def _secondary_cap(p: Project, config: PackConfig) -> int:
    """Maximum *secondary* bullets a project may show. Per-item ``max_bullets``
    is a TOTAL (incl. the fixed bullet), so subtract 1."""
    return (p.max_bullets - 1) if p.max_bullets is not None else config.max_bullets_per_item


def _ordered_pool(bullets, scores: dict[str, float], excludes: set[str]) -> list[str]:
    ids = [b.id for b in bullets if b.id not in excludes]
    return sorted(ids, key=lambda bid: (-scores.get(bid, 0.0), bid))


def pack(
    content: Content,
    scores: dict[str, float],
    heights: Heights,
    config: PackConfig,
    budget: float,
    pins: Iterable[str] = (),
    excludes: Iterable[str] = (),
) -> Selection:
    """Greedily fill ``budget`` (in the same unit as ``heights``) with the
    highest-density add-on units, honoring pins/excludes/caps and the breadth
    tie-break. Deterministic for a given input."""
    pins = set(pins)
    excludes = set(excludes)

    jobs: dict[str, Job] = {j.id: j for j in content.experience}
    projects: dict[str, Project] = {p.id: p for p in content.projects}
    proj_pool = {pid: _ordered_pool(p.bullets, scores, excludes) for pid, p in projects.items()}

    owner_of: dict[str, str] = {}  # project bullet_id -> project_id
    for pid, p in projects.items():
        for b in p.bullets:
            owner_of[b.id] = pid

    sel = Selection()
    used = 0.0

    # 0) Experience is always shown in full (every authored bullet, authored
    #    order). It lives in the locked baseline, so it costs nothing here — we
    #    only record it so the rendered context carries it.
    for job in content.experience:
        sel.exp_bullets[job.id] = [b.id for b in job.bullets]

    def open_project(pid: str, waived: bool) -> None:
        if pid in sel.open_projects:
            return
        sel.open_projects.append(pid)
        sel.proj_bullets.setdefault(pid, [])
        if waived:
            sel.pinned_projects.add(pid)
        nonlocal used
        used += heights.open_project(pid)

    def add_proj_bullet(bid: str, pinned: bool) -> None:
        nonlocal used
        pid = owner_of[bid]
        if pid not in sel.open_projects:
            open_project(pid, waived=pinned)
        lst = sel.proj_bullets.setdefault(pid, [])
        if bid in lst:
            return
        lst.append(bid)
        if pinned:
            sel.pinned_bullets.add(bid)
        used += heights.bullet(bid)

    # 1) Pins: force-open pinned projects, then force-add pinned project bullets.
    #    Excludes are a hard veto even over a pin (contradictory input -> safe
    #    default). Pinned experience bullets are moot — experience is always full.
    for pid in [p for p in projects if p in pins and p not in excludes]:
        open_project(pid, waived=True)
    for bid in sorted(b for b in pins if b in owner_of and b not in excludes):
        add_proj_bullet(bid, pinned=True)

    # 2) Greedy fill — projects only (experience never competes for budget).
    def chosen_count(pid: str) -> int:
        return len(sel.proj_bullets.get(pid, []))

    def generate() -> list[Move]:
        moves: list[Move] = []
        # next-best bullet for each open project (respect its secondary cap)
        for pid in sel.open_projects:
            if chosen_count(pid) >= _secondary_cap(projects[pid], config):
                continue
            chosen = set(sel.proj_bullets.get(pid, []))
            nxt = next((b for b in proj_pool.get(pid, []) if b not in chosen), None)
            if nxt is None:
                continue
            moves.append(Move("proj_bullet", pid, [nxt], scores.get(nxt, 0.0),
                              heights.bullet(nxt), dev=len(chosen)))
        # open a new eligible project (bundle of its secondary floor), capped by
        # max_projects so an auto-pack stays within the intended 3-4 projects.
        if len(sel.open_projects) < config.max_projects:
            for pid, p in projects.items():
                if pid in sel.open_projects or pid in excludes:
                    continue
                pool = proj_pool.get(pid, [])
                need = _secondary_floor(p, config)
                if len(pool) < need:
                    continue  # can't satisfy min-open (pinned opens were handled above)
                bundle = pool[:need]
                score = sum(scores.get(b, 0.0) for b in bundle)
                height = heights.open_project(pid) + sum(heights.bullet(b) for b in bundle)
                moves.append(Move("proj_open", pid, bundle, score, height, dev=0))
        return moves

    while True:
        # A move must fit the remaining budget. When ``fill_page`` is off we also
        # require some JD score, so irrelevant content is dropped (pin to force it
        # in) rather than used as filler. When on (the default), zero-score moves
        # are eligible too — but greedy-by-density still spends the relevant,
        # higher-density content first and only falls back to filler once it runs
        # out, so the page fills top-down by relevance.
        fitting = [m for m in generate()
                   if used + m.height <= budget + 1e-9
                   and (config.fill_page or m.score > MIN_MOVE_SCORE)]
        if not fitting:
            break
        pick = _choose(fitting, config.closeness_threshold)
        if pick.kind == "proj_open":
            open_project(pick.item_id, waived=False)
            for b in pick.bullet_ids:
                add_proj_bullet(b, pinned=False)
        else:
            add_proj_bullet(pick.bullet_ids[0], pinned=False)

    # 3) Floor: always show at least ``min_projects``. Force-open the best
    #    remaining eligible projects (those that can meet their own bullet floor),
    #    ranked by floor-bundle score, ignoring budget — the verify/trim loop
    #    enforces one-page fit without dropping below this floor.
    if len(sel.open_projects) < config.min_projects:
        def floor_bundle(pid: str, p: Project) -> list[str]:
            return proj_pool.get(pid, [])[:_secondary_floor(p, config)]

        eligible = [
            (pid, p) for pid, p in projects.items()
            if pid not in sel.open_projects and pid not in excludes
            and len(proj_pool.get(pid, [])) >= _secondary_floor(p, config)
        ]
        eligible.sort(key=lambda t: (
            -sum(scores.get(b, 0.0) for b in floor_bundle(*t)), t[0]))
        for pid, p in eligible:
            if len(sel.open_projects) >= config.min_projects:
                break
            open_project(pid, waived=False)
            for b in floor_bundle(pid, p):
                add_proj_bullet(b, pinned=False)

    return sel


def _choose(moves: list[Move], threshold: float) -> Move:
    """Pick the densest move; among near-ties (within ``threshold`` of the best
    density) prefer opening a new project, then the least-developed item."""
    top = max(m.density for m in moves)
    near = [m for m in moves if m.density >= top * (1.0 - threshold)]
    opens = [m for m in near if m.kind == "proj_open"]
    pool = opens if opens else near
    if not opens:
        min_dev = min(m.dev for m in pool)
        pool = [m for m in pool if m.dev == min_dev]
    pool.sort(key=lambda m: (-m.density, m.item_id, m.bullet_ids[0]))
    return pool[0]


# --------------------------------------------------------------------------- #
# Render-context mapping
# --------------------------------------------------------------------------- #
def selection_to_context(content: Content, sel: Selection) -> dict:
    """Turn a Selection into the Jinja context the template consumes. Bullet
    text is copied verbatim — fixed bullet first, then chosen secondary bullets
    in their original authored order."""
    profile = content.profile
    text_by_id = {b.id: b.text for item in (*content.experience, *content.projects)
                  for b in item.bullets}
    order_by_item = {item.id: [b.id for b in item.bullets]
                     for item in (*content.experience, *content.projects)}

    def ordered(item_id: str, chosen: list[str]) -> list[str]:
        chosen_set = set(chosen)
        return [bid for bid in order_by_item[item_id] if bid in chosen_set]

    experience = []
    for job in content.experience:  # all jobs locked, original order
        bullets = []
        if job.fixed_bullet:
            bullets.append(job.fixed_bullet)
        bullets += [text_by_id[b] for b in ordered(job.id, sel.exp_bullets.get(job.id, []))]
        experience.append({
            "title": job.title, "company": job.company, "tech": job.tech,
            "location": job.location, "dates": job.dates, "bullets": bullets,
        })

    projects = []
    proj_by_id = {p.id: p for p in content.projects}
    for pid in sel.open_projects:  # only opened projects, in open order
        p = proj_by_id[pid]
        bullets = []
        if p.fixed_bullet:
            bullets.append(p.fixed_bullet)
        bullets += [text_by_id[b] for b in ordered(pid, sel.proj_bullets.get(pid, []))]
        projects.append({
            "name": p.name, "tech": p.tech, "dates": p.dates, "bullets": bullets,
        })

    # Relevant-coursework line(s): names copied verbatim, ordered as chosen.
    name_set = {c.name for c in profile.coursework}
    coursework = [n for n in sel.coursework if n in name_set]

    return {
        "name": profile.name,
        "contacts": [c.model_dump() for c in profile.contacts],
        "education": [e.model_dump() for e in profile.education],
        "coursework": coursework,
        "experience": experience,
        "projects": projects,
        "awards": list(profile.awards),
        "skills": [s.model_dump() for s in profile.skills],
        "nontechnical": [n.model_dump() for n in profile.nontechnical],
    }


# --------------------------------------------------------------------------- #
# Compile-verified driver
# --------------------------------------------------------------------------- #
@dataclass
class PackResult:
    selection: Selection
    fit: FitResult
    total_score: float
    compiles: int
    status: str


# --------------------------------------------------------------------------- #
# Relevant-coursework selection (tailored, but always present)
# --------------------------------------------------------------------------- #
_COURSEWORK_LABEL = "Relevant Coursework: "


def _coursework_line_count(names: list[str], line_chars: int) -> int:
    """Greedy estimate of how many lines the coursework line wraps to, counting
    the leading label on line 1 and ``, `` separators between names."""
    if not names:
        return 0
    lines = 1
    cur = len(_COURSEWORK_LABEL)
    for i, name in enumerate(names):
        add = len(name) + (2 if i > 0 else 0)  # ", " separator
        if i > 0 and cur + add > line_chars:
            lines += 1
            cur = len(name)
        else:
            cur += add
    return lines


def select_coursework(ranked: list[tuple[str, float]], settings) -> list[str]:
    """Choose which courses to show, in order. Relevant (score > 0) courses fill
    up to ``coursework_max_lines``; with no JD signal we show a single default
    line so the section is never empty. Names are returned verbatim."""
    if not ranked:
        return []
    relevant = [name for name, score in ranked if score > 0]
    line_chars = settings.coursework_line_chars
    if relevant:
        pool, max_lines = relevant, max(1, settings.coursework_max_lines)
    else:
        pool, max_lines = [name for name, _ in ranked], 1
    chosen: list[str] = []
    for name in pool:
        if _coursework_line_count(chosen + [name], line_chars) > max_lines:
            break
        chosen.append(name)
    if not chosen and pool:
        chosen = [pool[0]]
    return chosen


def _shrink_coursework_to_lines(sel: Selection, line_chars: int, target_lines: int) -> bool:
    """Drop trailing courses until the line fits ``target_lines``. Returns True
    if anything was removed."""
    changed = False
    while sel.coursework and _coursework_line_count(sel.coursework, line_chars) > target_lines:
        sel.coursework.pop()
        changed = True
    return changed


# --------------------------------------------------------------------------- #
# Compile-verified driver
# --------------------------------------------------------------------------- #
def _experience_selection(content: Content) -> Selection:
    """A selection that shows every experience job with all its authored bullets
    — experience is always present, never score-trimmed."""
    sel = Selection()
    for job in content.experience:
        sel.exp_bullets[job.id] = [b.id for b in job.bullets]
    return sel


def _locked_context(content: Content, coursework: Iterable[str] = ()) -> dict:
    sel = _experience_selection(content)
    sel.coursework = list(coursework)
    return selection_to_context(content, sel)


def pack_and_verify(
    content: Content,
    scores: dict[str, float],
    coursework: Iterable[tuple[str, float]] = (),
    pins: Iterable[str] = (),
    excludes: Iterable[str] = (),
    *,
    build_dir: Path | None = None,
    max_compiles: int = 8,
) -> PackResult:
    """Estimate a packing, then confirm it with real compiles. The always-shown
    baseline is header + education + a tailored coursework line + all experience;
    the packer spends the leftover page on projects. On overflow we trim (drop the
    2nd coursework line, then the lowest-density project add-on, then coursework
    tail). Bounded by ``max_compiles``."""
    settings = content.profile.settings
    config = PackConfig(
        closeness_threshold=settings.closeness_threshold,
        min_bullets_per_open_project=settings.min_bullets_per_open_project,
        max_bullets_per_item=settings.max_bullets_per_item,
        min_projects=settings.min_projects,
        max_projects=settings.max_projects,
        fill_page=settings.fill_page,
    )
    heights = EstimatedHeights(content)
    projects = {p.id: p for p in content.projects}
    course_names = select_coursework(list(coursework), settings)
    line_chars = settings.coursework_line_chars

    # Baseline = always-shown content (incl. all experience + coursework), no
    # projects -> how much room is left for projects.
    base_fit = compile_and_measure(_locked_context(content, course_names), build_dir=build_dir)
    compiles = 1
    if base_fit.fits:
        remaining_lines = ((base_fit.remaining_pt or 0.0) / LINE_PT) - SAFETY_LINES
        budget = max(0.0, remaining_lines)
    else:
        budget = 0.0  # experience+coursework already tight; trim loop will adjust

    sel = pack(content, scores, heights, config, budget, pins=pins, excludes=excludes)
    sel.coursework = list(course_names)

    # Verify + trim loop.
    fit = compile_and_measure(selection_to_context(content, sel), build_dir=build_dir)
    compiles += 1
    while not fit.fits and compiles < max_compiles:
        if _shrink_coursework_to_lines(sel, line_chars, 1):
            pass
        elif _trim_lowest(sel, scores, heights, projects, config):
            pass
        elif sel.coursework:
            sel.coursework.pop()
        else:
            break
        fit = compile_and_measure(selection_to_context(content, sel), build_dir=build_dir)
        compiles += 1

    status = fit.status if fit.fits else (
        "OVERFLOW after trimming (experience + coursework may exceed one page)")
    return PackResult(sel, fit, sel.total_score(scores), compiles, status)


def _trim_lowest(
    sel: Selection,
    scores: dict[str, float],
    heights: Heights,
    projects: dict[str, Project],
    config: PackConfig,
) -> bool:
    """Remove the single lowest-density removable project add-on. Experience is
    locked (never trimmed); pinned content and each project's secondary floor are
    respected (a project at its min is removed whole rather than dipping below).
    Returns False when nothing can be trimmed."""
    candidates: list[tuple[float, str, str]] = []  # (density, kind, key)

    for pid in sel.open_projects:
        ids = sel.proj_bullets.get(pid, [])
        non_pinned = [b for b in ids if b not in sel.pinned_bullets]
        floor = 0 if pid in sel.pinned_projects else _secondary_floor(projects[pid], config)
        # individually removable only if it keeps the project at/above its floor
        if len(ids) - 1 >= floor:
            for bid in non_pinned:
                candidates.append((scores.get(bid, 0.0) / max(heights.bullet(bid), 1e-9),
                                   "proj_bullet", bid))
        # A project may be closed only if it isn't pinned and closing keeps the
        # selection at/above the min_projects floor.
        if pid not in sel.pinned_projects and len(sel.open_projects) > config.min_projects:
            score = sum(scores.get(b, 0.0) for b in ids)
            height = heights.open_project(pid) + sum(heights.bullet(b) for b in ids)
            candidates.append((score / max(height, 1e-9), "proj_close", pid))

    if not candidates:
        return False
    candidates.sort(key=lambda c: (c[0], c[2]))
    _density, kind, key = candidates[0]

    if kind == "proj_bullet":
        for ids in sel.proj_bullets.values():
            if key in ids:
                ids.remove(key)
                return True
    elif kind == "proj_close":
        sel.open_projects.remove(key)
        sel.proj_bullets.pop(key, None)
        return True
    return False

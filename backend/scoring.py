"""Deterministic, alias-aware JD scoring (Stage A).

This module never calls a model. It parses the pasted JD, expands JD terms and
bullet/item tags through ``aliases.yaml`` to a canonical vocabulary, weights each
required skill by the JD section it appears in and how often, then assigns every
secondary bullet and item a numeric relevance score.

It is three things at once (per ``plan.md``):
  * the **baseline** ranking signal,
  * the **fallback** when the LLM layer is off or unreachable, and
  * the **shortlister** that picks candidate bullets for the LLM to re-rank.

Scores are a single number per unit; nothing here knows about page packing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.models import Content, Job, Project

# --------------------------------------------------------------------------- #
# Tunable weights (kept here, not in YAML, since they are scorer internals)
# --------------------------------------------------------------------------- #
BASE_WEIGHT = 1.0
SECTION_WEIGHTS = {"required": 1.5, "body": 1.0, "nice": 0.7}
FREQ_CAP = 4          # occurrences beyond this don't add weight
FREQ_BONUS = 0.2      # each repeat (up to the cap) adds this fraction

# Section-heading detection. "nice" is checked first so that e.g. "Preferred
# Qualifications" is treated as nice-to-have rather than a hard requirement.
_NICE_RE = re.compile(
    r"\b(nice[\s-]?to[\s-]?have|preferred|bonus|plus(?:es)?|a plus|"
    r"good to have|desired|optional)\b",
    re.IGNORECASE,
)
_REQUIRED_RE = re.compile(
    r"\b(requirements?|qualifications?|responsibilities|"
    r"what you'?ll do|must[\s-]?have|required|you have|minimum)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Alias / vocabulary handling
# --------------------------------------------------------------------------- #
def build_canonical_map(aliases: dict[str, list[str]]) -> dict[str, str]:
    """Lower-cased surface form -> canonical skill. Canonicals map to
    themselves so ``canonicalize`` is idempotent."""
    cmap: dict[str, str] = {}
    for canonical, variants in aliases.items():
        cmap[canonical.lower()] = canonical
        for v in variants:
            cmap[v.lower()] = canonical
    return cmap


def canonicalize(term: str, canonical_map: dict[str, str]) -> str:
    """Map a raw tag/term to its canonical form, or itself if unknown."""
    return canonical_map.get(term.lower(), term)


def collect_content_tags(content: Content) -> set[str]:
    """Every tag used anywhere in the library (item-level and bullet-level)."""
    tags: set[str] = set()
    for item in (*content.experience, *content.projects):
        tags.update(item.tags)
        for b in item.bullets:
            tags.update(b.tags)
    return tags


def _surface_forms(canonical: str, aliases: dict[str, list[str]]) -> list[str]:
    return [canonical, *aliases.get(canonical, [])]


# --------------------------------------------------------------------------- #
# JD analysis
# --------------------------------------------------------------------------- #
@dataclass
class SkillHit:
    canonical: str
    frequency: int
    section: str          # required / body / nice (strongest context seen)
    weight: float


@dataclass
class JDAnalysis:
    skill_weights: dict[str, float] = field(default_factory=dict)
    hits: dict[str, SkillHit] = field(default_factory=dict)

    def weight_of(self, canonical: str) -> float:
        return self.skill_weights.get(canonical, 0.0)


def _section_of_line(line: str) -> str | None:
    """Return 'required' / 'nice' if the line looks like a section heading."""
    if _NICE_RE.search(line):
        return "nice"
    if _REQUIRED_RE.search(line):
        return "required"
    return None


# strongest-context ordering so a skill in a "required" block beats a later
# mention in the body or a nice-to-have block.
_SECTION_RANK = {"nice": 0, "body": 1, "required": 2}


def analyze_jd(
    jd_text: str,
    aliases: dict[str, list[str]],
    vocab: set[str],
) -> JDAnalysis:
    """Score the JD against ``vocab`` (canonical skills). For each skill we find
    its strongest section context and frequency, then
    ``weight = base * section_weight * freq_factor``."""
    canonical_map = build_canonical_map(aliases)
    # The terms we search the JD for: alias canonicals plus any content tag,
    # canonicalized so a bullet tag with no alias entry is still searchable.
    canonicals = {canonicalize(t, canonical_map) for t in vocab}
    canonicals |= set(aliases.keys())

    # Pre-compile a whole-word matcher per canonical, OR-ing its surface forms.
    matchers: dict[str, re.Pattern[str]] = {}
    for canonical in canonicals:
        forms = sorted(set(_surface_forms(canonical, aliases)), key=len, reverse=True)
        alt = "|".join(re.escape(f) for f in forms if f)
        if not alt:
            continue
        # \b is unreliable next to '+'/'.' (e.g. c++); allow a boundary or a
        # non-word/edge on either side.
        matchers[canonical] = re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE)

    # Walk the JD line by line, tracking the active section context.
    section = "body"
    freq: dict[str, int] = {}
    best_section: dict[str, str] = {}
    for line in jd_text.splitlines():
        heading = _section_of_line(line)
        if heading is not None:
            section = heading
            # A heading line itself can also name skills (e.g. "Required: Python").
        for canonical, matcher in matchers.items():
            n = len(matcher.findall(line))
            if not n:
                continue
            freq[canonical] = freq.get(canonical, 0) + n
            current = best_section.get(canonical)
            current_rank = _SECTION_RANK[current] if current is not None else -1
            if _SECTION_RANK[section] > current_rank:
                best_section[canonical] = section

    analysis = JDAnalysis()
    for canonical, n in freq.items():
        sect = best_section.get(canonical, "body")
        freq_factor = 1.0 + FREQ_BONUS * (min(n, FREQ_CAP) - 1)
        weight = BASE_WEIGHT * SECTION_WEIGHTS[sect] * freq_factor
        analysis.skill_weights[canonical] = weight
        analysis.hits[canonical] = SkillHit(canonical, n, sect, weight)
    return analysis


# --------------------------------------------------------------------------- #
# Content scoring
# --------------------------------------------------------------------------- #
@dataclass
class BulletScore:
    bullet_id: str
    item_id: str
    raw: float
    normalized: float
    matched_skills: list[str]
    # Filled by the LLM layer (step 4) when enabled; left as None for the
    # deterministic-only / fallback path.
    llm_score: float | None = None
    final_score: float | None = None
    matched_requirements: list[str] = field(default_factory=list)

    @property
    def pack_score(self) -> float:
        """The single number the packer ranks on: the blended LLM score when
        available, else the normalized baseline."""
        return self.final_score if self.final_score is not None else self.normalized


@dataclass
class ItemScore:
    item_id: str
    kind: str             # "job" | "project"
    tag_score: float      # from item-level tags only
    bullet_total: float   # sum of this item's secondary-bullet raw scores
    matched_skills: list[str]

    @property
    def total(self) -> float:
        return self.tag_score + self.bullet_total


@dataclass
class ScoreResult:
    jd: JDAnalysis
    bullets: dict[str, BulletScore] = field(default_factory=dict)
    items: dict[str, ItemScore] = field(default_factory=dict)

    def ranked_bullets(self) -> list[BulletScore]:
        # Stable: score desc, then id for determinism on ties.
        return sorted(self.bullets.values(), key=lambda b: (-b.raw, b.bullet_id))


def _score_tags(
    tags: list[str], jd: JDAnalysis, canonical_map: dict[str, str]
) -> tuple[float, list[str]]:
    total = 0.0
    matched: list[str] = []
    for tag in tags:
        c = canonicalize(tag, canonical_map)
        w = jd.weight_of(c)
        if w > 0:
            total += w
            matched.append(c)
    return total, matched


def score_content(content: Content, jd: JDAnalysis) -> ScoreResult:
    """Assign a raw score to every secondary bullet and item from the JD
    analysis, then normalize bullet scores to ``[0, 1]`` for blending."""
    canonical_map = build_canonical_map(content.aliases)
    result = ScoreResult(jd=jd)

    def handle(item: Job | Project, kind: str) -> None:
        tag_score, item_matched = _score_tags(item.tags, jd, canonical_map)
        bullet_total = 0.0
        for b in item.bullets:
            raw, matched = _score_tags(b.tags, jd, canonical_map)
            result.bullets[b.id] = BulletScore(
                bullet_id=b.id, item_id=item.id, raw=raw,
                normalized=0.0, matched_skills=matched,
            )
            bullet_total += raw
        result.items[item.id] = ItemScore(
            item_id=item.id, kind=kind, tag_score=tag_score,
            bullet_total=bullet_total, matched_skills=item_matched,
        )

    for job in content.experience:
        handle(job, "job")
    for proj in content.projects:
        handle(proj, "project")

    max_raw = max((b.raw for b in result.bullets.values()), default=0.0)
    if max_raw > 0:
        for b in result.bullets.values():
            b.normalized = b.raw / max_raw
    return result


def shortlist(result: ScoreResult, n: int) -> list[str]:
    """Top-``n`` secondary-bullet ids by baseline score, for the LLM re-rank.
    Zero-score bullets are excluded — nothing matched, nothing to re-rank."""
    ranked = [b for b in result.ranked_bullets() if b.raw > 0]
    return [b.bullet_id for b in ranked[:n]]


def score(content: Content, jd_text: str) -> ScoreResult:
    """Convenience: analyze the JD against the library vocabulary and score."""
    vocab = collect_content_tags(content)
    jd = analyze_jd(jd_text, content.aliases, vocab)
    return score_content(content, jd)

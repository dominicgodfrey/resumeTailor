"""Stage B: the local-LLM layer (Qwen 3 8B via Ollama).

The model is allowed to do exactly two things, both read-only:
  1. **Extract** weighted skills/requirements from the JD (structured JSON), and
  2. **Re-rank** a shortlist of *pre-written* bullets by relevance — emitting
     ``{bullet_id, relevance, matched_requirements}`` and **never** bullet text.

The final per-bullet number is ``blend_weight * llm + (1 - blend_weight) *
normalized_baseline`` and is written onto ``BulletScore.final_score`` so the
packer keeps consuming a single number per unit.

Everything here degrades gracefully: a connection error, non-200, or malformed
JSON is logged and the call returns ``None`` so the caller keeps the
deterministic baseline. Results are cached by ``(jd_hash, content_hash, model)``
at temperature 0 for reproducibility. No model output ever mutates content.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from backend.models import Content, Settings
from backend.scoring import ScoreResult, score_content, analyze_jd, collect_content_tags

log = logging.getLogger("resumetailor.llm")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / ".llm-cache"

# How many bullets to send to the re-rank call. Baseline-nonzero bullets first,
# then high-tier bullets so important content still gets a semantic look even if
# its tags didn't match the JD literally.
DEFAULT_CANDIDATE_LIMIT = 16


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
class LLMUnavailable(Exception):
    """Raised internally when Ollama can't be reached or returns an error."""


class ChatClient(Protocol):
    def chat(self, messages: list[dict]) -> str: ...


@dataclass
class OllamaClient:
    """Minimal OpenAI-compatible chat client for Ollama. Fast-failing connect so
    fallback is quick when the server is down; generous read for local gen."""

    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen3:8b"
    thinking: bool = False
    read_timeout_s: float = 120.0
    connect_timeout_s: float = 3.0

    def chat(self, messages: list[dict]) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        # Qwen 3 honors a "/no_think" directive to skip its reasoning phase.
        msgs = list(messages)
        if not self.thinking and msgs and msgs[0].get("role") == "system":
            msgs[0] = {**msgs[0], "content": msgs[0]["content"] + "\n/no_think"}
        payload = {
            "model": self.model,
            "messages": msgs,
            "temperature": 0,
            "seed": 0,
            "stream": False,
        }
        timeout = httpx.Timeout(self.read_timeout_s, connect=self.connect_timeout_s)
        try:
            resp = httpx.post(url, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"request to {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMUnavailable(f"{url} returned {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailable(f"unexpected response shape: {exc}") from exc


# --------------------------------------------------------------------------- #
# JSON extraction (robust to think-tags, code fences, and surrounding prose)
# --------------------------------------------------------------------------- #
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str):
    """Pull the first valid JSON value out of a model reply. Tolerates a
    ``<think>`` preamble, ``json`` code fences, and trailing commentary.
    Raises ``ValueError`` if nothing parses."""
    cleaned = _THINK_RE.sub("", text).strip()
    cleaned = _FENCE_RE.sub("", cleaned).strip()
    decoder = json.JSONDecoder()
    for i, ch in enumerate(cleaned):
        if ch in "[{":
            try:
                obj, _ = decoder.raw_decode(cleaned[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON value found in model output")


# --------------------------------------------------------------------------- #
# Disk cache  (key = sha256 of jd_hash:content_hash:model:kind)
# --------------------------------------------------------------------------- #
def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass
class DiskCache:
    directory: Path = CACHE_DIR

    def _path(self, key: str) -> Path:
        return self.directory / f"{_sha(key)}.json"

    def get(self, key: str):
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def set(self, key: str, value) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps(value), encoding="utf-8")
        except OSError as exc:  # cache is best-effort; never fatal
            log.warning("llm cache write failed: %s", exc)


def jd_hash(jd_text: str) -> str:
    return _sha(jd_text.strip())


def candidates_hash(candidates: list[tuple[str, str]]) -> str:
    """Fingerprint the (id, text) pairs sent to the model, so editing any bullet
    invalidates its cached ranking."""
    blob = "\n".join(f"{cid} {txt}" for cid, txt in candidates)
    return _sha(blob)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
_EXTRACT_SYSTEM = (
    "You extract required skills from a job description. "
    "You NEVER write, summarize, or invent resume content. "
    "Output ONLY a JSON object of the form "
    '{"skills": [{"name": "<skill>", "weight": <0.0-1.0>}]}. '
    "weight is how important the skill is to the role. No prose, JSON only."
)

_RERANK_SYSTEM = (
    "You score how relevant each PRE-WRITTEN resume bullet is to a set of job "
    "requirements. The bullet text is authored by a human and is immutable: you "
    "MUST NOT rewrite, edit, paraphrase, translate, or output any bullet text. "
    "Refer to bullets only by their bullet_id. "
    "Output ONLY a JSON object of the form "
    '{"rankings": [{"bullet_id": "<id>", "relevance": <0.0-1.0>, '
    '"matched_requirements": ["<req>"]}]}. '
    "Score every bullet_id you are given exactly once. No prose, JSON only."
)


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
@dataclass
class ExtractedSkill:
    name: str
    weight: float


def extract_jd_skills(
    jd_text: str,
    settings: Settings,
    *,
    client: ChatClient | None = None,
    cache: DiskCache | None = None,
) -> list[ExtractedSkill] | None:
    """One Ollama call -> weighted JD skills, or ``None`` on any failure."""
    if not settings.llm.enabled:
        return None
    cache = cache if cache is not None else DiskCache()
    key = f"{jd_hash(jd_text)}::{settings.llm.model}::extract"
    cached = cache.get(key)
    if cached is None:
        client = client or _client_from(settings)
        try:
            raw = client.chat([
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": jd_text},
            ])
            obj = extract_json(raw)
            cached = obj.get("skills", []) if isinstance(obj, dict) else obj
            cache.set(key, cached)
        except (LLMUnavailable, ValueError, AttributeError) as exc:
            log.warning("JD extraction failed, using baseline only: %s", exc)
            return None
    return _parse_skills(cached)


def _parse_skills(raw) -> list[ExtractedSkill]:
    out: list[ExtractedSkill] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            weight = float(item.get("weight", 0.0))
        except (TypeError, ValueError):
            weight = 0.0
        out.append(ExtractedSkill(name=name.strip(), weight=max(0.0, min(1.0, weight))))
    return out


# --------------------------------------------------------------------------- #
# Re-rank
# --------------------------------------------------------------------------- #
@dataclass
class RerankItem:
    relevance: float
    matched_requirements: list[str] = field(default_factory=list)


def rerank_bullets(
    requirements: list[str],
    candidates: list[tuple[str, str]],
    settings: Settings,
    *,
    client: ChatClient | None = None,
    cache: DiskCache | None = None,
) -> dict[str, RerankItem] | None:
    """Re-rank ``candidates`` (id, text) against ``requirements``. Returns a map
    keyed by bullet_id, or ``None`` on failure. Any returned 'text' field is
    ignored — only ids and numeric relevance are read."""
    if not settings.llm.enabled or not candidates:
        return None
    cache = cache if cache is not None else DiskCache()
    req_blob = jd_hash("|".join(requirements))
    key = f"{req_blob}::{candidates_hash(candidates)}::{settings.llm.model}::rerank"
    cached = cache.get(key)
    if cached is None:
        client = client or _client_from(settings)
        user = json.dumps({
            "requirements": requirements,
            "bullets": [{"bullet_id": cid, "text": txt} for cid, txt in candidates],
        })
        try:
            raw = client.chat([
                {"role": "system", "content": _RERANK_SYSTEM},
                {"role": "user", "content": user},
            ])
            obj = extract_json(raw)
            cached = obj.get("rankings", []) if isinstance(obj, dict) else obj
            cache.set(key, cached)
        except (LLMUnavailable, ValueError, AttributeError) as exc:
            log.warning("bullet re-rank failed, using baseline only: %s", exc)
            return None
    valid_ids = {cid for cid, _ in candidates}
    return _parse_rankings(cached, valid_ids)


def _parse_rankings(raw, valid_ids: set[str]) -> dict[str, RerankItem]:
    out: dict[str, RerankItem] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        bid = item.get("bullet_id")
        if bid not in valid_ids:  # ignore unknown / hallucinated ids
            continue
        try:
            rel = float(item.get("relevance", 0.0))
        except (TypeError, ValueError):
            continue
        reqs = item.get("matched_requirements", [])
        reqs = [str(r) for r in reqs] if isinstance(reqs, list) else []
        out[bid] = RerankItem(relevance=max(0.0, min(1.0, rel)), matched_requirements=reqs)
    return out


# --------------------------------------------------------------------------- #
# Candidate selection + blend
# --------------------------------------------------------------------------- #
_TIER_PRIORITY = {"must": 0, "strong": 1, "optional": 2}


def select_candidates(
    content: Content, baseline: ScoreResult, limit: int = DEFAULT_CANDIDATE_LIMIT
) -> list[tuple[str, str]]:
    """Pick (id, text) pairs for the re-rank: baseline-nonzero bullets first
    (by score), then fill with high-tier bullets so important content still gets
    a semantic look. Deterministic ordering throughout."""
    text_by_id = {b.id: b.text for item in (*content.experience, *content.projects)
                  for b in item.bullets}
    tier_by_id = {b.id: b.tier for item in (*content.experience, *content.projects)
                  for b in item.bullets}

    chosen: list[str] = [b.bullet_id for b in baseline.ranked_bullets() if b.raw > 0]
    if len(chosen) < limit:
        remaining = sorted(
            (bid for bid in text_by_id if bid not in chosen),
            key=lambda bid: (_TIER_PRIORITY.get(tier_by_id[bid], 9), bid),
        )
        for bid in remaining:
            if len(chosen) >= limit:
                break
            if tier_by_id[bid] in ("must", "strong"):
                chosen.append(bid)
    return [(bid, text_by_id[bid]) for bid in chosen[:limit]]


def apply_blend(
    baseline: ScoreResult, rankings: dict[str, RerankItem], blend_weight: float
) -> ScoreResult:
    """Write ``final_score`` onto every bullet:
    ``blend*llm + (1-blend)*normalized`` where an LLM score exists, else just the
    baseline portion. Mutates and returns ``baseline``."""
    for b in baseline.bullets.values():
        item = rankings.get(b.bullet_id)
        if item is not None:
            b.llm_score = item.relevance
            b.final_score = blend_weight * item.relevance + (1 - blend_weight) * b.normalized
            b.matched_requirements = item.matched_requirements
        else:
            b.final_score = (1 - blend_weight) * b.normalized
    return baseline


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _client_from(settings: Settings) -> OllamaClient:
    return OllamaClient(
        base_url=settings.llm.base_url,
        model=settings.llm.model,
        thinking=settings.llm.thinking,
    )


@dataclass
class BlendOutcome:
    result: ScoreResult
    llm_used: bool


def score_blended(
    content: Content,
    jd_text: str,
    settings: Settings,
    *,
    client: ChatClient | None = None,
    cache: DiskCache | None = None,
) -> BlendOutcome:
    """Full Stage A + Stage B pipeline. Always returns a usable ScoreResult:
    when the LLM is off/unreachable, ``pack_score`` falls back to the normalized
    baseline (``llm_used=False``)."""
    vocab = collect_content_tags(content)
    jd = analyze_jd(jd_text, content.aliases, vocab)
    baseline = score_content(content, jd)

    if not settings.llm.enabled:
        return BlendOutcome(result=baseline, llm_used=False)

    cache = cache if cache is not None else DiskCache()
    skills = extract_jd_skills(jd_text, settings, client=client, cache=cache)
    # Requirements for the re-rank: LLM-extracted names if available, else the
    # deterministic JD skills (graceful degradation of just the extraction call).
    if skills:
        requirements = [s.name for s in skills]
    else:
        requirements = sorted(jd.skill_weights, key=jd.skill_weights.get, reverse=True)

    candidates = select_candidates(content, baseline)
    rankings = rerank_bullets(requirements, candidates, settings, client=client, cache=cache)
    if rankings is None:
        return BlendOutcome(result=baseline, llm_used=False)

    blended = apply_blend(baseline, rankings, settings.llm.blend_weight)
    return BlendOutcome(result=blended, llm_used=True)

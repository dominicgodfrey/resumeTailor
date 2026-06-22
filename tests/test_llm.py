"""Unit tests for the LLM layer (Stage B) — all offline via stub clients.

Covers JSON-extraction robustness, schema parsing, the content-immutability
guarantee (returned bullet text is ignored), the blend math, caching, and
graceful fallback when Ollama is unreachable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.llm import (  # noqa: E402
    DiskCache,
    LLMUnavailable,
    apply_blend,
    candidates_hash,
    extract_json,
    extract_jd_skills,
    rerank_bullets,
    score_blended,
    select_candidates,
)
from backend.models import Bullet, Content, Profile, Project, Settings  # noqa: E402
from backend.scoring import score  # noqa: E402


# --------------------------------------------------------------------------- #
# Stub clients
# --------------------------------------------------------------------------- #
class StubClient:
    """Returns a canned string and records the messages it was asked to send."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[list[dict]] = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


class DownClient:
    def chat(self, messages):
        raise LLMUnavailable("connection refused")


def _settings(enabled=True, blend=0.6) -> Settings:
    return Settings.model_validate({"llm": {"enabled": enabled, "blend_weight": blend}})


def _content() -> Content:
    proj = Project(
        id="p1", name="P",
        bullets=[
            Bullet(id="b1", text="Built a REST API in Python", tags=["python", "rest"], tier="strong"),
            Bullet(id="b2", text="Tuned PostgreSQL indexes", tags=["postgres"], tier="optional"),
        ],
    )
    return Content(profile=Profile(name="T"), projects=[proj],
                   aliases={"postgres": ["postgresql"]})


@pytest.fixture
def cache(tmp_path) -> DiskCache:
    return DiskCache(directory=tmp_path / "llmcache")


# --------------------------------------------------------------------------- #
# extract_json robustness
# --------------------------------------------------------------------------- #
def test_extract_json_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_think_and_fences():
    raw = '<think>let me reason...</think>\n```json\n{"skills": []}\n```'
    assert extract_json(raw) == {"skills": []}


def test_extract_json_ignores_trailing_prose():
    raw = 'Sure! Here you go: {"rankings": [{"bullet_id": "b1"}]} hope that helps'
    assert extract_json(raw) == {"rankings": [{"bullet_id": "b1"}]}


def test_extract_json_array_top_level():
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("no json here at all")


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def test_extract_skills_parses_and_clamps(cache):
    client = StubClient('{"skills": [{"name": "Python", "weight": 1.5}, '
                        '{"name": "", "weight": 0.5}, {"weight": 0.2}]}')
    skills = extract_jd_skills("jd text", _settings(), client=client, cache=cache)
    assert [s.name for s in skills] == ["Python"]   # blank / nameless dropped
    assert skills[0].weight == 1.0                  # clamped to [0,1]


def test_extract_disabled_returns_none(cache):
    out = extract_jd_skills("jd", _settings(enabled=False), client=StubClient("{}"), cache=cache)
    assert out is None


def test_extract_fallback_on_down_client(cache):
    assert extract_jd_skills("jd", _settings(), client=DownClient(), cache=cache) is None


# --------------------------------------------------------------------------- #
# Re-rank + content immutability
# --------------------------------------------------------------------------- #
def test_rerank_ignores_returned_text_and_unknown_ids(cache):
    # Model tries to smuggle bullet text and an unknown id; both are ignored.
    reply = ('{"rankings": [{"bullet_id": "b1", "relevance": 0.9, '
             '"text": "REWRITTEN BY MODEL", "matched_requirements": ["python"]}, '
             '{"bullet_id": "ghost", "relevance": 1.0}]}')
    client = StubClient(reply)
    out = rerank_bullets(["python"], [("b1", "Built a REST API")], _settings(),
                         client=client, cache=cache)
    assert set(out) == {"b1"}                 # ghost id discarded
    assert out["b1"].relevance == 0.9
    assert not hasattr(out["b1"], "text")     # no text field carried through


def test_rerank_fallback_on_down_client(cache):
    assert rerank_bullets(["x"], [("b1", "t")], _settings(), client=DownClient(),
                          cache=cache) is None


def test_rerank_empty_candidates_returns_none(cache):
    assert rerank_bullets(["x"], [], _settings(), client=StubClient("{}"), cache=cache) is None


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def test_rerank_caches_by_jd_content_model(cache):
    client = StubClient('{"rankings": [{"bullet_id": "b1", "relevance": 0.5}]}')
    args = (["python"], [("b1", "text")], _settings())
    rerank_bullets(*args, client=client, cache=cache)
    rerank_bullets(*args, client=client, cache=cache)   # served from cache
    assert len(client.calls) == 1


def test_candidates_hash_changes_with_text():
    assert candidates_hash([("b1", "a")]) != candidates_hash([("b1", "b")])


# --------------------------------------------------------------------------- #
# Blend math
# --------------------------------------------------------------------------- #
def test_apply_blend_combines_llm_and_baseline():
    from backend.llm import RerankItem
    content = _content()
    baseline = score(content, "Requirements:\nPython and REST and PostgreSQL")
    # b1 normalized is the top (1.0); give it an llm relevance of 0.5.
    rankings = {"b1": RerankItem(relevance=0.5, matched_requirements=["python"])}
    apply_blend(baseline, rankings, blend_weight=0.6)
    b1 = baseline.bullets["b1"]
    assert b1.final_score == pytest.approx(0.6 * 0.5 + 0.4 * b1.normalized)
    assert b1.pack_score == b1.final_score
    # b2 had no llm score -> baseline-only portion.
    b2 = baseline.bullets["b2"]
    assert b2.final_score == pytest.approx(0.4 * b2.normalized)


# --------------------------------------------------------------------------- #
# Candidate selection
# --------------------------------------------------------------------------- #
def test_select_candidates_includes_nonzero_then_high_tier():
    content = _content()
    baseline = score(content, "Python")   # only b1 (python) scores nonzero
    cand_ids = [cid for cid, _ in select_candidates(content, baseline, limit=16)]
    assert cand_ids[0] == "b1"            # nonzero baseline first
    assert "b2" not in cand_ids or content  # b2 is optional+zero; only added if high-tier
    # b2 is optional, so it is NOT pulled in as a tier fill:
    assert "b2" not in cand_ids


# --------------------------------------------------------------------------- #
# Orchestration / end-to-end with stubs
# --------------------------------------------------------------------------- #
def test_score_blended_falls_back_when_llm_disabled(cache):
    content = _content()
    out = score_blended(content, "Python and REST", _settings(enabled=False), cache=cache)
    assert out.llm_used is False
    # final_score untouched -> pack_score is the normalized baseline.
    assert out.result.bullets["b1"].final_score is None
    assert out.result.bullets["b1"].pack_score == out.result.bullets["b1"].normalized


def test_score_blended_falls_back_when_ollama_down(cache):
    content = _content()
    out = score_blended(content, "Python and REST", _settings(), client=DownClient(), cache=cache)
    assert out.llm_used is False
    assert out.result.bullets["b1"].pack_score == out.result.bullets["b1"].normalized


def test_score_blended_uses_llm_when_available(cache):
    content = _content()
    # One stub answers both extraction and rerank calls with valid JSON for each
    # schema; extraction looks for "skills", rerank for "rankings".
    class DualClient:
        def __init__(self):
            self.calls = []

        def chat(self, messages):
            self.calls.append(messages)
            sysmsg = messages[0]["content"]
            if "extract required skills" in sysmsg:
                return '{"skills": [{"name": "python", "weight": 0.9}]}'
            return '{"rankings": [{"bullet_id": "b1", "relevance": 0.8, "matched_requirements": ["python"]}]}'

    out = score_blended(content, "Python and REST", _settings(), client=DualClient(), cache=cache)
    assert out.llm_used is True
    b1 = out.result.bullets["b1"]
    assert b1.llm_score == 0.8
    assert b1.final_score == pytest.approx(0.6 * 0.8 + 0.4 * b1.normalized)
    assert b1.matched_requirements == ["python"]

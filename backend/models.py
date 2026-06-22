"""Pydantic schemas for the hand-authored resume content.

These mirror the YAML data model described in ``plan.md``. The content is the
source of truth, edited by hand; the app only *reads* and *validates* it. No
field here is ever written by a model — bullet ``text`` is raw LaTeX authored by
the user and must survive byte-for-byte into the rendered PDF.

Validation rules enforced here:
  * bullet / item ids are non-empty and unique within their scope,
  * ``tier`` is one of must / strong / optional,
  * the settings block carries sane defaults so a sparse ``profile.yaml`` works.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Tier = Literal["must", "strong", "optional"]


class _Base(BaseModel):
    # Reject unknown keys so typos in hand-edited YAML surface immediately
    # rather than being silently dropped.
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class LLMSettings(_Base):
    enabled: bool = True
    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen3:8b"
    blend_weight: float = Field(0.6, ge=0.0, le=1.0)  # weight of LLM vs baseline
    thinking: bool = False


class Settings(_Base):
    page_target: int = Field(1, ge=1)
    closeness_threshold: float = Field(0.10, ge=0.0, le=1.0)
    min_bullets_per_open_project: int = Field(2, ge=1)
    max_bullets_per_item: int = Field(4, ge=1)
    llm: LLMSettings = Field(default_factory=LLMSettings)


# --------------------------------------------------------------------------- #
# Header / locked content
# --------------------------------------------------------------------------- #
class Contact(_Base):
    text: str
    href: str | None = None


class Education(_Base):
    school: str
    location: str = ""
    degree: str
    dates: str = ""


class SkillCategory(_Base):
    category: str
    items: str  # raw LaTeX string, e.g. "Python, C/C++, SQL (Postgres)"


# --------------------------------------------------------------------------- #
# Scored content
# --------------------------------------------------------------------------- #
class Bullet(_Base):
    id: str
    text: str  # raw LaTeX, never auto-escaped, never model-written
    tags: list[str] = Field(default_factory=list)
    tier: Tier = "optional"

    @field_validator("id")
    @classmethod
    def _id_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("bullet id must be non-empty")
        return v


class Job(_Base):
    """An experience entry. Always shown (locked) with its fixed first bullet;
    only the secondary ``bullets`` are score-governed."""

    id: str
    company: str
    title: str
    location: str = ""
    dates: str = ""
    link: str | None = None
    fixed_bullet: str | None = None  # always shown first when the job appears
    bullets: list[Bullet] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # item-level tags

    @field_validator("bullets")
    @classmethod
    def _unique_bullet_ids(cls, v: list[Bullet]) -> list[Bullet]:
        _require_unique([b.id for b in v], "bullet id")
        return v


class Project(_Base):
    """A project entry. Inclusion *and* secondary bullets are score-governed;
    the fixed first bullet is shown only when the project is selected."""

    id: str
    name: str
    tech: str | None = None  # tech-stack line shown in the project heading
    dates: str = ""
    link: str | None = None
    fixed_bullet: str | None = None
    bullets: list[Bullet] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("bullets")
    @classmethod
    def _unique_bullet_ids(cls, v: list[Bullet]) -> list[Bullet]:
        _require_unique([b.id for b in v], "bullet id")
        return v


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
class Profile(_Base):
    """Contents of ``profile.yaml`` — the locked header/footer content plus
    tunable settings. Experience and projects live in their own files."""

    name: str
    contacts: list[Contact] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    skills: list[SkillCategory] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)


class Content(BaseModel):
    """Everything the app loads: validated profile, experience, projects, and
    the alias map. Assembled by ``backend.content.load_content``."""

    model_config = ConfigDict(extra="forbid")

    profile: Profile
    experience: list[Job] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    aliases: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("experience")
    @classmethod
    def _unique_job_ids(cls, v: list[Job]) -> list[Job]:
        _require_unique([j.id for j in v], "experience id")
        return v

    @field_validator("projects")
    @classmethod
    def _unique_project_ids(cls, v: list[Project]) -> list[Project]:
        _require_unique([p.id for p in v], "project id")
        return v


def _require_unique(ids: list[str], label: str) -> None:
    seen: set[str] = set()
    dupes: set[str] = set()
    for i in ids:
        if i in seen:
            dupes.add(i)
        seen.add(i)
    if dupes:
        raise ValueError(f"duplicate {label}(s): {', '.join(sorted(dupes))}")

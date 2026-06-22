"""Load and validate the hand-authored YAML content.

``profile.yaml`` holds the locked header/education/awards/skills and settings.
``aliases.yaml`` maps a canonical skill to its variants. Each file under
``experience/`` and ``projects/`` is one entry. Everything is parsed with a
safe YAML loader and validated through ``backend.models`` — a malformed or
duplicate-id library raises here, before any scoring or rendering runs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from backend.models import Content, Job, Profile, Project

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTENT_DIR = PROJECT_ROOT / "content"


class ContentError(Exception):
    """Raised when content is missing, malformed, or fails validation."""


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise ContentError(f"missing content file: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:  # noqa: PERF203 - surface the file in the message
        raise ContentError(f"invalid YAML in {path}: {exc}") from exc


def _load_dir(directory: Path) -> list[dict]:
    """Read every ``*.yaml`` / ``*.yml`` in ``directory``, sorted by filename
    for deterministic ordering. Returns the raw mappings."""
    if not directory.exists():
        return []
    items: list[dict] = []
    for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        data = _read_yaml(path)
        if data is None:
            continue
        if not isinstance(data, dict):
            raise ContentError(f"{path} must contain a single mapping, got {type(data).__name__}")
        items.append(data)
    return items


def load_profile(content_dir: Path = DEFAULT_CONTENT_DIR) -> Profile:
    data = _read_yaml(content_dir / "profile.yaml")
    if not isinstance(data, dict):
        raise ContentError("profile.yaml must contain a single mapping")
    try:
        return Profile.model_validate(data)
    except Exception as exc:  # pydantic ValidationError -> friendly wrapper
        raise ContentError(f"profile.yaml failed validation: {exc}") from exc


def load_aliases(content_dir: Path = DEFAULT_CONTENT_DIR) -> dict[str, list[str]]:
    path = content_dir / "aliases.yaml"
    if not path.exists():
        return {}
    data = _read_yaml(path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ContentError("aliases.yaml must map canonical-skill -> [variants]")
    aliases: dict[str, list[str]] = {}
    for canonical, variants in data.items():
        if variants is None:
            variants = []
        if not isinstance(variants, list):
            raise ContentError(f"aliases.yaml: '{canonical}' must map to a list of variants")
        aliases[str(canonical)] = [str(v) for v in variants]
    return aliases


def load_experience(content_dir: Path = DEFAULT_CONTENT_DIR) -> list[Job]:
    raw = _load_dir(content_dir / "experience")
    try:
        return [Job.model_validate(d) for d in raw]
    except Exception as exc:
        raise ContentError(f"experience entry failed validation: {exc}") from exc


def load_projects(content_dir: Path = DEFAULT_CONTENT_DIR) -> list[Project]:
    raw = _load_dir(content_dir / "projects")
    try:
        return [Project.model_validate(d) for d in raw]
    except Exception as exc:
        raise ContentError(f"project entry failed validation: {exc}") from exc


def load_content(content_dir: Path = DEFAULT_CONTENT_DIR) -> Content:
    """Load and cross-validate the full library (profile + experience + projects
    + aliases). Cross-entry uniqueness of ids is enforced by ``Content``."""
    try:
        return Content(
            profile=load_profile(content_dir),
            experience=load_experience(content_dir),
            projects=load_projects(content_dir),
            aliases=load_aliases(content_dir),
        )
    except ContentError:
        raise
    except Exception as exc:
        raise ContentError(f"content failed cross-validation: {exc}") from exc

# CLAUDE.md

Guidance for working in this repository. Read this and [`plan.md`](plan.md) before making changes.

## What this project is

A **local web app that tailors a one-page resume to a job description (JD)** by *selecting and arranging* pre-written content — never by rewriting it. The user maintains a hand-authored library of bullets; the app scores them against a pasted JD, auto-packs a full-page proposal the user can override, and renders a real PDF from the user's LaTeX template.

## The one rule that must never break

**No model ever writes or edits resume content.** Bullet text is authored by hand and must appear **byte-for-byte** in the output. The local LLM (Qwen 3 8B via Ollama) is allowed only to **read** the JD and existing bullets and **emit scores / extracted skills** — structured JSON, never prose. Any feature that would have a model generate or alter bullet text is out of scope by design. A test asserts rendered PDF bullet text equals the source YAML.

Corollaries:
- Every project and experience has a **fixed first bullet** that is always shown when that item appears (high-level overview + tech stack + purpose/flow). It is never reordered, swapped, or omitted while the item is shown.
- Locked, always-present content: name, contact, portfolio, LinkedIn, GitHub, education, awards, the static Technical Skills block, and every experience job entry. Only *secondary* bullets, *project inclusion*, and *project bullets* are score-governed.

## Stack

- **Backend:** Python + FastAPI.
- **PDF:** Tectonic (single bundled binary; offline after first package fetch).
- **Local model:** Qwen 3 8B served by **Ollama** at `http://localhost:11434/v1` (OpenAI-compatible). Base URL and model id are configurable in `content/profile.yaml` under `settings.llm`.
- **Libraries:** `pydantic`, `pyyaml`, `jinja2`, `pypdf`, `httpx`. No in-process ML libs — the model runs in Ollama.
- **Frontend:** single page, vanilla JS + `fetch` (no framework in v1).
- **Content:** hand-edited YAML in `content/` (read-only from the app's perspective in v1).

## Architecture map

See [`plan.md`](plan.md) for the full design. Backend modules:

| File | Responsibility |
|---|---|
| `backend/models.py` | pydantic schemas (Profile, Job, Project, Bullet, Settings) |
| `backend/content.py` | load + validate YAML content |
| `backend/scoring.py` | deterministic JD parse, alias expansion, weighting, baseline scores |
| `backend/llm.py` | Ollama client: JD skill extraction + bullet re-rank (scores only), caching, fallback |
| `backend/packer.py` | bundle-greedy auto-pack, breadth tie-break, pins/excludes/caps |
| `backend/render.py` | Jinja2 → `.tex`, Tectonic compile, one-page fit probe (`zref-savepos`) |
| `backend/archive.py` | write dated export folder (PDF, `.tex`, JD, selection JSON) |
| `backend/app.py` | FastAPI routes |

## Scoring = deterministic baseline blended with LLM

- **Stage A (always):** alias-aware keyword scoring with requirement-section/frequency weighting. Deterministic; also the fallback and the candidate **shortlister**.
- **Stage B (when `llm.enabled`):** Ollama extracts weighted JD skills, then re-ranks the shortlist. Final score = `blend_weight * llm + (1 - blend_weight) * baseline`.
- **Always degrade gracefully:** if Ollama is down or returns malformed JSON, log and fall back to baseline-only. Never block on the model. Cache LLM results by `(jd_hash, content_hash, model)` for reproducibility; run at temperature 0.

## Packer invariants

Maximize total score; on near-ties (within `closeness_threshold`) prefer breadth. Eligible projects are dropped by default and **pinned to force**. Respect `min_bullets_per_open_project`, `max_bullets_per_item`, pins, and excludes. Verify fit by real compile + page-count / `zref-savepos`, not estimation alone.

## Conventions

- Treat bullet `text` as **raw LaTeX** — do not auto-escape; the author may use `\textbf{}` etc.
- Keep the packer's input a single numeric score per unit; never let scoring changes leak into packer logic.
- New settings go in `content/profile.yaml` under `settings`, surfaced through `models.py`.

## Commits & attribution

- **Do not list Claude / Claude Code as a contributor or co-author.** Do not add `Co-Authored-By` trailers or "Generated with" lines to commits or PRs. Commits are authored solely by the repository owner.
- Commit in coherent, atomic units; push only when asked.

## Workflow

Build proceeds in **major phases** (see the checkpoint list at the end of `plan.md`). **Stop and wait for instruction after each major phase.** Implementation has not started beyond scaffolding; step 1 (template ingestion) is blocked until the LaTeX template is added to the repo.

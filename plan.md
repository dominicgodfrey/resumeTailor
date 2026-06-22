# Resume Tailor — Implementation Plan

## Context

The goal is a tool to tailor a one-page resume to each job description (JD) **without any AI rewriting of content**. Bullet text written by hand is sacred and must appear byte-for-byte. The tool's job is *selection and arrangement*, not authoring:

- Every project and every experience has a **fixed first bullet** (high-level overview: general tech stack + purpose/flow) that is always shown when that item appears.
- Secondary bullets are **swappable** — chosen from a pre-written pool per item to best fit the JD.
- Certain content is **non-negotiable / always shown**: name, contact, portfolio site, LinkedIn, GitHub, education, awards, the Technical Skills block (static), and every experience job entry (with its fixed first bullet).
- **Projects** are the main dynamic section: a variable number appear depending on fit, and the packer fills the page — more projects with ~2 bullets each when many fit, fewer projects developed in more depth when only 2–3 fit.
- Hard target: **exactly one page**, no overflow, no wasted space.

The desired outcome is a **local web app** that scores a pasted JD against a hand-tagged bullet library — combining a deterministic alias/keyword scorer with a **local Qwen 3 8B (via Ollama) used only to read the JD and rank existing bullets, never to write them** — auto-packs a full-page proposal the user can override, renders a true PDF via the user's real LaTeX template, and archives each export.

> **Refinement:** a small **local** model (Qwen 3 8B, installed via Ollama) assists *matching*. This does not violate the core rule: the model only reads text and emits scores/extracted-skills — it never generates or edits bullet content. The deterministic scorer remains as baseline and as fallback when the model is off/unreachable.

## Locked design decisions

| Area | Decision |
|---|---|
| Selection engine | **No AI rewriting.** Deterministic tag/keyword scoring **blended with a local LLM (Qwen 3 8B via Ollama) for matching only** + manual overrides. Bullet text is never generated or altered by any model. |
| Interface | **Local web app**: Python + FastAPI backend, browser frontend. |
| PDF compile | **Tectonic** (bundled single binary; offline after first package fetch). |
| Page target | **One page**, hard constraint. |
| Fit behavior | **Auto-pack proposal** from the eligible pool; user tweaks. |
| Ranking signal | **Hybrid**: JD score drives ranking; per-bullet/project **pin / exclude / tier** overrides. |
| Pack objective | **Maximize total score**; when scores are within a tunable **closeness threshold**, prefer **breadth** (new project / less-developed item). |
| Eligible → shown | **Drop by default, pin to force.** "Eligible" = in scored pool; low scorers can be omitted unless pinned. |
| Skills section | **Fixed static block** (locked, like education/awards). |
| Match quality | **Alias-aware + weighting** (deterministic baseline/fallback) **blended with LLM extract + re-rank** for semantic matches the alias map misses. |
| Content mgmt | **YAML files** (source of truth, hand-edited); app reads them (read-only library for v1). |
| JD input | **Paste text.** |
| History | **Auto-archive on export**: dated folder with PDF, `.tex`, JD, and selection JSON. |
| Backend | **Python + FastAPI.** |
| Local model | **Qwen 3 8B via Ollama** (OpenAI-compatible at `localhost:11434/v1`); base_url/model configurable. |
| Model role | **Extract + re-rank, blended**: LLM extracts weighted JD skills, then semantically re-ranks a keyword-shortlisted candidate set; final score blends LLM + deterministic. **Graceful fallback** to deterministic-only if Ollama is unreachable. |

**Locked sections** (placed first, score-independent): header (name/contact/portfolio/LinkedIn/GitHub), education, awards, static skills block, and all experience jobs + their fixed first bullet.
**Score-governed**: project inclusion, project secondary bullets, experience secondary bullets. A project's fixed first bullet is included only if the project is shown.

## Architecture

```
resumeTailor/
  backend/
    app.py        # FastAPI routes: load content, score JD, propose pack, recompile, export
    models.py     # pydantic schemas (Profile, Job, Project, Bullet, Settings)
    content.py    # YAML load + validation
    scoring.py    # deterministic JD parse, alias expansion, weighting, baseline scores
    llm.py        # Ollama client: JD skill extraction + semantic bullet re-rank (scores only), caching, fallback
    packer.py     # bundle-greedy auto-pack + breadth tie-break + pins/excludes/caps
    render.py     # Jinja2 -> .tex, Tectonic compile, one-page fit probe
    archive.py    # write dated export folder
  content/
    profile.yaml          # header, education, awards, static skills, settings
    aliases.yaml          # canonical skill -> [variants]
    experience/*.yaml     # one file per job
    projects/*.yaml       # one file per project
  templates/
    resume.tex.j2         # Jinja2 mapped to the LaTeX macros (built in step 1)
    *.cls / *.sty         # template assets
  frontend/
    index.html, app.js, styles.css
  archive/                # auto-written exports
  requirements.txt
  README.md
```

## Data model (YAML)

`profile.yaml` — header fields, education list, awards list, static skills block, and `settings`:
`page_target: 1`, `closeness_threshold: 0.10`, `min_bullets_per_open_project: 2`, `max_bullets_per_item: 4`, plus an `llm` block: `enabled: true`, `base_url: http://localhost:11434/v1`, `model: qwen3:8b`, `blend_weight: 0.6` (LLM vs deterministic), `thinking: false`.

Each `experience/*.yaml` and `projects/*.yaml`:
```yaml
id: proj-foo
name: "Foo"            # projects; jobs use company/role/dates/location
link: "https://..."
fixed_bullet: "Built X with React/Node/Postgres to do Y …"   # always shown when item appears
bullets:
  - { id: foo-1, text: "Verbatim bullet …", tags: [react, caching, performance], tier: strong }
  - { id: foo-2, text: "…", tags: [postgres, indexing], tier: optional }
tags: [react, node, postgres]   # optional item-level tags
```
- `text` is treated as **raw LaTeX** (authored by hand; supports `\textbf{}` etc.) — no auto-escaping; documented in README.
- `tier` (must / strong / optional) is the static override lever feeding the packer.

## Scoring (`scoring.py` + `llm.py`)

**Stage A — deterministic baseline (`scoring.py`), always runs:**
1. Tokenize JD; detect section headings (Requirements/Responsibilities/Qualifications vs. nice-to-have) via regex.
2. Expand JD terms and bullet tags through `aliases.yaml` to canonical skills.
3. Per matched skill weight = `base × section_weight × frequency_factor`; bullet baseline score = Σ weights of matched tags; project score = aggregate of bullets + item-level tags.
4. Fully deterministic; doubles as the fallback when the model is off/unreachable, and **shortlists** candidates for the LLM.

**Stage B — local LLM layer (`llm.py`), when `llm.enabled`:**
1. **JD extraction**: one Ollama call returns a JSON list of required skills/competencies with importance weights (handles paraphrase/implicit requirements the alias map misses). Feeds back into Stage A weighting and the shortlist.
2. **Semantic re-rank**: a single batched call passes the extracted requirements + the shortlisted bullets' *text* and asks for a JSON array of `{bullet_id, relevance_0_1, matched_requirements}`. The prompt is strictly read-and-score — the model is instructed never to emit bullet text; output is JSON-parsed and anything off-schema is discarded.
3. **Blend**: final score = `blend_weight × llm_score + (1 − blend_weight) × normalized_baseline`. This single number feeds the packer (which is unchanged).

**Determinism & safety:**
- Temperature 0, fixed seed, `thinking: false` for speed; results **cached by `(jd_hash, content_hash, model)`** so re-runs of the same JD are reproducible.
- Ollama down or malformed JSON ⇒ log + **fall back to baseline-only**; the app never blocks on the model.
- The model reads text and emits numbers/labels only; a verification test asserts the rendered PDF's bullet text is byte-identical to the source YAML (no model can mutate content).

## Auto-packer (`packer.py`)

- Place all **locked** content into the page budget first.
- Build candidate **bundles**: opening a project costs its fixed-first-bullet height (≈0 score) but unlocks its scored secondary bullets; each additional bullet (project or experience) is an add-on unit with score + height.
- Greedy by **score density** (score per estimated height). **Breadth tie-break**: when two candidates' densities differ by ≤ `closeness_threshold`, prefer the one that opens a new project or adds to the least-developed item.
- Honor **pins** (force include/open), **excludes** (remove), `min_bullets_per_open_project` (don't open a project unless it gets ≥2 bullets, unless pinned), and `max_bullets_per_item`.
- **Verify** against a real compile + fit probe; if overflow, drop the lowest-density unit; if room remains, continue. Cap compiles per proposal (~3–8). Optionally pre-estimate line heights to reduce compile count.

## Page-fit probe (`render.py`)

- Render `resume.tex.j2` with the chosen selection via Jinja2 → `.tex`; compile with Tectonic.
- **Fits?** Count PDF pages (`pypdf`); >1 page ⇒ overflow.
- **Room left?** Use `zref-savepos`: place `\zsavepos{end}` after content, read the end y-position from the `.aux`, compare to the text-area bounds ⇒ exact "fits, X cm to spare" / "overflow by N." Expose this as the UI fit gauge.
- Tectonic's first compile fetches packages (one-time, cached); subsequent compiles are ~0.5–2s.

## Frontend (lean)

Single page, vanilla JS + `fetch` (no heavy framework for v1):
- **JD pane**: textarea + "Score" button.
- **Library** (left): bullets grouped by job/project, each showing score, tier badge, and **pin / exclude** toggles; a grep-style **search box** filtering on text + tags.
- **Proposal** (center): the auto-packed selection, with toggles to add/remove and re-pack.
- **Preview** (right): the compiled PDF (`<iframe>`/PDF.js) + the **fit gauge**.
- **Export** button → triggers archive write.

## Archive on export (`archive.py`)

Write `archive/<YYYY-MM-DD>-<company-slug>/` containing `resume.pdf`, `resume.tex`, `jd.txt`, and `selection.json` (full selection + pins + scores) for reproducibility and later reuse.

## Implementation steps

1. **Ingest the LaTeX template** (`.tex` + any `.cls`/`.sty`): identify header/section/bullet macros, build `resume.tex.j2`, and confirm Tectonic compiles a hardcoded sample to a clean one-page PDF. Implement the `zref-savepos` fit probe. *(Blocked on dropping the template into the repo.)*
2. **Content model**: pydantic schemas + YAML loader/validation; seed `content/` from the real resume (jobs, projects, fixed first bullets, tags, tiers) + `aliases.yaml`.
3. **Deterministic scoring** module + unit tests (alias expansion, weighting, shortlist).
4. **LLM layer** (`llm.py`): Ollama client, JD-extraction + re-rank prompts, JSON parsing, caching, and fallback; verify against a running `qwen3:8b` and with the endpoint stopped (fallback path).
5. **Packer** + unit tests with a stub height function, then wire to real compile-verify.
6. **FastAPI routes**: load content, score JD (baseline + LLM blend), propose pack, recompile on toggle, export+archive.
7. **Frontend** UI wiring all of the above (show blended score + which requirements each bullet matched).
8. **End-to-end pass** with a real JD; tune `closeness_threshold`, weighting, and `blend_weight`.

## Verification

- **Unit**: scorer (alias/weight correctness), packer (respects pins/excludes/caps/min-open; breadth tie-break fires within threshold), YAML validation.
- **LLM layer**: JSON-parse robustness, fallback to baseline when Ollama is unreachable, score caching/reproducibility, and a **content-immutability** check (rendered PDF bullet text == source YAML — proves no model rewrote anything).
- **Integration**: real template + seeded content + a sample JD ⇒ assert one-page PDF, all locked sections present, every shown item's fixed first bullet present, pins honored, archive folder written with all four artifacts.
- **Manual e2e**: launch app, paste a JD, confirm the proposal fills the page without overflow, toggle pins/excludes and watch the fit gauge + re-pack, export, inspect the archive.

## Open items / assumptions

- **Needs the actual LaTeX template** before step 1 can complete; everything downstream maps to its macros.
- **Requires Ollama running** with `qwen3:8b` pulled for the LLM layer; the app degrades to deterministic scoring if it's not. Only a lightweight HTTP client (`httpx`) is added — no in-process ML libraries, since the model runs in Ollama.
- Single bullet pool (no separate "profiles"): different JDs naturally surface different bullets via scoring.
- In-app content CRUD, URL/PDF JD ingestion, and section weighting tuning UI are explicitly **out of scope for v1** (YAML-managed, paste-only), and can be added later.

## Build phases (checkpointed)

Work proceeds in major phases; **stop and wait for instruction after each**:
0. **Scaffolding** — `plan.md` + `CLAUDE.md`, repo init, first push. *(current)*
1. Template ingestion + render/fit probe.
2. Content model + seed data.
3. Deterministic scoring.
4. LLM layer.
5. Packer.
6. FastAPI routes.
7. Frontend.
8. End-to-end + tuning.

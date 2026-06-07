# Postgame LLM Narrative Layer

**Status:** Proposed design (2026-06-07), pending review. Brainstormed with the user; all four product decisions (focus, generation method, language, length) are settled below.

**Goal:** Replace the dry, score-listing postgame output with a single **Korean storytelling paragraph** that frames the game as the tension between the *model's prediction* and the *actual result*. The deterministic facts (performance scores, labels, slot verdicts, gap) stay exactly as they are; we add a narrative layer that turns those facts into prose.

**Architecture:** The narrative is **presentation, not scoring**. A new, self-contained `app/postgame/narrative/` package (mirroring the existing `app/lineup_model/batting_order/` LLM package) takes the already-computed `PostgameReviewBreakdown` facts and produces a Korean paragraph. An LLM produces the rich story when enabled; on disablement, failure, or timeout it falls back to a **deterministic Korean skeleton** built from the same facts. The narrative is generated once at postgame-pipeline time and persisted, never recomputed at view time.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy + Alembic, pytest, uv (run from `apps/api`). Reuses the existing `openai` client wrapper. Next.js 16 web for the surfaced field.

---

## Decisions (from brainstorming, locked)

| Dimension | Decision |
|---|---|
| Narrative focus | **Comprehensive** — game recap framed around the model-prediction-vs-reality tension |
| Generation | **LLM narrative layer**, reusing the existing provider infra; deterministic fallback |
| Language | **Korean** (the deterministic fallback skeleton is also Korean, for language consistency) |
| Length | **One paragraph** (~4–6 sentences): setup → tension → result |

---

## Why the current output feels dry (the defect)

`app/postgame/review_generator.py` `_generate_summary_text` selects one of **four hardcoded English templates** keyed only on the gap label and the over/under-performer counts (e.g. *"The actual lineup was close to optimal and performed within expectation."*). The underlying data is rich — who over/under-performed with box lines, per-slot model-pick-vs-actual verdicts, the final score — but the prose collapses it to one of four generic sentences. That is the "딱딱함" the user wants gone.

## Architecture invariant (load-bearing)

`apps/api/app/lineup_model/` scoring and the postgame **deterministic** layer must stay deterministic, and the postgame `output_hash` must remain reproducible for identical inputs. Therefore:

- The narrative is **never part of `output_hash`**. `generate_review_for_run` computes `run.output_hash = _output_hash(breakdown.key_insights_json)`; the narrative is a separate field and is naturally excluded. We do **not** add it to `key_insights_json`.
- All scoring/labels/verdicts in `PostgameReviewBreakdown` are unchanged. The narrative is a pure function of those already-computed facts (plus, when enabled, a best-effort LLM call) — exactly the additive, best-effort posture of the batting-order LLM layer.
- This matches the documented exception in `CLAUDE.md`: once an LLM layer is on, that artifact is "an audit fingerprint, not an idempotency key." The narrative is best-effort; the deterministic review remains the source of truth.

## Components

### 1. `app/postgame/narrative/` package (new)

Mirrors `app/lineup_model/batting_order/`:

- `types.py` — `NarrativeFacts` (a frozen, serialisable view of the breakdown the narrative needs: gap label + scores, over/under-performers with names and key box-line stats, difference reviews (model pick vs actual, who won), final score) and a `NarrativeProvider` Protocol.
- `skeleton.py` — `build_skeleton(facts: NarrativeFacts) -> str`: the **deterministic Korean** paragraph. Pure, total, no I/O. Composes setup → tension → result from the facts (richer than today's 4 templates because it interpolates the actual names/numbers). This is both the fallback and the testable core.
- `prompt.py` — static system prompt + `build_user_prompt(facts) -> str`. Instructs: one Korean paragraph, setup (what the model expected) → tension (how the actual choices differed) → result (who proved or disproved the model), **using only the supplied facts** (no invented stats), analytical-neutral tone.
- `schema.py` — forced JSON schema `{"narrative": string}` for `response_format`.
- `provider.py` — `OpenAINarrativeProvider` (may reuse the generic `OpenAIProvider.complete(system, user, schema)` already in `lineup_model/batting_order/provider.py`) and `build_narrative_provider() -> NarrativeProvider | None`.
- `generator.py` — `generate_narrative(facts, provider) -> tuple[str, str]` returning `(narrative_text, source)` where `source ∈ {"llm", "skeleton"}`. Calls the provider, validates a non-empty string, and on **any** exception/timeout/empty result returns `(build_skeleton(facts), "skeleton")`. Never raises.

### 2. Config (dedicated flag — independent of the batting-order LLM)

Read in `build_narrative_provider()`:

- `POSTGAME_NARRATIVE_ENABLED` (default `false`) — gates the LLM path. Off → `build_narrative_provider()` returns `None` → skeleton.
- `OPENAI_API_KEY` — shared with the existing layer; absent → `None` → skeleton.
- `POSTGAME_NARRATIVE_MODEL` (default `gpt-5.5`, matching the canary's batting-order model).
- `POSTGAME_NARRATIVE_TIMEOUT_S` (default `60`, matching the canary's reasoning-model headroom).

Added to `apps/api/.env.example`. The `ingestion-canary` workflow sets `POSTGAME_NARRATIVE_ENABLED=true` so canary-persisted reviews carry the LLM narrative (consistent with it already enabling the batting-order LLM).

### 3. Persistence

- New nullable column `narrative` on `postgame_review_summaries` (Alembic migration, next revision after the current head).
- `generate_review_for_run` (`services/postgame_reviews.py`): after `breakdown` is built, assemble `NarrativeFacts` from it + the player-name map, call `generate_narrative(facts, build_narrative_provider())`, store the text in `PostgameReviewSummary.narrative`, and record `narrative_source` in the run's `model_config_json` (analogous to the existing `batting_order_source` marker). `output_hash` is unchanged.

### 4. API + schema

- Add `narrative: str` to `PostgameResponse` (`schemas/postgame.py`). It is **always populated** (LLM story or Korean skeleton), so the client never has to branch on null.
- `summary_text` is **retained** unchanged for backward compatibility (existing English deterministic summary).
- `build_postgame_view` maps the persisted `narrative` into the response; if a pre-existing review row has `NULL` narrative (rows written before this change), fall back to `build_skeleton` computed on the fly from the stored breakdown facts (or to `summary_text` if facts are unavailable) so old rows still render.

### 5. Web (`apps/web`)

The postgame page surfaces `narrative` as the headline story block (prominent), with the existing per-player and difference-review tables kept below as the supporting detail. `summary_text` is no longer the headline.

## Data flow

```
box score + eval run (persisted facts)
  → generate_postgame_review()           # deterministic breakdown (UNCHANGED)
  → NarrativeFacts(breakdown, names)
  → generate_narrative(facts, provider)  # LLM if enabled, else skeleton — never raises
       provider = build_narrative_provider()  # None unless POSTGAME_NARRATIVE_ENABLED + key
  → PostgameReviewSummary{ summary_text (old), narrative (new) }
  → output_hash = hash(key_insights_json)  # narrative excluded
```

## Failure handling

Every failure mode resolves to the deterministic Korean skeleton, and the postgame pipeline never fails because of the narrative:

| Condition | Result |
|---|---|
| `POSTGAME_NARRATIVE_ENABLED` unset/false | skeleton, `source="skeleton"` |
| `OPENAI_API_KEY` missing | skeleton |
| Provider raises / times out / returns empty or non-string | skeleton (caught in `generate_narrative`) |
| LLM returns valid narrative | `source="llm"` |

## Testing (TDD)

- `build_skeleton` (pure): given representative `NarrativeFacts` (model-better, actual-better, blowout, off-day-ish), asserts the Korean paragraph names the right players/verdicts and follows setup→tension→result. Deterministic, no I/O.
- `generate_narrative`: with a fake provider returning a story → `("…","llm")`; with a fake provider that raises / returns `""` / returns non-string → falls back to skeleton; provider `None` → skeleton. No real OpenAI calls.
- `build_narrative_provider`: env matrix (flag off, flag on without key, flag on with key) → `None`/`None`/provider.
- Integration (`generate_review_for_run`): provider off → `summary.narrative == build_skeleton(...)`, `narrative_source="skeleton"`, `output_hash` identical to pre-change baseline for the same input (proves the narrative does not perturb the hash); fake provider on → `narrative_source="llm"`, narrative persisted, `output_hash` still identical.
- Schema/view: `PostgameResponse.narrative` is always present; an old summary row with `NULL` narrative renders via the on-the-fly skeleton fallback.

## Scope / YAGNI

- One Korean paragraph only — no multi-length variants, no per-player mini-stories, no English narrative field.
- No new data source; uses only facts already in `PostgameReviewBreakdown`.
- `summary_text` is kept as-is (no migration of its content); only a new field is added.
- Reuse the existing `OpenAIProvider.complete` rather than a second OpenAI wrapper if its interface fits.

## Harness impact

Update `CLAUDE.md` (postgame narrative is a second best-effort LLM artifact; note the canary enables it) and `apps/api/.env.example` (new env vars). Re-run `/harness-audit` before the PR. No new paths/commands that the structural drift checker tracks beyond the env template.

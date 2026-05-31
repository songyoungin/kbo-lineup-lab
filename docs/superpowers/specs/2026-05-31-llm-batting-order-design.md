# LLM-Driven Batting Order + Korean Explanations — Design

- Date: 2026-05-31
- Status: Proposed
- Author: brainstormed with Claude
- Scope: One implementation plan

## 1. Summary

Keep the deterministic sabermetric engine as the source of truth for
**player scoring** and **defensive position assignment**, but replace the
rule-based **batting-order assignment** with an LLM (OpenAI) that arranges the
nine already-selected players to **maximize team run production** and writes
the supporting explanations in **Korean**.

The lineup score continues to be computed deterministically by
`compute_lineup_score` over whatever order the LLM returns, so the displayed
number stays grounded and comparable. The existing rule-based order
(`_assign_batting_order`) is preserved as a fallback path.

## 2. Motivation

The current rule-based order has a real defect: in `_assign_batting_order`
(`recommendation.py`), slot 1 is filled first by highest OBP and the chosen
player is removed from the pool. The best overall hitter (who usually leads OBP)
is therefore "used up" at leadoff before the cleanup slots (3/4) are filled —
e.g. the team's best bat lands at #1 instead of #3/#4. Batting order is exactly
the kind of fuzzy, convention-driven, low-arithmetic decision where an LLM adds
value, whereas precise arithmetic (the scores) is where rules are superior.

Expectation setting: optimal vs reasonable batting orders differ by very little
in actual run expectancy. The value of this feature is **explainability and
context-fit** ("why this order"), not a measurable jump in win rate.

## 3. Decisions (from brainstorming)

| Topic | Decision |
| --- | --- |
| Player scoring | Deterministic, unchanged |
| Position assignment | Deterministic, unchanged (recommendation by Claude) |
| Batting order | LLM decides, free to adapt to situation |
| Objective | **Maximize team run production** (OBP table-setters ahead of producers) |
| Provider | OpenAI (single provider; abstracted behind a Protocol) |
| Determinism | Generate once per game run, persist, never regenerate; `temperature=0` (+ `seed`) |
| Explanation scope | Lineup summary (Korean) + per-slot rationale (Korean) |
| Fallback | On any LLM failure, fall back to deterministic `_assign_batting_order` |
| Integration approach | **A** — LLM only reorders the nine pre-selected players |

## 4. Architecture & Components

LLM is an **additive layer**; existing deterministic code is preserved as the
fallback.

```
app/lineup_model/
├── recommendation.py        (modified) split selection from ordering
│     ├── select_and_assign_positions()  ← extracted greedy fill loop (deterministic)
│     ├── _assign_batting_order()        ← preserved (= fallback path)
│     └── generate_recommendation()      ← now: select positions → order → score
├── player_score.py          (unchanged)
├── lineup_score.py          (unchanged)
└── batting_order/           (new) LLM ordering layer
      ├── types.py     BattingOrderResult(slots, rationale_ko_by_player, summary_ko, source)
      ├── schema.py    OpenAI structured-output JSON schema + validation helpers
      ├── prompt.py    static prefix (role, objective, rules, few-shot) + dynamic (9 players)
      ├── provider.py  BattingOrderProvider Protocol + OpenAIProvider
      └── orderer.py   orchestration: build → call → validate → retry×1 → fallback
```

- **`provider.py`** — `BattingOrderProvider` is a `Protocol` with a single method
  that takes a prompt payload and returns raw structured output. `OpenAIProvider`
  implements it via the `openai` SDK. Tests inject a fake provider.
- **`orderer.py`** — the only public entry point:
  `order(assigned, stats_by_player, opp_handedness, context) -> BattingOrderResult`.
  It owns validation, the single retry, and the deterministic fallback, so the
  caller (`lineup_evaluator`) stays unaware of LLM mechanics.
- **Integration** — in `lineup_evaluator.evaluate_lineup_for_run`, replace the
  single `generate_recommendation(...)` call with: `select_and_assign_positions`
  → `orderer.order` → `compute_lineup_score`.

Each unit has a single responsibility and is independently testable. If the LLM
is unavailable, a valid lineup is still produced.

## 5. Data Flow

1. `daily_pipeline` → `evaluate_lineup_for_run(run)` (runs once per run).
2. `build_hitter_stats` → `eligible: list[HitterStats]`.
3. `select_and_assign_positions(eligible, opp_handedness)` →
   `dict[Position, HitterStats]` (deterministic; 9 players + positions fixed).
4. `orderer.order(assigned, stats_by_player, opp_handedness, context)` →
   `BattingOrderResult{ slots, per-slot rationale_ko, summary_ko, source }`
   (LLM, `temperature=0`; falls back to `_assign_batting_order` on failure).
5. `compute_lineup_score(slots, stats_by_player, opp_handedness)` → breakdown
   (deterministic score over the LLM-chosen order).
6. Persist: per-slot `rationale_ko` → `RecommendedLineupRow.rationale`;
   `summary_ko` → `LineupEvaluationSummary.summary_text`; scores →
   `key_insights_json`; `output_hash` computed from the final slots (as today).

## 6. LLM Input/Output Contract

### Prompt structure (static prefix first, for prompt caching)

**System / static prefix** (cacheable, identical across games):
- Role: KBO batting-order analyst.
- Objective: maximize **team run production** — place high-OBP table-setters
  in slots 1–2 so high-SLG/run-producing hitters bat with runners on base;
  cleanup hitters (the best overall bats) belong at 3–4, not leadoff.
- Hard rules: use **exactly** the nine provided `player_id`s, each once; assign
  `batting_order` as a permutation of 1..9; do not invent players or stats.
- Output: must conform to the JSON schema (below).
- Few-shot: 1–2 worked examples demonstrating the OBP-ahead-of-SLG logic and a
  Korean rationale style.

**User / dynamic payload** (varies per game):
- Opposing starter handedness.
- Optional context (park, notable notes) — empty for MVP.
- For each of the nine players: `player_id`, name, assigned `position`,
  handedness, season OBP/SLG/OPS, recent 14d/30d OPS, vs-LHP/RHP OPS + PA, and
  the deterministic `total_score`.

### Output schema (OpenAI structured output, `strict: true`)

```json
{
  "batting_order": [
    { "batting_order": 1, "player_id": 123, "rationale_ko": "출루율이 가장 높아 1번 적합" }
    // ... exactly 9 entries
  ],
  "lineup_summary_ko": "이 타순은 ..."
}
```

### Validation (in `orderer.py`)

- `batting_order` has exactly 9 entries.
- The set of `player_id`s equals the nine assigned players.
- The `batting_order` values are a permutation of 1..9.
- All Korean text fields are non-empty.

On validation failure: retry once. If it still fails → fallback.

## 7. Determinism, Persistence, Idempotency

- Call with `temperature=0` and a fixed `seed` (best-effort; OpenAI does not
  guarantee bit-identical output, hence persistence below).
- The recommendation (order + Korean texts) is generated **once** inside
  `evaluate_lineup_for_run` and persisted to the DB.
- Postgame reads the persisted `RecommendedLineupRow` / summary; it never calls
  the LLM. `output_hash` is computed from the final persisted slots, exactly as
  today, preserving idempotency tracking.
- Re-evaluation of an already-evaluated run reuses persisted rows (matching the
  existing duplicate-avoidance behavior noted in `lineup_evaluator`); it does
  not re-call the LLM unless the run is explicitly recomputed.

## 8. Error Handling & Fallback

`source` on `BattingOrderResult` records the path taken: `"llm"` or
`"fallback"`.

Fallback triggers (→ deterministic `_assign_batting_order`, Korean texts fall
back to a templated string):
- LLM feature disabled, or `OPENAI_API_KEY` missing.
- Network error / timeout.
- Invalid or non-conforming output after one retry.

Every fallback logs a warning with the reason. A lineup is always produced.

## 9. Configuration

Follow the existing `os.environ.get` pattern (no Settings class in the repo):

| Env var | Meaning | Default |
| --- | --- | --- |
| `LINEUP_LLM_ENABLED` | Master toggle for the LLM ordering path | `false` |
| `OPENAI_API_KEY` | OpenAI credential (never hardcoded) | — (required when enabled) |
| `LINEUP_LLM_MODEL` | OpenAI model id | configurable (e.g. `gpt-4.1`) |
| `LINEUP_LLM_TIMEOUT_S` | Per-call timeout (seconds) | `20` |

When `LINEUP_LLM_ENABLED` is false or the key is absent, the system behaves
exactly as today (deterministic order), so the feature can ship dark and be
flipped on per environment. The model id used is recorded in
`LineupEvaluationRun.model_config_json` for traceability.

## 10. Persistence Mapping

| Data | Column | Notes |
| --- | --- | --- |
| Per-slot Korean rationale | `RecommendedLineupRow.rationale` | Replaces the current concatenated deterministic string |
| Deterministic score | `RecommendedLineupRow.score` | Unchanged (still from `compute_player_score`) |
| Lineup Korean summary | `LineupEvaluationSummary.summary_text` | Now Korean prose instead of the English template |
| Scores / gaps | `LineupEvaluationSummary.key_insights_json` | Unchanged; deterministic component scores may be added here for transparency |
| LLM model + source | `LineupEvaluationRun.model_config_json` | e.g. `{ "batting_order_source": "llm", "llm_model": "gpt-4.1" }` |

No schema migration required — existing columns absorb the new content.

## 11. Testing Strategy

- **Provider injection**: `orderer.order` accepts a `BattingOrderProvider`;
  tests pass a fake returning canned JSON. No real API calls in the test suite.
- `orderer` tests: valid output → correct slots; invalid output (missing player,
  duplicate order, wrong count) → retry then deterministic fallback; disabled
  flag → fallback.
- `select_and_assign_positions` test: deterministic 9-player + position output
  (extraction must not change current behavior).
- Pipeline test: `evaluate_lineup_for_run` with a fake provider persists Korean
  `rationale` + `summary_text` and a stable `output_hash`.
- Existing `player_score` / `lineup_score` / `test_recommendation` tests stay
  green (fallback path must reproduce today's deterministic order).
- Tests are functions (not classes); mocks via decorators; Korean docstrings per
  repo conventions.

## 12. Out of Scope (Future)

- LLM choosing **which** players start or their positions (approach B).
- Engine-generated candidate orders with LLM as re-ranker (approach C).
- Multi-provider support (Gemini); the Protocol leaves room for it.
- Rich game context (park factors, weather, bullpen) in the prompt.
- Rewriting individual player-score notes into Korean prose.

## 13. Risks

- **Non-determinism**: mitigated by generate-once + persist + `temperature=0`.
- **Hallucinated/invalid lineups**: mitigated by strict schema + validation +
  fallback.
- **Cost/latency**: one call per game; prompt caching helps when the daily
  pipeline batches games (static prefix reused within the cache TTL).
- **Over-expectation**: documented — value is explainability, not win rate.

# Lineup Simulator (Phase 3 of Visual/Entertainment series) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user drag-reorder the recommended batting order and instantly see the deterministic Markov engine recompute expected runs and the delta vs the model's recommended order — an interactive "내가 감독" 타순 시뮬레이터.

**Architecture:** A new read-only `POST /api/games/{game_id}/lineup-score` endpoint reuses the *existing* deterministic `compute_lineup_score` (the analytic Markov run-expectancy model) to score any permutation of the recommended lineup. It validates the submitted player ids are a permutation of that game's recommended lineup, rebuilds each player's `HitterStats` from the run's stat snapshot, and returns the custom order's expected runs plus a **self-contained** delta against a freshly-recomputed recommended baseline (both on the same raw scale). The web adds a dependency-free native-HTML5 drag-to-reorder list that re-posts on each drop and shows a live expected-runs gauge.

**Tech Stack:** FastAPI + Pydantic v2 (frozen models), SQLAlchemy 2.x, pytest (in-memory SQLite + TestClient); Next.js 16 (webpack dev), React client component, Tailwind v4, native HTML5 drag-and-drop (no DnD library).

## Global Constraints

- **Architecture invariant (binding):** the deterministic scoring/Markov layer in `apps/api/app/lineup_model/` is the source of truth and must stay deterministic. This feature is **additive and read-only**: it must not modify `lineup_score.py`, the Markov model, the recommendation/optimizer code, persisted `RecommendedLineupRow.score`, or any `output_hash`. It only *calls* `compute_lineup_score` with a user-supplied order.
- **Self-contained scale (binding):** the persisted `recommended_total_score` (and the pregame hero number) has the opponent-pitcher matchup *multiplier* applied (`recommendation` headline = `recommended.total_score * mult`), whereas raw `compute_lineup_score` is un-multiplied. The simulator must compute BOTH its baseline (recommended order) and the custom order with raw `compute_lineup_score` (no multiplier) so the delta is on one consistent scale. The endpoint returns its OWN recommended baseline; the UI must NOT compare the simulator number against the pregame hero number. (The multiplier scales every ordering equally, so the ranking/delta of orderings is identical with or without it — raw is correct for a what-if reorder tool.)
- **Determinism:** given the same completed run and the same submitted order, the response is fully reproducible (`compute_lineup_score` is deterministic; no clock/random).
- **No new runtime dependency:** the drag-to-reorder list uses native HTML5 drag-and-drop (`draggable`, `onDragStart`/`onDragOver`/`onDrop`). No dnd-kit/react-beautiful-dnd. Provide up/down move buttons as an accessible, touch-friendly fallback.
- **Frozen Pydantic models** for new schemas (`model_config = ConfigDict(frozen=True)`).
- **English** for code, comments, docs, commit messages; **Korean** only in user-facing UI strings.
- **No JS unit-test harness** in `apps/web` (verified: no vitest/jest); the web verification gate is `npm run build` + `pre-commit run --all-files` + manual smoke (established Phase 1–2 pattern). Backend tasks use full pytest TDD.
- **Next.js 16 caveat:** `npm run dev` runs `next dev --webpack`; open the app at `http://localhost:3000`, never `127.0.0.1:3000`.
- **Commit convention:** commitizen, branch `feature/viz-lineup-simulator`. Never commit on `main`; never use the git `-C` flag.
- **Pre-commit only** for lint/type/format (ruff/mypy/bandit/vulture/eslint/prettier/harness-drift). Run `/harness-audit` before opening a PR (it gates `gh pr create`).

---

## File Structure

**Backend (`apps/api`):**
- Modify `app/schemas/pregame.py` — add `LineupScoreRequest`, `LineupScoreResponse`.
- Modify `app/services/pregame_views.py` — add `score_custom_batting_order(...)` + a private `_slots_for_order(...)` helper.
- Modify `app/api/routes/games.py` — add the `POST /{game_id}/lineup-score` route.
- Modify `tests/test_pregame_api.py` — add endpoint tests.

**Frontend (`apps/web`):**
- Modify `lib/types.ts` — add `LineupScoreRequest`, `LineupScoreResponse`.
- Modify `lib/api.ts` — add `api.lineupScore(gameId, playerIds)`.
- Create `components/pregame/lineup-simulator.tsx` — the drag-reorder simulator (client component).
- Modify `app/games/[gameId]/pregame/page.tsx` — mount a "타순 시뮬레이터" section.

---

## Interfaces produced by this plan (consumed by Phase 4)

```
POST /api/games/{game_id}/lineup-score
  body: LineupScoreRequest { player_ids: list[int] }   # a permutation of the recommended lineup's 9 players, in batting order
  -> LineupScoreResponse {
       game_id: int
       expected_runs: float            # raw Markov expected runs of the submitted order (weighted_player_score)
       handedness_adjustment: float    # handedness_balance_adjustment (<= 0)
       total_score: float              # expected_runs + handedness_adjustment
       recommended_total_score: float  # raw baseline: recommended order scored the same way
       delta_vs_recommended: float     # total_score - recommended_total_score
     }
api.lineupScore(gameId: number, playerIds: number[]): Promise<LineupScoreResponse>
<LineupSimulator gameId={number} recommendedLineup={LineupRow[]} />
```

Phase 4 (win-probability distribution) can call this same endpoint to score arbitrary orders for its distribution view.

---

## Task 1: Custom batting-order scoring endpoint

**Files:**
- Modify: `apps/api/app/schemas/pregame.py` (add schemas after `PlayerScoreCardResponse`, before the "Job schemas" section)
- Modify: `apps/api/app/services/pregame_views.py` (add helper + service after `build_player_score_card`)
- Modify: `apps/api/app/api/routes/games.py` (add route + imports)
- Test: `apps/api/tests/test_pregame_api.py` (append tests)

**Interfaces:**
- Consumes (existing, verified):
  - `app.lineup_model.lineup_score.compute_lineup_score(slots: tuple[LineupSlot, ...], stats_by_player: dict[int, HitterStats], opp_handedness: Handedness) -> LineupScoreBreakdown` (returns `.weighted_player_score` = raw expected runs, `.handedness_balance_adjustment`, `.total_score`).
  - `app.lineup_model.types.LineupSlot(batting_order: int, player_id: int, position: Position)`, `Position`, `Handedness`.
  - `app.services.lineup_evaluator.build_hitter_stats`, `_resolve_opp_handedness` (already imported in `pregame_views.py`).
  - `pregame_views.py` helpers `_lookup_team_id`, `_latest_completed_run`; models `Game`, `RecommendedLineupRow`, `PlayerStatSnapshotRow` (already imported).
- Produces: `LineupScoreResponse` (shape above) and the route.

- [ ] **Step 1: Add the request/response schemas**

In `apps/api/app/schemas/pregame.py`, insert immediately before the `# ---- Job schemas ----` section (right after `PlayerScoreCardResponse`):

```python
# ---------------------------------------------------------------------------
# Lineup simulator schemas (score an arbitrary batting order; read-only)
# ---------------------------------------------------------------------------


class LineupScoreRequest(BaseModel):
    """Body for POST /api/games/{game_id}/lineup-score.

    player_ids is the desired batting order (slot 1 first). It must be a
    permutation of the game's recommended-lineup player ids — same nine
    players, any order. Each player keeps the defensive position assigned in
    the recommended lineup.
    """

    player_ids: list[int]


class LineupScoreResponse(BaseModel):
    """Response for POST /api/games/{game_id}/lineup-score.

    All scores are raw run-expectancy values from compute_lineup_score (no
    opponent-pitcher multiplier), so the simulator is internally consistent and
    its baseline may differ slightly from the multiplied pregame headline.
    """

    model_config = ConfigDict(frozen=True)

    game_id: int
    # Raw Markov expected runs of the submitted order
    expected_runs: float
    # Handedness-streak penalty applied to the submitted order (<= 0)
    handedness_adjustment: float
    # expected_runs + handedness_adjustment
    total_score: float
    # Recommended order scored the same raw way (the what-if baseline)
    recommended_total_score: float
    # total_score - recommended_total_score (positive = better than recommended)
    delta_vs_recommended: float
```

- [ ] **Step 2: Write the failing tests**

Append to `apps/api/tests/test_pregame_api.py`:

```python
# ---------------------------------------------------------------------------
# POST /api/games/{id}/lineup-score  (lineup simulator)
# ---------------------------------------------------------------------------


def _recommended_player_ids(client: TestClient, game_id: int) -> list[int]:
    """Read the recommended lineup's player ids in batting order from pregame."""
    resp = client.get(f"/api/games/{game_id}/pregame")
    assert resp.status_code == 200
    rows = sorted(resp.json()["recommended_lineup"], key=lambda r: r["batting_order"])
    return [int(r["player_id"]) for r in rows]


def test_lineup_score_recommended_order_has_zero_delta(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Submitting the recommended order itself yields delta_vs_recommended == 0."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)
    ids = _recommended_player_ids(client, _game_id)

    resp = client.post(f"/api/games/{_game_id}/lineup-score", json={"player_ids": ids})
    assert resp.status_code == 200
    data = resp.json()
    assert data["game_id"] == _game_id
    assert data["delta_vs_recommended"] == pytest.approx(0.0, abs=1e-9)
    assert data["total_score"] == pytest.approx(data["recommended_total_score"], abs=1e-9)
    assert isinstance(data["expected_runs"], float)


def test_lineup_score_reordered_is_scored(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """A reversed order is valid and returns a finite expected-runs score."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)
    ids = _recommended_player_ids(client, _game_id)

    resp = client.post(
        f"/api/games/{_game_id}/lineup-score", json={"player_ids": list(reversed(ids))}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["expected_runs"] > 0.0
    # total_score == expected_runs + handedness_adjustment (adjustment <= 0)
    assert data["total_score"] == pytest.approx(
        data["expected_runs"] + data["handedness_adjustment"], abs=1e-9
    )


def test_lineup_score_rejects_non_permutation(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """A player-id set that is not a permutation of the recommended lineup is 422."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)
    ids = _recommended_player_ids(client, _game_id)

    # Drop one and duplicate another → same length, wrong set
    bad = ids[:-1] + [ids[0]]
    resp = client.post(f"/api/games/{_game_id}/lineup-score", json={"player_ids": bad})
    assert resp.status_code == 422


def test_lineup_score_unknown_game_returns_404(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Scoring an order for a game with no completed run / missing game is 404."""
    resp = client.post("/api/games/999999/lineup-score", json={"player_ids": [1, 2, 3]})
    assert resp.status_code == 404
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pregame_api.py -k lineup_score -v`
Expected: FAIL (404 / route not found / `ImportError` on `score_custom_batting_order`).

- [ ] **Step 4: Implement the service helper + function**

In `apps/api/app/services/pregame_views.py`:

Add imports (near the other `app.lineup_model` imports added in Phase 1):

```python
from app.lineup_model.lineup_score import compute_lineup_score
from app.lineup_model.types import LineupSlot
```

(`Position` and `Handedness` are already imported; `build_hitter_stats` and `_resolve_opp_handedness` are already imported from `lineup_evaluator`.)

Add the schema imports to the existing `from app.schemas.pregame import (...)` group:

```python
    LineupScoreRequest,
    LineupScoreResponse,
```

Append after `build_player_score_card` (end of file):

```python
# ---------------------------------------------------------------------------
# Lineup simulator (score an arbitrary batting order; read-only)
# ---------------------------------------------------------------------------


def _slots_for_order(
    ordered_player_ids: list[int],
    positions_by_player: dict[int, str],
) -> tuple[LineupSlot, ...]:
    """Build batting-order slots (1..N) for an ordered list of player ids.

    Each player keeps its recommended position; an unparseable position string
    degrades to DH (mirrors build_player_score_card's guard).
    """
    slots: list[LineupSlot] = []
    for idx, player_id in enumerate(ordered_player_ids, start=1):
        try:
            position = Position(positions_by_player[player_id])
        except ValueError:
            position = Position.DH
        slots.append(LineupSlot(batting_order=idx, player_id=player_id, position=position))
    return tuple(slots)


def score_custom_batting_order(
    session: Session,
    game_id: int,
    player_ids: list[int],
    *,
    team_id: int | None = None,
) -> LineupScoreResponse:
    """Score a user-supplied batting order via the deterministic Markov model.

    Reuses compute_lineup_score (raw expected runs + handedness penalty) for both
    the submitted order and a freshly-recomputed recommended baseline, so the
    delta is on one consistent scale. Read-only; never persists.

    Args:
        session: SQLAlchemy session.
        game_id: Game whose latest completed run supplies the snapshot.
        player_ids: Desired batting order; must be a permutation of the
            recommended lineup's nine player ids.
        team_id: Team to evaluate; defaults to LG.

    Returns:
        LineupScoreResponse.

    Raises:
        HTTPException: 404 (game/run missing), 422 (not a permutation of the
            recommended lineup).
    """
    if team_id is None:
        team_id = _lookup_team_id(session, "LG")

    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")

    run = _latest_completed_run(session, game_id, team_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No completed evaluation run for game_id={game_id} team_id={team_id}. "
                "Trigger one via POST /api/jobs/replay-evaluation first."
            ),
        )

    rec_rows = (
        session.execute(
            select(RecommendedLineupRow)
            .where(RecommendedLineupRow.evaluation_run_id == run.id)
            .order_by(RecommendedLineupRow.batting_order)
        )
        .scalars()
        .all()
    )
    if not rec_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No recommended lineup for game {game_id}",
        )

    positions_by_player = {r.player_id: r.position for r in rec_rows}
    recommended_ids = [r.player_id for r in rec_rows]

    # The submitted order must be a permutation of the recommended nine players.
    if len(player_ids) != len(recommended_ids) or set(player_ids) != set(recommended_ids):
        raise HTTPException(
            status_code=422,
            detail="player_ids must be a permutation of the recommended lineup's players",
        )

    stat_rows = (
        session.execute(
            select(PlayerStatSnapshotRow).where(
                PlayerStatSnapshotRow.snapshot_id == run.stat_snapshot_id,
                PlayerStatSnapshotRow.player_id.in_(recommended_ids),
            )
        )
        .scalars()
        .all()
    )
    stats_json_by_player: dict[int, dict[str, object]] = {
        r.player_id: r.stats_json for r in stat_rows
    }
    # Enrichment (recent_positions / starts_last_5) is irrelevant here: compute_lineup_score
    # uses only OBP/SLG (expected runs) and handedness+position (penalty), so building stats
    # straight from the snapshot reproduces the engine's lineup score exactly.
    stats_by_player = {
        pid: build_hitter_stats(pid, stats_json_by_player.get(pid, {}), positions_by_player[pid])
        for pid in recommended_ids
    }

    opp_handedness, _ = _resolve_opp_handedness(session, run)

    custom = compute_lineup_score(
        _slots_for_order(player_ids, positions_by_player), stats_by_player, opp_handedness
    )
    baseline = compute_lineup_score(
        _slots_for_order(recommended_ids, positions_by_player), stats_by_player, opp_handedness
    )

    return LineupScoreResponse(
        game_id=game_id,
        expected_runs=custom.weighted_player_score,
        handedness_adjustment=custom.handedness_balance_adjustment,
        total_score=custom.total_score,
        recommended_total_score=baseline.total_score,
        delta_vs_recommended=custom.total_score - baseline.total_score,
    )
```

- [ ] **Step 5: Add the route**

In `apps/api/app/api/routes/games.py`, add the new schemas to the existing `from app.schemas.pregame import (...)` block:

```python
    LineupScoreRequest,
    LineupScoreResponse,
```

Add `score_custom_batting_order` to the `from app.services.pregame_views import (...)` block. Then append the route:

```python
@router.post("/{game_id}/lineup-score", response_model=LineupScoreResponse)
def lineup_score(
    game_id: int,
    req: LineupScoreRequest,
    session: SessionDep,
) -> LineupScoreResponse:
    """Score a user-supplied batting order (simulator) via the deterministic model."""
    return score_custom_batting_order(session, game_id, req.player_ids)
```

- [ ] **Step 6: Run the lineup-score tests to verify they pass**

Run: `uv run pytest tests/test_pregame_api.py -k lineup_score -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the full pregame API suite (no regression)**

Run: `uv run pytest tests/test_pregame_api.py -q`
Expected: PASS (all existing + 4 new).

- [ ] **Step 8: Commit**

```bash
git checkout -b feature/viz-lineup-simulator
git add apps/api/app/schemas/pregame.py apps/api/app/services/pregame_views.py apps/api/app/api/routes/games.py apps/api/tests/test_pregame_api.py
git commit -m "feat(api): add lineup-score endpoint to score arbitrary batting orders"
```

---

## Task 2: Web data layer + drag-reorder simulator component

**Files:**
- Modify: `apps/web/lib/types.ts` (add interfaces near `PlayerScoreCardResponse`)
- Modify: `apps/web/lib/api.ts` (add wrapper + import)
- Create: `apps/web/components/pregame/lineup-simulator.tsx`

**Interfaces:**
- Consumes: Task 1's endpoint; `LineupRow` from `lib/types.ts` (`batting_order`, `position`, `player_id`, `player_name`); `apiPost` from `lib/api.ts`.
- Produces: `api.lineupScore(gameId, playerIds)` and `<LineupSimulator gameId recommendedLineup />`.

- [ ] **Step 1: Add the TypeScript types**

In `apps/web/lib/types.ts`, after the `PlayerScoreCardResponse` interface, add:

```typescript
export interface LineupScoreRequest {
  player_ids: number[];
}

export interface LineupScoreResponse {
  game_id: number;
  expected_runs: number;
  handedness_adjustment: number;
  total_score: number;
  recommended_total_score: number;
  delta_vs_recommended: number;
}
```

- [ ] **Step 2: Add the API wrapper**

In `apps/web/lib/api.ts`, add `LineupScoreRequest` and `LineupScoreResponse` to the `import type { ... } from "./types"` list, then add inside the `api` object (after `lineupComparison`):

```typescript
  lineupScore: (gameId: number, playerIds: number[]) =>
    apiPost<LineupScoreRequest, LineupScoreResponse>(
      `/api/games/${gameId}/lineup-score`,
      { player_ids: playerIds }
    ),
```

- [ ] **Step 3: Create the simulator component**

Create `apps/web/components/pregame/lineup-simulator.tsx`:

```tsx
"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { LineupRow, LineupScoreResponse } from "@/lib/types";

type FetchState =
  | { status: "loading" }
  | { status: "success"; data: LineupScoreResponse }
  | { status: "error"; message: string };

type FetchAction =
  | { type: "loading" }
  | { type: "success"; data: LineupScoreResponse }
  | { type: "error"; message: string };

function reducer(_s: FetchState, a: FetchAction): FetchState {
  switch (a.type) {
    case "loading":
      return { status: "loading" };
    case "success":
      return { status: "success", data: a.data };
    case "error":
      return { status: "error", message: a.message };
  }
}

// Pure: move item at `from` to `to`, returning a new array.
function reorder<T>(list: T[], from: number, to: number): T[] {
  const next = list.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

// Delta colour: positive (better than recommended) = brand; negative = zinc.
function deltaTone(delta: number): string {
  if (delta > 0.0005) return "text-brand-600";
  if (delta < -0.0005) return "text-zinc-400";
  return "text-zinc-500";
}

export function LineupSimulator({
  gameId,
  recommendedLineup,
}: {
  gameId: number;
  recommendedLineup: LineupRow[];
}) {
  // Canonical recommended order (sorted by batting_order), used as the reset baseline.
  const baseline = [...recommendedLineup].sort(
    (a, b) => a.batting_order - b.batting_order
  );
  const [order, setOrder] = useState<LineupRow[]>(baseline);
  const [state, dispatch] = useReducer(reducer, { status: "loading" });
  const dragFrom = useRef<number | null>(null);
  // Monotonic token so a slow earlier response can't overwrite a newer one.
  const reqToken = useRef(0);

  useEffect(() => {
    const token = ++reqToken.current;
    dispatch({ type: "loading" });
    api
      .lineupScore(
        gameId,
        order.map((r) => r.player_id)
      )
      .then((data) => {
        if (token === reqToken.current) dispatch({ type: "success", data });
      })
      .catch((e) => {
        if (token === reqToken.current)
          dispatch({ type: "error", message: (e as Error).message });
      });
  }, [gameId, order]);

  const move = (from: number, to: number) => {
    if (to < 0 || to >= order.length || from === to) return;
    setOrder((cur) => reorder(cur, from, to));
  };

  const isModified = order.some(
    (r, i) => r.player_id !== baseline[i].player_id
  );

  const data = state.status === "success" ? state.data : null;

  return (
    <div className="rounded-md border border-rule bg-surface p-4">
      {/* Headline: live expected runs + delta vs recommended */}
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
            기대 득점
          </p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-ink">
            {data ? data.total_score.toFixed(3) : "—"}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
            추천 대비
          </p>
          <p
            className={`mt-1 text-2xl font-bold tabular-nums ${
              data ? deltaTone(data.delta_vs_recommended) : "text-zinc-400"
            }`}
          >
            {data
              ? `${data.delta_vs_recommended >= 0 ? "+" : ""}${data.delta_vs_recommended.toFixed(3)}`
              : "—"}
          </p>
        </div>
      </div>

      {state.status === "error" && (
        <p className="mb-3 text-xs text-rose-500">
          점수를 불러오지 못했습니다.
        </p>
      )}

      {/* Draggable batting order */}
      <ol className="space-y-1.5">
        {order.map((row, i) => (
          <li
            key={row.player_id}
            draggable
            onDragStart={() => {
              dragFrom.current = i;
            }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (dragFrom.current !== null) move(dragFrom.current, i);
              dragFrom.current = null;
            }}
            className="flex cursor-grab items-center gap-3 rounded-md border border-rule bg-white px-3 py-2 active:cursor-grabbing"
          >
            <span className="w-5 text-center text-sm font-bold tabular-nums text-brand-600">
              {i + 1}
            </span>
            <span aria-hidden className="text-zinc-300">
              ⠿
            </span>
            <span className="flex-1 text-sm font-semibold text-zinc-900">
              {row.player_name}
            </span>
            <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-bold text-zinc-500">
              {row.position}
            </span>
            {/* Accessible / touch fallback for reordering */}
            <span className="flex flex-col">
              <button
                type="button"
                aria-label={`${i + 1}번 타자 위로`}
                disabled={i === 0}
                onClick={() => move(i, i - 1)}
                className="leading-none text-zinc-400 disabled:opacity-30"
              >
                ▲
              </button>
              <button
                type="button"
                aria-label={`${i + 1}번 타자 아래로`}
                disabled={i === order.length - 1}
                onClick={() => move(i, i + 1)}
                className="leading-none text-zinc-400 disabled:opacity-30"
              >
                ▼
              </button>
            </span>
          </li>
        ))}
      </ol>

      <div className="mt-3 flex items-center justify-between">
        <p className="text-[11px] text-zinc-400">
          드래그하거나 ▲▼로 타순을 바꿔 보세요.
        </p>
        <button
          type="button"
          disabled={!isModified}
          onClick={() => setOrder(baseline)}
          className="rounded-md border border-rule px-2.5 py-1 text-xs font-semibold text-zinc-600 disabled:opacity-40"
        >
          추천 타순으로 초기화
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Verify the build compiles**

Run: `cd apps/web && npm run build`
Expected: `✓ Compiled successfully`, TypeScript clean. (Component is exported but not yet mounted — builds fine.)

- [ ] **Step 5: Commit**

```bash
git add apps/web/lib/types.ts apps/web/lib/api.ts apps/web/components/pregame/lineup-simulator.tsx
git commit -m "feat(web): add drag-to-reorder lineup simulator component and api wrapper"
```

---

## Task 3: Mount the simulator on the pregame page

**Files:**
- Modify: `apps/web/app/games/[gameId]/pregame/page.tsx`

**Interfaces:**
- Consumes: `pregame.recommended_lineup` (`LineupRow[]`, already fetched), `gameId` (already in scope), `<LineupSimulator>` (Task 2).

- [ ] **Step 1: Import the component**

In `apps/web/app/games/[gameId]/pregame/page.tsx`, add next to the other pregame imports:

```tsx
import { LineupSimulator } from "@/components/pregame/lineup-simulator";
```

- [ ] **Step 2: Add a simulator section**

Insert a new section immediately AFTER the `{/* 대결 라운드 */}` section (the `<VersusRounds .../>` block) and before the `{/* 선수 비교 패널 */}` section:

```tsx
        {/* 타순 시뮬레이터 */}
        <section className="reveal reveal-3 space-y-3">
          <SectionHeading>타순 시뮬레이터</SectionHeading>
          <LineupSimulator
            gameId={gameId}
            recommendedLineup={pregame.recommended_lineup}
          />
        </section>
```

- [ ] **Step 3: Verify the build compiles**

Run: `cd apps/web && npm run build`
Expected: `✓ Compiled successfully`, TypeScript clean.

- [ ] **Step 4: Manual visual smoke against Supabase dev**

Follow the running-supabase-dev skill (API on :8000; web on **http://localhost:3000**, never 127.0.0.1). Open `http://localhost:3000/games/16/pregame`:
- A "타순 시뮬레이터" section shows the 9 recommended players in batting order, a live "기대 득점" number, and "추천 대비 +0.000".
- Drag a player to a new slot (or click ▲/▼) → the expected-runs number and the "추천 대비" delta update; reordering toward a worse order shows a negative delta, toward a better order a positive (brand-coloured) delta.
- "추천 타순으로 초기화" restores the order and the delta returns to +0.000.
- No console errors (besides any pre-existing browser-extension hydration warning).

- [ ] **Step 5: Run pre-commit across the repo**

Run: `pre-commit run --all-files`
Expected: ruff, mypy, bandit, vulture, eslint, prettier, harness drift all pass.

- [ ] **Step 6: Commit**

```bash
git add "apps/web/app/games/[gameId]/pregame/page.tsx"
git commit -m "feat(web): mount the lineup simulator on the pregame page"
```

---

## Self-Review

**1. Spec coverage:**
- Drag-reorder a batting order → `LineupSimulator` with native HTML5 DnD + ▲▼ fallback (Task 2). ✅
- Live Markov recompute → `POST /lineup-score` reusing `compute_lineup_score` (Task 1); the component re-posts on every reorder (Task 2). ✅
- Delta vs recommended → endpoint returns `delta_vs_recommended` against a raw recomputed baseline (Task 1); displayed live (Task 2). ✅
- Architecture invariant (no engine mutation; read-only) → service only *calls* `compute_lineup_score`, no writes, no `lineup_model/` edits. ✅
- Self-contained scale → both custom and baseline scored with raw `compute_lineup_score`; UI never mixes with the multiplied hero number (Global Constraints + Task 2 shows only the endpoint's own numbers). ✅
- No new dependency → native DnD; no dnd library. ✅

**2. Placeholder scan:** No TBD/TODO; every code step is complete; tests use real fixtures and the existing `_replay_body`/`client`/`_game_id` fixtures; `pytest.approx` is already imported in the test module (it uses `pytest`).

**3. Type consistency:**
- `LineupScoreRequest { player_ids }` / `LineupScoreResponse { game_id, expected_runs, handedness_adjustment, total_score, recommended_total_score, delta_vs_recommended }` identical across Pydantic (Task 1), TS (Task 2), and component usage (Task 2). ✅
- `score_custom_batting_order` name consistent between service, route import, and route call. ✅
- `api.lineupScore(gameId, playerIds)` consistent between definition and component call. ✅
- `_slots_for_order` returns `tuple[LineupSlot, ...]`, matching `compute_lineup_score`'s first parameter. ✅
- Endpoint returns `weighted_player_score` as `expected_runs` and `handedness_balance_adjustment` as `handedness_adjustment` — names verified against `LineupScoreBreakdown` in `app/lineup_model/types.py`. ✅
- `<LineupSimulator gameId recommendedLineup>` prop names match the page's mount (Task 3). ✅

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-22-viz-lineup-simulator.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

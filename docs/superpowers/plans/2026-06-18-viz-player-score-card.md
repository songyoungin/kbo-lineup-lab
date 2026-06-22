# Player Score Card (Phase 1 of Visual/Entertainment series) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a game-card-style player visualization (FIFA-style OVR + 5-axis radar of the model's scoring factors + hot/cold form badge) to the pregame page, backed by a new read-only API endpoint that recomputes the per-factor breakdown from existing snapshot data.

**Architecture:** A new `GET /api/games/{game_id}/players/{player_id}/score-card` endpoint reuses the *existing* deterministic scoring functions (`build_hitter_stats` → `compute_player_score`) to expose the five `ScoringReason` components that today are computed-and-discarded (only the total is persisted). The endpoint adds **display-only** derived values (radar axis scores 0–100, an OVR integer, a hot/cold badge) — it does **not** touch the deterministic scoring layer, the persisted `score`, or any `output_hash`. The web renders the card as a pure inline-SVG pentagon (no new chart dependency).

**Tech Stack:** FastAPI + Pydantic v2 (frozen models), SQLAlchemy 2.x, pytest (in-memory SQLite + TestClient); Next.js 16 (webpack dev), React client component, Tailwind v4, inline SVG.

## Global Constraints

- **Architecture invariant:** the deterministic scoring/position layer in `apps/api/app/lineup_model/` stays the source of truth and must stay deterministic. This feature is **additive and read-only**: it must not modify `player_score.py`, `compute_lineup_score`, persisted `RecommendedLineupRow.score`, or any `output_hash`. All new numbers (axis 0–100, OVR, badge) are presentation-only and derived at request time.
- **English** for code, comments, docs, commit messages. Korean is allowed only in user-facing UI strings (matches existing `player-comparison-panel.tsx`).
- **Determinism of the endpoint:** given the same completed evaluation run, the score-card response is fully reproducible (same `opp_handedness` resolution, same `stats_json`). No randomness, no clock reads.
- **Frozen Pydantic models:** all new response schemas use `model_config = ConfigDict(frozen=True)` to match the file's convention.
- **Commit convention:** commitizen, branch `feature/viz-player-score-card`. Never commit on `main`; never use the git `-C` path flag.
- **Pre-commit only** for lint/type/format (ruff, mypy, bandit, vulture) — never invoke formatters directly. Run `pre-commit run --all-files` before the final commit.
- **No new runtime dependency** on either side (radar is hand-rolled SVG).

---

## File Structure

**Backend (`apps/api`):**
- Modify `app/schemas/pregame.py` — add `PlayerScoreCardFactor` and `PlayerScoreCardResponse` (display schemas).
- Modify `app/services/pregame_views.py` — add `build_player_score_card(session, game_id, player_id, *, team_id=None)` plus small pure helpers `_axis_score` and `_form_badge`.
- Modify `app/api/routes/games.py` — add the route.
- Modify `tests/test_pregame_api.py` — add endpoint + helper tests.

**Frontend (`apps/web`):**
- Modify `lib/types.ts` — add `PlayerScoreCardFactor`, `PlayerScoreCardResponse`.
- Modify `lib/api.ts` — add `api.playerScoreCard(gameId, playerId)`.
- Create `components/pregame/player-score-card.tsx` — the SVG card (client component).
- Modify `app/games/[gameId]/pregame/page.tsx` — mount a row of cards for the recommended lineup.

---

## Interfaces produced by this plan (consumed by Phases 2–4)

The later phases (AI-vs-manager versus screen, lineup simulator, win-probability) reuse this contract, so it is fixed here:

```
GET /api/games/{game_id}/players/{player_id}/score-card
  -> PlayerScoreCardResponse {
       game_id: int
       player_id: int
       player_name: str
       position: str                       # the slot position the card was scored at
       overall: int                        # OVR, 0–99 (display only)
       total_score: float                  # the model composite (same scale as RecommendedLineupRow.score)
       factors: list[PlayerScoreCardFactor]  # exactly 5, fixed order below
       form_badge: "HOT" | "COLD" | "NEUTRAL"
       vs_rhp_ops: float | null
       vs_lhp_ops: float | null
       risp_avg: float | null
     }
  PlayerScoreCardFactor {
       component: "season_offense"|"recent_form"|"matchup"|"position_fit"|"start_rhythm"
       label_ko: str
       raw_value: float                    # the ScoringReason.value (unmodified)
       weight: float                       # the ScoringReason.weight
       axis_score: float                   # 0–100, display-only radar axis
     }
```

Web wrapper: `api.playerScoreCard(gameId: number, playerId: number): Promise<PlayerScoreCardResponse>`.
Web component: `<PlayerScoreCard gameId={number} playerId={number} playerName={string} />`.

---

## Task 1: Score-card API endpoint

**Files:**
- Modify: `apps/api/app/schemas/pregame.py` (add schemas after `PlayerComparisonResponse`, before the "Job schemas" section ~line 259)
- Modify: `apps/api/app/services/pregame_views.py` (add helpers + service function after `build_player_comparison`, ~line 866)
- Modify: `apps/api/app/api/routes/games.py` (add route + import)
- Test: `apps/api/tests/test_pregame_api.py` (add tests at end of file)

**Interfaces:**
- Consumes (existing, verified):
  - `app.services.lineup_evaluator.build_hitter_stats(player_id: int, stats_json: dict[str, object], player_position: str | None = None) -> HitterStats`
  - `app.services.lineup_evaluator._resolve_opp_handedness(session, run) -> tuple[Handedness, str]`
  - `app.lineup_model.player_score.compute_player_score(stats, slot_position, opp_handedness) -> PlayerScoreBreakdown | None`
  - `app.lineup_model.types.Position`, `Handedness`, `ScoringReason`
  - existing helpers in `pregame_views.py`: `_lookup_team_id`, `_latest_completed_run`, `_player_names_bulk`
  - models already imported in `pregame_views.py`: `Game`, `RecommendedLineupRow`, `ActualLineupSnapshotRow`, `PlayerStatSnapshotRow`
- Produces: `PlayerScoreCardResponse` (shape above).

- [ ] **Step 1: Write failing schema/import — add the response schemas**

In `apps/api/app/schemas/pregame.py`, insert after the `PlayerComparisonResponse` class (immediately before the `# Job schemas` comment block):

```python
# ---------------------------------------------------------------------------
# Player score-card schemas (display-only; derived at request time)
# ---------------------------------------------------------------------------


FormBadgeLiteral = Literal["HOT", "COLD", "NEUTRAL"]

ScoreCardComponentLiteral = Literal[
    "season_offense",
    "recent_form",
    "matchup",
    "position_fit",
    "start_rhythm",
]


class PlayerScoreCardFactor(BaseModel):
    """One radar axis: a scoring component with a display-normalized 0–100 score."""

    model_config = ConfigDict(frozen=True)

    component: ScoreCardComponentLiteral
    label_ko: str
    # Raw ScoringReason.value (OPS-space for offense/recent/matchup, [0.6,1.0] for the others)
    raw_value: float
    weight: float
    # Display-only radar axis in [0, 100]; NOT used by the deterministic model.
    axis_score: float


class PlayerScoreCardResponse(BaseModel):
    """Response for GET /api/games/{game_id}/players/{player_id}/score-card.

    All of overall/axis_score/form_badge are presentation-only values derived
    from the deterministic breakdown; they never feed back into scoring.
    """

    model_config = ConfigDict(frozen=True)

    game_id: int
    player_id: int
    player_name: str
    position: str
    # OVR 0–99 (display only)
    overall: int
    # Model composite — same numeric scale as the persisted recommended slot score
    total_score: float
    factors: list[PlayerScoreCardFactor]
    form_badge: FormBadgeLiteral
    vs_rhp_ops: float | None
    vs_lhp_ops: float | None
    risp_avg: float | None
```

- [ ] **Step 2: Write the failing test for the helpers and endpoint**

Append to `apps/api/tests/test_pregame_api.py`:

```python
# ---------------------------------------------------------------------------
# GET /api/games/{id}/players/{player_id}/score-card
# ---------------------------------------------------------------------------


def _recommended_player_id(client: TestClient, game_id: int, batting_order: int) -> int:
    """Read a recommended player_id from the compare endpoint for a slot."""
    resp = client.get(f"/api/games/{game_id}/players/compare?batting_order={batting_order}")
    assert resp.status_code == 200
    return int(resp.json()["recommended"]["player_id"])


def test_score_card_returns_five_factors(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """The score-card returns exactly the five scoring components in fixed order."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)
    player_id = _recommended_player_id(client, _game_id, 1)

    resp = client.get(f"/api/games/{_game_id}/players/{player_id}/score-card")
    assert resp.status_code == 200
    data = resp.json()
    assert data["player_id"] == player_id
    components = [f["component"] for f in data["factors"]]
    assert components == [
        "season_offense",
        "recent_form",
        "matchup",
        "position_fit",
        "start_rhythm",
    ]


def test_score_card_axis_scores_in_range(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Every radar axis_score is clamped to [0, 100] and OVR to [0, 99]."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)
    player_id = _recommended_player_id(client, _game_id, 3)

    resp = client.get(f"/api/games/{_game_id}/players/{player_id}/score-card")
    assert resp.status_code == 200
    data = resp.json()
    assert 0 <= data["overall"] <= 99
    for f in data["factors"]:
        assert 0.0 <= f["axis_score"] <= 100.0
    assert data["form_badge"] in ("HOT", "COLD", "NEUTRAL")


def test_score_card_unknown_player_returns_404(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """A player_id not in this game's lineups yields 404."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/players/99999999/score-card")
    assert resp.status_code == 404


def test_score_card_no_run_returns_404(clean_env: tuple[TestClient, int, int, int]) -> None:
    """With no completed evaluation run, the score-card is 404 (no snapshot to score)."""
    client, game_id, _team_id, _mv_id = clean_env
    resp = client.get(f"/api/games/{game_id}/players/1/score-card")
    assert resp.status_code == 404
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pregame_api.py -k score_card -v`
Expected: FAIL — `404`/`ImportError`/`AttributeError` because the route and service don't exist yet (collection may also fail on the missing `build_player_score_card`).

- [ ] **Step 4: Implement the service helpers and function**

In `apps/api/app/services/pregame_views.py`, update the existing import of the schemas block (the `from app.schemas.pregame import (...)` at ~line 25) to also import the two new names, and add the lineup-evaluator imports. Add to the existing schema import group:

```python
    PlayerScoreCardFactor,
    PlayerScoreCardResponse,
```

Update the lineup_evaluator import (line 44) to:

```python
from app.services.lineup_evaluator import (
    build_hitter_stats,
    compute_actual_lineup_score,
    evaluate_lineup_for_run,
    _resolve_opp_handedness,
)
```

Add the snapshot row import if not already present — `ActualLineupSnapshotRow` and `PlayerStatSnapshotRow` are already imported in the `from app.models.snapshot import (...)` block (verified: used by `build_player_comparison`). Add `compute_player_score` and types:

```python
from app.lineup_model.player_score import compute_player_score
from app.lineup_model.types import Position, ScoringReason
```

(`Handedness` is already imported at line 13.)

Then append after `build_player_comparison` (end of file, ~line 866):

```python
# ---------------------------------------------------------------------------
# Player score card (display-only visualization of the scoring breakdown)
# ---------------------------------------------------------------------------

# Korean labels for each scoring component, keyed by ScoringReason.component.
_COMPONENT_LABEL_KO: dict[str, str] = {
    "season_offense": "시즌 타격",
    "recent_form": "최근 폼",
    "matchup": "상대 매치업",
    "position_fit": "포지션",
    "start_rhythm": "출전 리듬",
}

# Fixed radar order so every card draws the pentagon identically.
_CARD_COMPONENT_ORDER: tuple[str, ...] = (
    "season_offense",
    "recent_form",
    "matchup",
    "position_fit",
    "start_rhythm",
)

# Display normalization bands. Offense/recent/matchup live in OPS space; the
# position/rhythm components are already normalized to [0.6, 1.0].
_OPS_AXIS_LO = 0.500
_OPS_AXIS_HI = 1.100
_NORM_AXIS_LO = 0.600
_NORM_AXIS_HI = 1.000
_OPS_SPACE_COMPONENTS = frozenset({"season_offense", "recent_form", "matchup"})

# Hot/cold threshold: recent-14d OPS this far above/below season OPS flips the badge.
_FORM_BADGE_DELTA = 0.050


def _axis_score(component: str, value: float) -> float:
    """Map a ScoringReason value to a display radar axis in [0, 100].

    OPS-space components use the [0.5, 1.1] band; the already-normalized
    position/rhythm components use their native [0.6, 1.0] band. Display-only.
    """
    if component in _OPS_SPACE_COMPONENTS:
        lo, hi = _OPS_AXIS_LO, _OPS_AXIS_HI
    else:
        lo, hi = _NORM_AXIS_LO, _NORM_AXIS_HI
    pct = (value - lo) / (hi - lo)
    return max(0.0, min(100.0, pct * 100.0))


def _form_badge(recent_14d_ops: float | None, season_ops: float) -> str:
    """Classify hot/cold from recent-14d vs season OPS (display only)."""
    if recent_14d_ops is None:
        return "NEUTRAL"
    if recent_14d_ops >= season_ops + _FORM_BADGE_DELTA:
        return "HOT"
    if recent_14d_ops <= season_ops - _FORM_BADGE_DELTA:
        return "COLD"
    return "NEUTRAL"


def build_player_score_card(
    session: Session,
    game_id: int,
    player_id: int,
    *,
    team_id: int | None = None,
) -> PlayerScoreCardResponse:
    """Assemble the display score card for one player in a game.

    Recomputes the five-factor breakdown via the deterministic scoring path and
    adds presentation-only axis/OVR/badge values. Read-only; never persists.

    Args:
        session: SQLAlchemy session.
        game_id: Game whose latest completed run supplies the snapshot.
        player_id: Player to render. Must appear in the recommended or actual
            lineup of that run (so a valid slot position exists).
        team_id: Team to evaluate; defaults to LG.

    Returns:
        PlayerScoreCardResponse.

    Raises:
        HTTPException: 404 when game, completed run, snapshot row, or the
            player's lineup slot is missing.
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

    # Resolve the slot position: prefer the recommended lineup, fall back to actual.
    position: str | None = None
    rec_slot = (
        session.execute(
            select(RecommendedLineupRow).where(
                RecommendedLineupRow.evaluation_run_id == run.id,
                RecommendedLineupRow.player_id == player_id,
            )
        )
        .scalars()
        .first()
    )
    if rec_slot is not None:
        position = rec_slot.position
    else:
        act_slot = (
            session.execute(
                select(ActualLineupSnapshotRow).where(
                    ActualLineupSnapshotRow.snapshot_id == run.lineup_snapshot_id,
                    ActualLineupSnapshotRow.player_id == player_id,
                )
            )
            .scalars()
            .first()
        )
        if act_slot is not None:
            position = act_slot.position

    if position is None:
        raise HTTPException(
            status_code=404,
            detail=f"Player {player_id} is not in game {game_id} lineups",
        )

    # Load the player's snapshot stats.
    stat_row = (
        session.execute(
            select(PlayerStatSnapshotRow).where(
                PlayerStatSnapshotRow.snapshot_id == run.stat_snapshot_id,
                PlayerStatSnapshotRow.player_id == player_id,
            )
        )
        .scalars()
        .first()
    )
    if stat_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No stat snapshot for player {player_id} in game {game_id}",
        )

    stats = build_hitter_stats(player_id, stat_row.stats_json, position)
    opp_handedness, _ = _resolve_opp_handedness(session, run)

    try:
        slot_position = Position(position)
    except ValueError:
        slot_position = Position.DH

    breakdown = compute_player_score(stats, slot_position, opp_handedness)
    if breakdown is None:
        # Position came from the lineup row, so it should be eligible; guard anyway.
        raise HTTPException(
            status_code=404,
            detail=f"Player {player_id} not eligible at position {position}",
        )

    by_component: dict[str, ScoringReason] = {r.component: r for r in breakdown.reasons}
    factors: list[PlayerScoreCardFactor] = []
    for component in _CARD_COMPONENT_ORDER:
        reason = by_component[component]
        factors.append(
            PlayerScoreCardFactor(
                component=component,  # type: ignore[arg-type]  # constrained by _CARD_COMPONENT_ORDER
                label_ko=_COMPONENT_LABEL_KO[component],
                raw_value=reason.value,
                weight=reason.weight,
                axis_score=_axis_score(component, reason.value),
            )
        )

    overall = int(round(_axis_score("season_offense", breakdown.total_score) * 0.99))

    name_map = _player_names_bulk(session, [player_id])

    return PlayerScoreCardResponse(
        game_id=game_id,
        player_id=player_id,
        player_name=name_map.get(player_id, f"Player({player_id})"),
        position=position,
        overall=overall,
        total_score=breakdown.total_score,
        factors=factors,
        form_badge=_form_badge(stats.recent_14d_ops, stats.ops),  # type: ignore[arg-type]
        vs_rhp_ops=stats.vs_rhp_ops,
        vs_lhp_ops=stats.vs_lhp_ops,
        risp_avg=(float(stat_row.stats_json["risp_avg"])
                  if isinstance(stat_row.stats_json.get("risp_avg"), (int, float)) else None),
    )
```

> Note on OVR: `total_score` is a weighted average that mostly lives in OPS space (~0.6–1.0), so mapping it through the OPS band (`_axis_score("season_offense", ...)`) and scaling by 0.99 yields a 0–99 game-card number. This is presentation-only.

- [ ] **Step 5: Add the route**

In `apps/api/app/api/routes/games.py`, update the schema import and the service import, then add the route. Change line 7 to add the new response model:

```python
from app.schemas.pregame import (
    LineupComparisonResponse,
    PlayerComparisonResponse,
    PlayerScoreCardResponse,
    PregameResponse,
)
```

Change the `from app.services.pregame_views import (...)` block to include `build_player_score_card`:

```python
from app.services.pregame_views import (
    build_lineup_comparison,
    build_player_comparison,
    build_player_score_card,
    build_pregame_view,
)
```

Append the route at the end of the file:

```python
@router.get(
    "/{game_id}/players/{player_id}/score-card",
    response_model=PlayerScoreCardResponse,
)
def player_score_card(game_id: int, player_id: int, session: SessionDep) -> PlayerScoreCardResponse:
    """Return the display score card (radar factors + OVR) for one player."""
    return build_player_score_card(session, game_id, player_id)
```

- [ ] **Step 6: Run the score-card tests to verify they pass**

Run: `uv run pytest tests/test_pregame_api.py -k score_card -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Run the full pregame API suite to verify no regression**

Run: `uv run pytest tests/test_pregame_api.py -q`
Expected: PASS (all existing + 4 new).

- [ ] **Step 8: Commit**

```bash
git checkout -b feature/viz-player-score-card
git add apps/api/app/schemas/pregame.py apps/api/app/services/pregame_views.py apps/api/app/api/routes/games.py apps/api/tests/test_pregame_api.py
git commit -m "feat(api): add player score-card endpoint with display radar factors"
```

---

## Task 2: Web data layer + card component

**Files:**
- Modify: `apps/web/lib/types.ts` (add interfaces near `PlayerComparisonResponse`, ~line 145)
- Modify: `apps/web/lib/api.ts` (add wrapper + import)
- Create: `apps/web/components/pregame/player-score-card.tsx`

**Interfaces:**
- Consumes: `PlayerScoreCardResponse` JSON from Task 1's endpoint.
- Produces: `api.playerScoreCard(gameId, playerId)` and `<PlayerScoreCard gameId playerId playerName />`.

- [ ] **Step 1: Add the TypeScript types**

In `apps/web/lib/types.ts`, after the `PlayerComparisonResponse` interface, add:

```typescript
export type FormBadge = "HOT" | "COLD" | "NEUTRAL";

export type ScoreCardComponent =
  | "season_offense"
  | "recent_form"
  | "matchup"
  | "position_fit"
  | "start_rhythm";

export interface PlayerScoreCardFactor {
  component: ScoreCardComponent;
  label_ko: string;
  raw_value: number;
  weight: number;
  axis_score: number;
}

export interface PlayerScoreCardResponse {
  game_id: number;
  player_id: number;
  player_name: string;
  position: string;
  overall: number;
  total_score: number;
  factors: PlayerScoreCardFactor[];
  form_badge: FormBadge;
  vs_rhp_ops: number | null;
  vs_lhp_ops: number | null;
  risp_avg: number | null;
}
```

- [ ] **Step 2: Add the API wrapper**

In `apps/web/lib/api.ts`, add `PlayerScoreCardResponse` to the `import type { ... } from "./types"` list, then add this wrapper inside the `api` object (after `playerCompare`):

```typescript
  playerScoreCard: (gameId: number, playerId: number) =>
    apiGet<PlayerScoreCardResponse>(
      `/api/games/${gameId}/players/${playerId}/score-card`
    ),
```

- [ ] **Step 3: Create the card component**

Create `apps/web/components/pregame/player-score-card.tsx`:

```tsx
"use client";

import { useEffect, useReducer } from "react";
import { api } from "@/lib/api";
import type { PlayerScoreCardResponse } from "@/lib/types";

type FetchState =
  | { status: "loading" }
  | { status: "success"; data: PlayerScoreCardResponse }
  | { status: "error"; message: string };

type FetchAction =
  | { type: "loading" }
  | { type: "success"; data: PlayerScoreCardResponse }
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

const BADGE_STYLE: Record<string, string> = {
  HOT: "bg-rose-100 text-rose-600",
  COLD: "bg-sky-100 text-sky-600",
  NEUTRAL: "bg-zinc-100 text-zinc-500",
};

const BADGE_LABEL: Record<string, string> = {
  HOT: "🔥 HOT",
  COLD: "🧊 COLD",
  NEUTRAL: "— EVEN",
};

// Pentagon radar over the 5 axes (0–100). Pure SVG, no chart dependency.
function Radar({ values }: { values: number[] }) {
  const size = 132;
  const c = size / 2;
  const r = c - 18;
  const n = values.length; // 5
  const point = (i: number, mag: number) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
    const rad = (mag / 100) * r;
    return [c + rad * Math.cos(angle), c + rad * Math.sin(angle)];
  };
  const grid = [25, 50, 75, 100]
    .map((g) =>
      values
        .map((_, i) => point(i, g).join(","))
        .join(" ")
    );
  const poly = values.map((v, i) => point(i, v).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="h-32 w-32">
      {grid.map((g, i) => (
        <polygon
          key={i}
          points={g}
          className="fill-none stroke-zinc-200"
          strokeWidth={1}
        />
      ))}
      {values.map((_, i) => {
        const [x, y] = point(i, 100);
        return (
          <line
            key={i}
            x1={c}
            y1={c}
            x2={x}
            y2={y}
            className="stroke-zinc-200"
            strokeWidth={1}
          />
        );
      })}
      <polygon
        points={poly}
        className="fill-brand-400/30 stroke-brand-500"
        strokeWidth={2}
      />
    </svg>
  );
}

export function PlayerScoreCard({
  gameId,
  playerId,
  playerName,
}: {
  gameId: number;
  playerId: number;
  playerName: string;
}) {
  const [state, dispatch] = useReducer(reducer, { status: "loading" });

  useEffect(() => {
    let cancelled = false;
    async function run() {
      dispatch({ type: "loading" });
      try {
        const data = await api.playerScoreCard(gameId, playerId);
        if (!cancelled) dispatch({ type: "success", data });
      } catch (e) {
        if (!cancelled)
          dispatch({ type: "error", message: (e as Error).message });
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [gameId, playerId]);

  if (state.status === "loading") {
    return (
      <div className="h-64 w-44 animate-pulse rounded-xl border border-rule bg-surface" />
    );
  }
  if (state.status === "error") {
    return (
      <div className="flex h-64 w-44 items-center justify-center rounded-xl border border-rule bg-surface p-2 text-center text-xs text-rose-500">
        {playerName}
        <br />
        불러오기 실패
      </div>
    );
  }

  const d = state.data;
  return (
    <div className="flex w-44 flex-col items-center gap-1 rounded-xl border border-rule bg-gradient-to-b from-white to-brand-50 p-3 shadow-sm">
      <div className="flex w-full items-start justify-between">
        <span className="text-3xl font-black tabular-nums text-brand-600">
          {d.overall}
        </span>
        <span className="rounded bg-zinc-900 px-1.5 py-0.5 text-[10px] font-bold text-white">
          {d.position}
        </span>
      </div>
      <div className="w-full truncate text-sm font-bold text-zinc-900">
        {d.player_name}
      </div>
      <Radar values={d.factors.map((f) => f.axis_score)} />
      <span
        className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${BADGE_STYLE[d.form_badge]}`}
      >
        {BADGE_LABEL[d.form_badge]}
      </span>
      <div className="mt-1 w-full space-y-0.5 text-[10px] text-zinc-500">
        {d.factors.map((f) => (
          <div key={f.component} className="flex justify-between">
            <span>{f.label_ko}</span>
            <span className="tabular-nums text-zinc-700">
              {Math.round(f.axis_score)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Verify the web build compiles (web has no unit-test harness)**

Run: `cd apps/web && npm run build`
Expected: build succeeds with no type errors. (There is no Jest/Vitest setup in `apps/web`; `npm run build` is the type/compile gate.)

- [ ] **Step 5: Commit**

```bash
git add apps/web/lib/types.ts apps/web/lib/api.ts apps/web/components/pregame/player-score-card.tsx
git commit -m "feat(web): add player score-card component and api wrapper"
```

---

## Task 3: Mount cards on the pregame page

**Files:**
- Modify: `apps/web/app/games/[gameId]/pregame/page.tsx`

**Interfaces:**
- Consumes: `pregame.recommended_lineup` (already fetched in the page; `LineupRow[]` with `player_id`, `player_name`), and `<PlayerScoreCard>` from Task 2.

- [ ] **Step 1: Import the card component**

In `apps/web/app/games/[gameId]/pregame/page.tsx`, add to the imports (next to the existing `PlayerComparisonPanel` import on line 6):

```tsx
import { PlayerScoreCard } from "@/components/pregame/player-score-card";
```

- [ ] **Step 2: Render a card row for the recommended lineup**

Locate the existing `<PlayerComparisonPanel ... />` block (~line 102). Insert a new section immediately **before** it:

```tsx
        <section className="space-y-3">
          <SectionHeading>추천 라인업 선수 카드</SectionHeading>
          <div className="flex flex-wrap gap-3">
            {pregame.recommended_lineup.map((row) => (
              <PlayerScoreCard
                key={row.player_id}
                gameId={gameId}
                playerId={row.player_id}
                playerName={row.player_name}
              />
            ))}
          </div>
        </section>
```

> `gameId` is already in scope on this page (used by `PlayerComparisonPanel`); reuse the same value/variable. If the page derives it as `Number(params.gameId)`, pass that same number.

- [ ] **Step 3: Verify the build compiles**

Run: `cd apps/web && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Manual smoke test against Supabase dev**

Follow the running-supabase-dev skill (API on :8000, web on **http://localhost:3000** — never 127.0.0.1). Open `http://localhost:3000/games/16/pregame`:
- A "추천 라인업 선수 카드" row renders 9 cards.
- Each card shows an OVR number, position chip, a 5-axis pentagon, and a HOT/COLD/EVEN badge.
- No console errors; cards populate (confirms client hydration).

- [ ] **Step 5: Run pre-commit across the repo**

Run: `pre-commit run --all-files`
Expected: ruff, mypy, bandit, vulture pass. Fix any findings, re-run.

- [ ] **Step 6: Commit**

```bash
git add apps/web/app/games/[gameId]/pregame/page.tsx
git commit -m "feat(web): mount player score cards on the pregame page"
```

---

## Self-Review

**1. Spec coverage:**
- 5-factor radar → Task 1 emits `factors` (fixed order), Task 2 `Radar` draws them. ✅
- OVR number → Task 1 `overall`, Task 2 header. ✅
- Hot/cold badge → Task 1 `form_badge`, Task 2 badge. ✅
- Reusable card atom for Phases 2–4 → `PlayerScoreCardResponse` + `<PlayerScoreCard>` contract fixed in Interfaces section. ✅
- Architecture invariant (no scoring-layer mutation, display-only derived values) → Global Constraints + Step 4 note; no edits to `player_score.py` or persisted scores. ✅
- Sparkline (per-game momentum) is intentionally **out of scope** here — per-game OPS logs are not in `stats_json` (only 14d/30d aggregates), so it needs a separate ingestion-surfacing task. Card uses the hot/cold badge instead. Documented.

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; test code is concrete with real fixtures and the existing `_replay_body`/`clean_env` helpers.

**3. Type consistency:**
- `PlayerScoreCardResponse`/`PlayerScoreCardFactor` field names identical across Pydantic (Task 1), TS (Task 2), and component usage (`axis_score`, `form_badge`, `overall`, `factors`, `player_name`, `position`). ✅
- Component literal set identical in `ScoreCardComponentLiteral` (py) and `ScoreCardComponent` (ts) and `_CARD_COMPONENT_ORDER`. ✅
- Service name `build_player_score_card` consistent between service, route import, and route call. ✅
- `api.playerScoreCard` name consistent between definition (Task 2) and component call (Task 2 Step 3). ✅

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-18-viz-player-score-card.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

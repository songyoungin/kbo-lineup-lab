# LLM Batting Order + Korean Explanations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rule-based batting-order assignment with an OpenAI LLM that reorders the nine deterministically-selected players to maximize team run production and writes Korean explanations, while keeping player scoring and position assignment deterministic and falling back to the existing rule on any failure.

**Architecture:** The deterministic engine (`player_score`, `lineup_score`, position-assignment greedy) stays as the source of truth. A new additive layer `app/lineup_model/batting_order/` calls OpenAI (behind a `Protocol`), validates the output against the nine assigned players, retries once, and falls back to the preserved rule-based order. `lineup_evaluator.evaluate_lineup_for_run` wires it in; the lineup score is always recomputed deterministically over the chosen order.

**Tech Stack:** Python 3.13, pydantic 2, SQLAlchemy 2, pytest, `openai` SDK, `uv` for dependency management. Reference spec: `docs/superpowers/specs/2026-05-31-llm-batting-order-design.md`.

**Working directory:** all commands run from `apps/api/` (the API package root). Tests live in `apps/api/tests/`.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `apps/api/pyproject.toml` | Add `openai` runtime dependency |
| `apps/api/app/lineup_model/recommendation.py` | (modify) extract `select_and_assign_positions`; keep `_assign_batting_order` + `generate_recommendation` behavior identical |
| `apps/api/app/lineup_model/batting_order/__init__.py` | (new) package marker |
| `apps/api/app/lineup_model/batting_order/types.py` | (new) `BattingOrderResult`, `BattingOrderProvider` Protocol |
| `apps/api/app/lineup_model/batting_order/schema.py` | (new) OpenAI JSON schema + `parse_and_validate` |
| `apps/api/app/lineup_model/batting_order/prompt.py` | (new) static system prompt + dynamic user-prompt builder |
| `apps/api/app/lineup_model/batting_order/provider.py` | (new) `OpenAIProvider` + `build_provider` env factory |
| `apps/api/app/lineup_model/batting_order/orderer.py` | (new) `order()` orchestration + deterministic fallback |
| `apps/api/app/services/lineup_evaluator.py` | (modify) wire selection → order → score; persist Korean texts + source |
| `apps/api/tests/test_batting_order.py` | (new) unit tests for schema/prompt/provider/orderer |
| `apps/api/tests/test_recommendation.py` | (existing) must stay green (fallback reproduces today's order) |

---

## Task 1: Add the `openai` dependency

**Files:**
- Modify: `apps/api/pyproject.toml`

- [ ] **Step 1: Add the dependency with uv**

Run (from `apps/api/`):
```bash
uv add openai
```
Expected: `openai` appears under `[project].dependencies` in `apps/api/pyproject.toml` and `uv.lock` is updated.

- [ ] **Step 2: Verify it imports**

Run:
```bash
uv run python -c "import openai; print(openai.__version__)"
```
Expected: prints a version string, no error.

- [ ] **Step 3: Commit**

```bash
git add apps/api/pyproject.toml apps/api/uv.lock
git commit -m "chore(api): add openai dependency for LLM batting order"
```

---

## Task 2: Extract `select_and_assign_positions` (deterministic refactor)

Goal: expose the greedy position-assignment step as its own function without changing any observable behavior. `generate_recommendation` keeps producing the exact same result (used as the fallback core and by existing tests).

**Files:**
- Modify: `apps/api/app/lineup_model/recommendation.py`
- Test: `apps/api/tests/test_recommendation.py` (existing, must stay green)

- [ ] **Step 1: Write a failing test for the extracted function**

Add to `apps/api/tests/test_recommendation.py` (after the existing imports add `select_and_assign_positions` to the import from `app.lineup_model.recommendation`):

```python
def test_select_and_assign_positions_is_deterministic_and_complete() -> None:
    """동일 입력에 대해 9개 포지션이 모두 채워지고 결과가 결정론적인지 검증."""
    from app.lineup_model.recommendation import select_and_assign_positions

    pool = _make_pool()
    first = select_and_assign_positions(pool, Handedness.RIGHT)
    second = select_and_assign_positions(pool, Handedness.RIGHT)

    assert len(first) == 9
    assert {str(p) for p in first} == {"C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"}
    assert {p: s.player_id for p, s in first.items()} == {
        p: s.player_id for p, s in second.items()
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run:
```bash
uv run pytest tests/test_recommendation.py::test_select_and_assign_positions_is_deterministic_and_complete -v
```
Expected: FAIL with `ImportError: cannot import name 'select_and_assign_positions'`.

- [ ] **Step 3: Extract the function in `recommendation.py`**

In `apps/api/app/lineup_model/recommendation.py`, add this function just above `generate_recommendation`:

```python
def select_and_assign_positions(
    eligible_players: list[HitterStats],
    opp_handedness: Handedness,
) -> dict[Position, HitterStats]:
    """포지션별 최고 점수 선수를 그리디로 배정해 9개 수비 위치를 채운다.

    Args:
        eligible_players: 가용 타자 풀.
        opp_handedness: 상대 선발 투수의 손.

    Returns:
        포지션 → 배정된 HitterStats 매핑(9개).

    Raises:
        ValueError: 9개 포지션을 모두 채울 수 없는 경우.
    """
    assigned: dict[Position, HitterStats] = {}
    excluded_ids: set[int] = set()

    for position in _POSITIONS_TO_FILL:
        best = _best_player_for_position(eligible_players, position, opp_handedness, excluded_ids)
        if best is None:
            raise ValueError(
                f"Cannot fill position {position}: no eligible player remaining in pool. "
                f"Assigned so far: {list(assigned.keys())}"
            )
        assigned[position] = best
        excluded_ids.add(best.player_id)

    return assigned
```

Then replace the body of `generate_recommendation` so it delegates to the new function (behavior unchanged):

```python
def generate_recommendation(
    eligible_players: list[HitterStats],
    opp_handedness: Handedness,
) -> LineupScoreBreakdown:
    """Generate the best valid 9-slot lineup from the eligible player pool.

    Raises ValueError if the pool cannot fill all 9 positions.

    Args:
        eligible_players: All available hitters (status = available).
        opp_handedness: Opposing starter's handedness.

    Returns:
        LineupScoreBreakdown for the recommended lineup.

    Raises:
        ValueError: If no valid 9-player lineup can be assembled.
    """
    assigned = select_and_assign_positions(eligible_players, opp_handedness)
    slots = _assign_batting_order(assigned, opp_handedness)
    stats_by_player = {stats.player_id: stats for stats in eligible_players}
    return compute_lineup_score(tuple(slots), stats_by_player, opp_handedness)
```

- [ ] **Step 4: Run the new test and the full recommendation suite**

Run:
```bash
uv run pytest tests/test_recommendation.py -v
```
Expected: all PASS (new test plus every existing test — behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/lineup_model/recommendation.py apps/api/tests/test_recommendation.py
git commit -m "refactor(lineup): extract select_and_assign_positions from generate_recommendation"
```

---

## Task 3: Batting-order types (`BattingOrderResult`, provider Protocol)

**Files:**
- Create: `apps/api/app/lineup_model/batting_order/__init__.py`
- Create: `apps/api/app/lineup_model/batting_order/types.py`
- Test: `apps/api/tests/test_batting_order.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_batting_order.py`:

```python
"""LLM 타순 레이어(batting_order) 단위 테스트."""

from __future__ import annotations

from app.lineup_model.types import LineupSlot, Position


def test_batting_order_result_is_frozen_and_typed() -> None:
    """BattingOrderResult가 슬롯·근거·요약·소스를 보관하고 불변인지 검증."""
    from app.lineup_model.batting_order.types import BattingOrderResult

    result = BattingOrderResult(
        slots=(LineupSlot(batting_order=1, player_id=7, position=Position.C),),
        rationale_ko_by_player={7: "출루율이 높아 1번"},
        summary_ko="요약",
        source="llm",
    )
    assert result.source == "llm"
    assert result.rationale_ko_by_player[7] == "출루율이 높아 1번"
```

- [ ] **Step 2: Run it to verify it fails**

Run:
```bash
uv run pytest tests/test_batting_order.py::test_batting_order_result_is_frozen_and_typed -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.lineup_model.batting_order'`.

- [ ] **Step 3: Create the package and types**

Create `apps/api/app/lineup_model/batting_order/__init__.py`:

```python
"""LLM 기반 타순 설계 레이어."""
```

Create `apps/api/app/lineup_model/batting_order/types.py`:

```python
"""타순 레이어의 입출력 타입과 제공자 인터페이스."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.lineup_model.types import LineupSlot


class BattingOrderResult(BaseModel):
    """LLM(또는 폴백)이 산출한 타순 결과.

    Attributes 설명은 생략(필드명 자체로 자명).
    """

    model_config = ConfigDict(frozen=True)

    slots: tuple[LineupSlot, ...]
    rationale_ko_by_player: dict[int, str]
    summary_ko: str
    source: str  # "llm" 또는 "fallback"


class BattingOrderProvider(Protocol):
    """타순 LLM 호출을 추상화한 인터페이스."""

    def complete(
        self, *, system: str, user: str, schema: dict[str, object]
    ) -> dict[str, object]:
        """스키마를 만족하는 JSON 객체를 파싱해 반환한다."""
        ...
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
uv run pytest tests/test_batting_order.py::test_batting_order_result_is_frozen_and_typed -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/lineup_model/batting_order/__init__.py apps/api/app/lineup_model/batting_order/types.py apps/api/tests/test_batting_order.py
git commit -m "feat(lineup): add batting_order types and provider protocol"
```

---

## Task 4: Output schema + `parse_and_validate`

**Files:**
- Create: `apps/api/app/lineup_model/batting_order/schema.py`
- Test: `apps/api/tests/test_batting_order.py`

- [ ] **Step 1: Write the failing tests**

Add to `apps/api/tests/test_batting_order.py` (add `HitterStats`, `Handedness` to the top-level import from `app.lineup_model.types`):

```python
def _assigned_three() -> dict[Position, HitterStats]:
    """포지션 3개에 선수를 배정한 작은 매핑(검증 로직 테스트용)."""
    base = dict(ops=0.800, obp=0.350, slg=0.450)
    return {
        Position.C: HitterStats(player_id=1, handedness=Handedness.RIGHT, primary_position=Position.C, **base),
        Position.FIRST: HitterStats(player_id=2, handedness=Handedness.LEFT, primary_position=Position.FIRST, **base),
        Position.DH: HitterStats(player_id=3, handedness=Handedness.RIGHT, primary_position=Position.DH, **base),
    }


def test_parse_and_validate_accepts_valid_permutation() -> None:
    """배정된 선수와 정확히 일치하는 유효 출력은 슬롯·근거·요약으로 파싱된다."""
    from app.lineup_model.batting_order.schema import parse_and_validate

    assigned = _assigned_three()
    raw = {
        "batting_order": [
            {"batting_order": 2, "player_id": 1, "rationale_ko": "근거1"},
            {"batting_order": 1, "player_id": 2, "rationale_ko": "근거2"},
            {"batting_order": 3, "player_id": 3, "rationale_ko": "근거3"},
        ],
        "lineup_summary_ko": "요약",
    }
    out = parse_and_validate(raw, assigned)
    assert out is not None
    slots, rationale, summary = out
    assert [s.batting_order for s in slots] == [1, 2, 3]
    assert slots[0].player_id == 2 and slots[0].position == Position.FIRST
    assert rationale[1] == "근거1"
    assert summary == "요약"


def test_parse_and_validate_rejects_unknown_player() -> None:
    """배정되지 않은 player_id가 섞이면 None을 반환한다."""
    from app.lineup_model.batting_order.schema import parse_and_validate

    assigned = _assigned_three()
    raw = {
        "batting_order": [
            {"batting_order": 1, "player_id": 99, "rationale_ko": "x"},
            {"batting_order": 2, "player_id": 2, "rationale_ko": "y"},
            {"batting_order": 3, "player_id": 3, "rationale_ko": "z"},
        ],
        "lineup_summary_ko": "요약",
    }
    assert parse_and_validate(raw, assigned) is None


def test_parse_and_validate_rejects_duplicate_order() -> None:
    """batting_order 값이 1..N 순열이 아니면 None을 반환한다."""
    from app.lineup_model.batting_order.schema import parse_and_validate

    assigned = _assigned_three()
    raw = {
        "batting_order": [
            {"batting_order": 1, "player_id": 1, "rationale_ko": "x"},
            {"batting_order": 1, "player_id": 2, "rationale_ko": "y"},
            {"batting_order": 3, "player_id": 3, "rationale_ko": "z"},
        ],
        "lineup_summary_ko": "요약",
    }
    assert parse_and_validate(raw, assigned) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run:
```bash
uv run pytest tests/test_batting_order.py -k parse_and_validate -v
```
Expected: FAIL with `ModuleNotFoundError` / `cannot import name 'parse_and_validate'`.

- [ ] **Step 3: Implement the schema module**

Create `apps/api/app/lineup_model/batting_order/schema.py`:

```python
"""OpenAI structured-output 스키마와 출력 검증 로직."""

from __future__ import annotations

from app.lineup_model.types import HitterStats, LineupSlot, Position

# OpenAI Chat Completions의 response_format=json_schema 에 넘기는 정의.
ORDER_JSON_SCHEMA: dict[str, object] = {
    "name": "batting_order",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "batting_order": {
                "type": "array",
                "minItems": 9,
                "maxItems": 9,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "batting_order": {"type": "integer", "minimum": 1, "maximum": 9},
                        "player_id": {"type": "integer"},
                        "rationale_ko": {"type": "string"},
                    },
                    "required": ["batting_order", "player_id", "rationale_ko"],
                },
            },
            "lineup_summary_ko": {"type": "string"},
        },
        "required": ["batting_order", "lineup_summary_ko"],
    },
}


def parse_and_validate(
    raw: dict[str, object],
    assigned: dict[Position, HitterStats],
) -> tuple[tuple[LineupSlot, ...], dict[int, str], str] | None:
    """LLM 출력을 배정된 선수 집합에 대해 검증한다.

    배정된 선수와 정확히 일치하고 batting_order가 1..N 순열이며 모든
    한국어 텍스트가 비어있지 않을 때만 (슬롯, 선수별 근거, 요약)을 반환한다.
    그 외에는 None.

    Args:
        raw: LLM이 반환한 JSON 객체.
        assigned: 포지션 → 배정 선수 매핑.

    Returns:
        (슬롯 튜플(타순 정렬), player_id→근거, 요약) 또는 None.
    """
    position_by_player = {stats.player_id: pos for pos, stats in assigned.items()}
    assigned_ids = set(position_by_player)
    expected_count = len(assigned_ids)

    entries = raw.get("batting_order")
    summary = raw.get("lineup_summary_ko")
    if not isinstance(entries, list) or len(entries) != expected_count:
        return None
    if not isinstance(summary, str) or not summary.strip():
        return None

    slots: list[LineupSlot] = []
    rationale: dict[int, str] = {}
    seen_orders: set[int] = set()
    seen_players: set[int] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            return None
        order = entry.get("batting_order")
        pid = entry.get("player_id")
        note = entry.get("rationale_ko")
        if not isinstance(order, int) or not isinstance(pid, int):
            return None
        if not isinstance(note, str) or not note.strip():
            return None
        if order < 1 or order > expected_count or order in seen_orders:
            return None
        if pid not in assigned_ids or pid in seen_players:
            return None
        seen_orders.add(order)
        seen_players.add(pid)
        slots.append(
            LineupSlot(batting_order=order, player_id=pid, position=position_by_player[pid])
        )
        rationale[pid] = note.strip()

    if seen_players != assigned_ids:
        return None

    ordered = tuple(sorted(slots, key=lambda s: s.batting_order))
    return ordered, rationale, summary.strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_batting_order.py -k parse_and_validate -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/lineup_model/batting_order/schema.py apps/api/tests/test_batting_order.py
git commit -m "feat(lineup): add batting-order output schema and validation"
```

---

## Task 5: Prompt builder (static system + dynamic user prompt)

**Files:**
- Create: `apps/api/app/lineup_model/batting_order/prompt.py`
- Test: `apps/api/tests/test_batting_order.py`

- [ ] **Step 1: Write the failing tests**

Add to `apps/api/tests/test_batting_order.py`:

```python
def test_system_prompt_states_team_run_objective() -> None:
    """시스템 프롬프트가 팀 득점 극대화 목표와 한국어 작성 규칙을 담는지 검증."""
    from app.lineup_model.batting_order.prompt import SYSTEM_PROMPT

    assert "득점" in SYSTEM_PROMPT
    assert "한국어" in SYSTEM_PROMPT


def test_build_user_prompt_lists_all_assigned_players() -> None:
    """유저 프롬프트가 배정된 모든 player_id와 상대 손 정보를 포함하는지 검증."""
    from app.lineup_model.batting_order.prompt import build_user_prompt

    assigned = _assigned_three()
    text = build_user_prompt(assigned, Handedness.LEFT)
    for pid in (1, 2, 3):
        assert f"player_id={pid}" in text
    assert "L" in text
```

- [ ] **Step 2: Run them to verify they fail**

Run:
```bash
uv run pytest tests/test_batting_order.py -k prompt -v
```
Expected: FAIL with `cannot import name 'SYSTEM_PROMPT'` / `build_user_prompt`.

- [ ] **Step 3: Implement the prompt module**

Create `apps/api/app/lineup_model/batting_order/prompt.py`:

```python
"""타순 LLM 호출에 사용할 프롬프트(정적 시스템 + 동적 유저)."""

from __future__ import annotations

from app.lineup_model.player_score import compute_player_score
from app.lineup_model.types import Handedness, HitterStats, Position

# 정적 프리픽스: 게임마다 동일 → 프롬프트 캐싱 대상. 항상 프롬프트 앞에 둔다.
SYSTEM_PROMPT = (
    "당신은 KBO 타순을 설계하는 야구 분석가입니다. 주어진 9명의 선수를 "
    "1번부터 9번 타순으로 배치하세요.\n\n"
    "목표: 팀 전체의 득점을 극대화합니다. 출루율이 높은 테이블세터를 1~2번에 두어 "
    "득점 생산형(장타·타점) 타자가 주자가 있는 상황에서 타석에 들어서게 하세요. "
    "팀에서 가장 뛰어난 타자는 보통 3~4번(클린업)에 배치하며, 1번(리드오프)에 "
    "소모하지 않습니다.\n\n"
    "규칙:\n"
    "- 제공된 9명의 player_id만 사용하고, 각 선수를 정확히 한 번만 배치합니다.\n"
    "- batting_order는 1부터 9까지의 순열이어야 합니다.\n"
    "- 존재하지 않는 선수나 스탯을 지어내지 마세요.\n"
    "- 각 선수의 배치 근거(rationale_ko)와 전체 요약(lineup_summary_ko)은 "
    "반드시 자연스러운 한국어로 작성합니다.\n"
    "- 반드시 제공된 JSON 스키마 형식으로만 응답합니다."
)


def build_user_prompt(
    assigned: dict[Position, HitterStats],
    opp_handedness: Handedness,
) -> str:
    """배정된 선수들의 스탯을 담은 동적 유저 프롬프트를 만든다.

    Args:
        assigned: 포지션 → 배정 선수 매핑.
        opp_handedness: 상대 선발 투수의 손.

    Returns:
        LLM에 전달할 유저 프롬프트 문자열.
    """
    lines: list[str] = [
        f"상대 선발 투수 손: {opp_handedness}",
        "",
        "선발 라인업 9명(점수는 결정론 엔진이 계산한 참고용 종합 점수):",
    ]
    for position, stats in assigned.items():
        breakdown = compute_player_score(stats, position, opp_handedness)
        score = breakdown.total_score if breakdown is not None else 0.0
        lines.append(
            f"- player_id={stats.player_id} 포지션={position} 타격손={stats.handedness} "
            f"OBP={stats.obp:.3f} SLG={stats.slg:.3f} OPS={stats.ops:.3f} "
            f"최근14일OPS={stats.recent_14d_ops} 최근30일OPS={stats.recent_30d_ops} "
            f"vsLHP_OPS={stats.vs_lhp_ops}(PA={stats.vs_lhp_pa}) "
            f"vsRHP_OPS={stats.vs_rhp_ops}(PA={stats.vs_rhp_pa}) "
            f"종합점수={score:.4f}"
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_batting_order.py -k prompt -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/lineup_model/batting_order/prompt.py apps/api/tests/test_batting_order.py
git commit -m "feat(lineup): add batting-order prompt builder"
```

---

## Task 6: OpenAI provider + env factory

**Files:**
- Create: `apps/api/app/lineup_model/batting_order/provider.py`
- Test: `apps/api/tests/test_batting_order.py`

- [ ] **Step 1: Write the failing tests**

Add to `apps/api/tests/test_batting_order.py` (add `import json` and `from unittest.mock import MagicMock, patch` at the top of the file):

```python
def test_build_provider_returns_none_when_disabled(monkeypatch) -> None:
    """LINEUP_LLM_ENABLED가 꺼져 있으면 build_provider가 None을 반환한다."""
    from app.lineup_model.batting_order.provider import build_provider

    monkeypatch.delenv("LINEUP_LLM_ENABLED", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert build_provider() is None


def test_build_provider_returns_none_without_api_key(monkeypatch) -> None:
    """플래그가 켜져 있어도 API 키가 없으면 None을 반환한다."""
    from app.lineup_model.batting_order.provider import build_provider

    monkeypatch.setenv("LINEUP_LLM_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert build_provider() is None


@patch("app.lineup_model.batting_order.provider.OpenAI")
def test_openai_provider_parses_json_content(mock_openai_cls: MagicMock) -> None:
    """OpenAIProvider.complete가 응답 content(JSON 문자열)를 dict로 파싱한다."""
    from app.lineup_model.batting_order.provider import OpenAIProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    message = MagicMock()
    message.content = json.dumps({"lineup_summary_ko": "ok", "batting_order": []})
    mock_client.chat.completions.create.return_value.choices = [MagicMock(message=message)]

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4.1", timeout_s=5.0)
    out = provider.complete(system="sys", user="usr", schema={"name": "x"})
    assert out == {"lineup_summary_ko": "ok", "batting_order": []}
    mock_client.chat.completions.create.assert_called_once()
```

- [ ] **Step 2: Run them to verify they fail**

Run:
```bash
uv run pytest tests/test_batting_order.py -k provider -v
```
Expected: FAIL with `cannot import name 'build_provider'` / `OpenAIProvider`.

- [ ] **Step 3: Implement the provider module**

Create `apps/api/app/lineup_model/batting_order/provider.py`:

```python
"""OpenAI 기반 타순 제공자와 환경변수 팩토리."""

from __future__ import annotations

import json
import os

from openai import OpenAI

from app.lineup_model.batting_order.types import BattingOrderProvider

_DEFAULT_MODEL = "gpt-4.1"
_DEFAULT_TIMEOUT_S = 20.0


class OpenAIProvider:
    """OpenAI Chat Completions를 structured output으로 호출하는 제공자."""

    def __init__(self, api_key: str, model: str, timeout_s: float) -> None:
        self._client = OpenAI(api_key=api_key, timeout=timeout_s)
        self._model = model

    def complete(
        self, *, system: str, user: str, schema: dict[str, object]
    ) -> dict[str, object]:
        """스키마를 강제해 호출하고 응답 JSON을 dict로 반환한다.

        Args:
            system: 정적 시스템 프롬프트.
            user: 동적 유저 프롬프트.
            schema: response_format에 넘길 json_schema 정의.

        Returns:
            파싱된 JSON 객체.

        Raises:
            ValueError: 응답이 JSON 객체가 아닐 때.
        """
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            seed=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_schema", "json_schema": schema},
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM returned non-object JSON")
        return parsed


def build_provider() -> BattingOrderProvider | None:
    """환경변수를 읽어 제공자를 만든다(비활성/키 없음이면 None).

    Returns:
        OpenAIProvider 또는 None(비활성화·키 없음).
    """
    if os.environ.get("LINEUP_LLM_ENABLED", "false").lower() != "true":
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("LINEUP_LLM_MODEL", _DEFAULT_MODEL)
    timeout_s = float(os.environ.get("LINEUP_LLM_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S)))
    return OpenAIProvider(api_key=api_key, model=model, timeout_s=timeout_s)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_batting_order.py -k provider -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/lineup_model/batting_order/provider.py apps/api/tests/test_batting_order.py
git commit -m "feat(lineup): add OpenAI batting-order provider and env factory"
```

---

## Task 7: Orderer (orchestration + deterministic fallback)

**Files:**
- Create: `apps/api/app/lineup_model/batting_order/orderer.py`
- Test: `apps/api/tests/test_batting_order.py`

- [ ] **Step 1: Write the failing tests**

Add to `apps/api/tests/test_batting_order.py`:

```python
def _full_assigned() -> dict[Position, HitterStats]:
    """9개 포지션을 모두 채운 배정(오더러 테스트용)."""
    positions = [
        Position.C, Position.FIRST, Position.SECOND, Position.THIRD, Position.SHORT,
        Position.LEFT, Position.CENTER, Position.RIGHT, Position.DH,
    ]
    out: dict[Position, HitterStats] = {}
    for i, pos in enumerate(positions, start=1):
        out[pos] = HitterStats(
            player_id=i, handedness=Handedness.RIGHT, primary_position=pos,
            ops=0.700 + i * 0.01, obp=0.330, slg=0.420,
        )
    return out


class _FakeProvider:
    """미리 정한 dict를 반환하거나 예외를 던지는 가짜 제공자."""

    def __init__(self, payload: dict | None = None, raises: bool = False) -> None:
        self._payload = payload
        self._raises = raises
        self.calls = 0

    def complete(self, *, system: str, user: str, schema: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        assert self._payload is not None
        return self._payload


def test_order_uses_llm_when_output_valid() -> None:
    """유효한 LLM 출력이면 source='llm'으로 슬롯과 근거를 반환한다."""
    from app.lineup_model.batting_order.orderer import order

    assigned = _full_assigned()
    payload = {
        "batting_order": [
            {"batting_order": i, "player_id": i, "rationale_ko": f"{i}번 근거"}
            for i in range(1, 10)
        ],
        "lineup_summary_ko": "팀 득점 극대화 타순",
    }
    result = order(assigned, Handedness.RIGHT, _FakeProvider(payload=payload))
    assert result.source == "llm"
    assert [s.batting_order for s in result.slots] == list(range(1, 10))
    assert result.summary_ko == "팀 득점 극대화 타순"
    assert result.rationale_ko_by_player[1] == "1번 근거"


def test_order_falls_back_when_provider_is_none() -> None:
    """제공자가 None이면 결정론 폴백(source='fallback')을 사용한다."""
    from app.lineup_model.batting_order.orderer import order

    assigned = _full_assigned()
    result = order(assigned, Handedness.RIGHT, None)
    assert result.source == "fallback"
    assert len(result.slots) == 9
    assert sorted(s.batting_order for s in result.slots) == list(range(1, 10))


def test_order_retries_then_falls_back_on_invalid_output() -> None:
    """잘못된 출력이면 1회 재시도 후 폴백한다(제공자가 2번 호출됨)."""
    from app.lineup_model.batting_order.orderer import order

    assigned = _full_assigned()
    bad = {"batting_order": [], "lineup_summary_ko": "x"}  # 9개가 아님 → 무효
    provider = _FakeProvider(payload=bad)
    result = order(assigned, Handedness.RIGHT, provider)
    assert result.source == "fallback"
    assert provider.calls == 2


def test_order_falls_back_on_provider_exception() -> None:
    """제공자가 예외를 던지면 폴백한다."""
    from app.lineup_model.batting_order.orderer import order

    assigned = _full_assigned()
    provider = _FakeProvider(raises=True)
    result = order(assigned, Handedness.RIGHT, provider)
    assert result.source == "fallback"
```

- [ ] **Step 2: Run them to verify they fail**

Run:
```bash
uv run pytest tests/test_batting_order.py -k order -v
```
Expected: FAIL with `cannot import name 'order'`.

- [ ] **Step 3: Implement the orderer module**

Create `apps/api/app/lineup_model/batting_order/orderer.py`:

```python
"""타순 결정 오케스트레이션: LLM 호출 → 검증 → 재시도 → 결정론 폴백."""

from __future__ import annotations

import logging

from app.lineup_model.batting_order.prompt import SYSTEM_PROMPT, build_user_prompt
from app.lineup_model.batting_order.schema import ORDER_JSON_SCHEMA, parse_and_validate
from app.lineup_model.batting_order.types import BattingOrderProvider, BattingOrderResult
from app.lineup_model.recommendation import _assign_batting_order
from app.lineup_model.types import Handedness, HitterStats, Position

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2


def _fallback(
    assigned: dict[Position, HitterStats],
    opp_handedness: Handedness,
) -> BattingOrderResult:
    """규칙 기반 결정론 타순으로 결과를 만든다."""
    slots = _assign_batting_order(assigned, opp_handedness)
    ordered = tuple(sorted(slots, key=lambda s: s.batting_order))
    rationale = {s.player_id: f"규칙 기반 배정: {s.batting_order}번" for s in ordered}
    summary = "LLM을 사용할 수 없어 규칙 기반 타순으로 구성했습니다."
    return BattingOrderResult(
        slots=ordered,
        rationale_ko_by_player=rationale,
        summary_ko=summary,
        source="fallback",
    )


def order(
    assigned: dict[Position, HitterStats],
    opp_handedness: Handedness,
    provider: BattingOrderProvider | None,
) -> BattingOrderResult:
    """배정된 9명의 타순을 LLM으로 결정하고, 실패 시 결정론 폴백한다.

    Args:
        assigned: 포지션 → 배정 선수 매핑(9명).
        opp_handedness: 상대 선발 투수의 손.
        provider: 타순 제공자. None이면 곧바로 폴백.

    Returns:
        BattingOrderResult(slots, 선수별 근거, 요약, source).
    """
    if provider is None:
        return _fallback(assigned, opp_handedness)

    user_prompt = build_user_prompt(assigned, opp_handedness)
    for attempt in range(_MAX_ATTEMPTS):
        try:
            raw = provider.complete(
                system=SYSTEM_PROMPT, user=user_prompt, schema=ORDER_JSON_SCHEMA
            )
        except Exception as exc:  # noqa: BLE001 — 어떤 제공자 오류든 재시도/폴백 대상
            logger.warning("LLM batting-order call failed (attempt %d): %s", attempt + 1, exc)
            continue

        validated = parse_and_validate(raw, assigned)
        if validated is not None:
            slots, rationale, summary = validated
            return BattingOrderResult(
                slots=slots,
                rationale_ko_by_player=rationale,
                summary_ko=summary,
                source="llm",
            )
        logger.warning("LLM batting-order output invalid (attempt %d)", attempt + 1)

    return _fallback(assigned, opp_handedness)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
uv run pytest tests/test_batting_order.py -v
```
Expected: all PASS (every test in the file).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/lineup_model/batting_order/orderer.py apps/api/tests/test_batting_order.py
git commit -m "feat(lineup): add batting-order orderer with retry and deterministic fallback"
```

---

## Task 8: Wire the orderer into `evaluate_lineup_for_run`

Goal: build the recommendation from `select_and_assign_positions` → `order` → `compute_lineup_score`, persist Korean per-slot rationale + Korean summary, and record the source/model. The deterministic score and `output_hash` are unchanged when the fallback path runs (identical slots → identical hash), so existing tests stay green.

**Files:**
- Modify: `apps/api/app/services/lineup_evaluator.py`
- Test: `apps/api/tests/test_recommendation.py`

- [ ] **Step 1: Write a failing test that injects a fake provider end-to-end**

First, make the provider injectable. The test will monkeypatch `build_provider` used inside `lineup_evaluator`. Add to `apps/api/tests/test_recommendation.py`:

```python
def test_evaluate_persists_llm_rationale_and_summary(session, monkeypatch) -> None:
    """LLM 제공자를 주입하면 한국어 슬롯 근거와 요약이 영속되는지 검증.

    monkeypatch: lineup_evaluator가 사용하는 build_provider가 가짜 제공자를
    반환하도록 교체. 가짜 제공자는 배정된 9명을 그대로 1~9번에 매핑한 JSON을 반환.
    검증: recommended_lineup_rows.rationale가 한국어, summary_text가 LLM 요약,
    run.output_hash 설정, 행 수 9.
    """
    import app.services.lineup_evaluator as evaluator_mod

    # 가짜 제공자: 받은 유저 프롬프트에서 player_id를 추출해 순서대로 1~9 배치
    class _FakeProvider:
        def complete(self, *, system, user, schema):
            import re

            ids = [int(m) for m in re.findall(r"player_id=(\d+)", user)]
            return {
                "batting_order": [
                    {"batting_order": i + 1, "player_id": pid, "rationale_ko": f"{i + 1}번 한국어 근거"}
                    for i, pid in enumerate(ids)
                ],
                "lineup_summary_ko": "LLM이 작성한 한국어 요약",
            }

    monkeypatch.setattr(evaluator_mod, "build_provider", lambda: _FakeProvider())

    run = _seed_run_from_fixture(session)  # 아래 Step 1b에서 정의/재사용
    evaluate_lineup_for_run(session, run)

    rows = session.query(RecommendedLineupRow).filter_by(evaluation_run_id=run.id).all()
    summary = session.query(LineupEvaluationSummary).filter_by(evaluation_run_id=run.id).one()
    assert len(rows) == 9
    assert all("근거" in (r.rationale or "") for r in rows)
    assert summary.summary_text == "LLM이 작성한 한국어 요약"
    assert run.output_hash is not None
```

- [ ] **Step 1b: Reuse the existing DB-seeding helper**

This test needs a seeded run. Reuse whatever the existing persistence test in `test_recommendation.py` uses to create a `LineupEvaluationRun` (look for the test that asserts `recommended_lineup_rows count == 9`). Extract its setup into a module-level helper `_seed_run_from_fixture(session) -> LineupEvaluationRun` and call it from both that test and the new one. If the existing test already has inline setup, move the run-creation lines verbatim into `_seed_run_from_fixture` and have both tests call it. Do not change the seeding logic — only relocate it.

- [ ] **Step 2: Run it to verify it fails**

Run:
```bash
uv run pytest tests/test_recommendation.py::test_evaluate_persists_llm_rationale_and_summary -v
```
Expected: FAIL — either `build_provider` is not an attribute of `lineup_evaluator` yet, or `summary_text`/`rationale` do not match (English template still in use).

- [ ] **Step 3: Update the imports in `lineup_evaluator.py`**

In `apps/api/app/services/lineup_evaluator.py`, replace the recommendation import line:

```python
from app.lineup_model.recommendation import generate_recommendation
```

with:

```python
from app.lineup_model.batting_order.orderer import order as order_batting_lineup
from app.lineup_model.batting_order.provider import build_provider
from app.lineup_model.lineup_score import compute_lineup_score
from app.lineup_model.recommendation import select_and_assign_positions
```

(Keep the existing `from app.lineup_model.player_score import compute_player_score` import — it is still used for the per-player `score` column.)

- [ ] **Step 4: Replace the recommendation call**

Find this line (around `recommendation.py` call site, ~line 343):

```python
    recommended = generate_recommendation(eligible, opp_handedness)
```

Replace it with:

```python
    assigned = select_and_assign_positions(eligible, opp_handedness)
    provider = build_provider()
    order_result = order_batting_lineup(assigned, opp_handedness, provider)
    stats_by_player = {s.player_id: s for s in eligible}
    recommended = compute_lineup_score(order_result.slots, stats_by_player, opp_handedness)
```

Note: a later block also defines `stats_by_player = {s.player_id: s for s in eligible}` (around line 347). Remove that now-duplicate assignment so the variable is defined once here.

- [ ] **Step 5: Use the Korean rationale when persisting rows**

In the `RecommendedLineupRow` persistence loop (around lines 350–369), replace the deterministic rationale construction. Change:

```python
    for slot in sorted(recommended.slots, key=lambda s: s.batting_order):
        stats = stats_by_player[slot.player_id]
        # Build a concise rationale string
        breakdown = compute_player_score(stats, slot.position, opp_handedness)
        rationale_parts = []
        if breakdown is not None:
            for r in breakdown.reasons:
                rationale_parts.append(f"{r.component}={r.value:.3f}(w={r.weight}): {r.note}")
        rationale = "; ".join(rationale_parts)

        session.add(
            RecommendedLineupRow(
                evaluation_run_id=run.id,
                player_id=slot.player_id,
                batting_order=slot.batting_order,
                position=str(slot.position),
                score=breakdown.total_score if breakdown is not None else None,
                rationale=rationale,
            )
        )
```

to:

```python
    for slot in sorted(recommended.slots, key=lambda s: s.batting_order):
        stats = stats_by_player[slot.player_id]
        breakdown = compute_player_score(stats, slot.position, opp_handedness)
        rationale = order_result.rationale_ko_by_player.get(slot.player_id, "")

        session.add(
            RecommendedLineupRow(
                evaluation_run_id=run.id,
                player_id=slot.player_id,
                batting_order=slot.batting_order,
                position=str(slot.position),
                score=breakdown.total_score if breakdown is not None else None,
                rationale=rationale,
            )
        )
```

- [ ] **Step 6: Use the Korean summary as `summary_text` and record the source**

Find the `summary_text` construction (around lines 406–418, the f-string starting `"Recommended lineup score: ..."`). Replace the assignment of the English template so the persisted `summary_text` is `order_result.summary_ko`. Locate the `LineupEvaluationSummary(...)` creation and set `summary_text=order_result.summary_ko`. Keep `key_insights_json=key_insights` unchanged.

Then, where `run.model_config_json` is set (search for `model_config_json`; if it is currently `None`/unset at completion, add this assignment just before `run.status = "completed"`):

```python
    run.model_config_json = {
        "batting_order_source": order_result.source,
        "llm_model": os.environ.get("LINEUP_LLM_MODEL", "gpt-4.1"),
    }
```

Add `import os` at the top of the file if it is not already imported.

- [ ] **Step 7: Run the targeted test and the full recommendation suite**

Run:
```bash
uv run pytest tests/test_recommendation.py -v
```
Expected: all PASS — the new LLM-injection test passes, and the existing tests pass because the default (no provider) path falls back to the identical deterministic order, keeping `output_hash` and row counts stable.

- [ ] **Step 8: Run the entire test suite**

Run:
```bash
uv run pytest -q
```
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/services/lineup_evaluator.py apps/api/tests/test_recommendation.py
git commit -m "feat(lineup): use LLM batting order with Korean explanations in evaluation"
```

---

## Task 9: Documentation note + final regression

**Files:**
- Modify: `apps/api/.env.example` (create if absent) or the project README data-sources doc

- [ ] **Step 1: Document the new environment variables**

Append to `apps/api/.env.example` (create the file if it does not exist):

```bash
# LLM batting-order layer (optional; disabled by default)
LINEUP_LLM_ENABLED=false
OPENAI_API_KEY=
LINEUP_LLM_MODEL=gpt-4.1
LINEUP_LLM_TIMEOUT_S=20
```

- [ ] **Step 2: Run the full suite once more**

Run:
```bash
uv run pytest -q
```
Expected: all tests pass.

- [ ] **Step 3: Run pre-commit on the changed files**

Run (from repo root):
```bash
pre-commit run --all-files
```
Expected: hooks pass (ruff, mypy, etc.). Fix any reported issues, then re-run.

- [ ] **Step 4: Commit**

```bash
git add apps/api/.env.example
git commit -m "docs(lineup): document LLM batting-order env vars"
```

---

## Notes for the implementer

- **Determinism contract:** the recommendation is generated once during `evaluate_lineup_for_run` and persisted. Postgame reads persisted rows; it never calls the LLM. With the LLM disabled (default), behavior is byte-identical to today.
- **Why the fallback shares `_assign_batting_order`:** keeping one rule implementation means the fallback order equals the historical order, so `output_hash` stays stable and existing tests pass unchanged.
- **Cost/caching:** the static `SYSTEM_PROMPT` is sent first so OpenAI automatic prompt caching can reuse it across games processed in the same daily batch.
- **Security:** `OPENAI_API_KEY` is read only from the environment; never hardcode it. `.env` must be git-ignored (verify).
- **Out of scope (do not build):** LLM choosing which players start or their positions; Gemini provider; engine-generated candidate re-ranking; rewriting individual player-score notes into Korean.
```

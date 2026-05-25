"""KBO 박스스코어 raw 페이로드를 BoxScoreSnapshot + BoxScoreRow로 정규화한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.collectors._constants import LG_TEAM_CODE
from app.ingestion.player_matcher import MatchStatus, match_player
from app.models.game import Game
from app.models.snapshot import BoxScoreRow, BoxScoreSnapshot, RawIngestionPayload
from app.models.team import Team
from app.util.time import to_utc

__all__ = ["BoxScoreNormalizeResult", "normalize_box_score"]


@dataclass(frozen=True)
class BoxScoreNormalizeResult:
    """박스스코어 정규화 결과.

    Attributes:
        snapshot_id: 생성되거나 기존 BoxScoreSnapshot의 PK. 게임이 FINAL이 아니면 None.
        rows_created: 새로 삽입된 BoxScoreRow 수.
        rows_skipped: 선수를 찾지 못해 건너뛴 행 수.
        skipped_not_final: gameStatus가 FINAL이 아니어서 전체 페이로드를 건너뛴 경우 True.
        needs_review_reasons: 검토가 필요한 이유 목록.
    """

    snapshot_id: int | None
    rows_created: int
    rows_skipped: int
    skipped_not_final: bool
    needs_review_reasons: tuple[str, ...]


def _compute_content_hash(canonical: object) -> str:
    """정규화된 JSON 직렬화 후 SHA-256 해시를 반환한다."""
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def normalize_box_score(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> BoxScoreNormalizeResult:
    """raw 박스스코어 페이로드를 파싱하여 BoxScoreSnapshot + 행을 생성한다.

    기대하는 페이로드 형태 (KBO Official MVP 플레이스홀더):
    JSON:
        {
            "game_external_id": "20260415LGDOO",
            "taken_at": "2026-04-15T22:00:00+09:00",
            "gameStatus": "FINAL",
            "lg_hitters": [
                {"player_external_id": "LG-B001", "at_bats": 4, "hits": 2,
                 "runs": 1, "rbis": 1, "extra_stats_json": {}},
                ...
            ],
            "opponent_pitchers": [
                {"player_external_id": "OPP-P001", "innings_pitched": 5.2, ...},
                ...
            ]
        }

    gameStatus가 "FINAL"이 아니면 스냅샷을 생성하지 않고 즉시 반환한다.

    BoxScoreSnapshot은 content_hash로 중복 여부를 판단한다. 멱등 보장.

    HTML 폴백: MVP에서 미구현. NotImplementedError를 발생시킨다.

    Args:
        session: 활성 SQLAlchemy 세션. 커밋은 호출자가 담당.
        raw_payload: raw_ingestion_payloads 행.

    Returns:
        BoxScoreNormalizeResult.

    Raises:
        NotImplementedError: content_type이 JSON이 아닌 경우.
        ValueError: 페이로드 JSON 형식이 올바르지 않은 경우.
    """
    if "json" not in raw_payload.content_type.lower():
        raise NotImplementedError(
            f"HTML box_score normalization not implemented in MVP; "
            f"content_type={raw_payload.content_type!r}"
        )

    try:
        body = json.loads(raw_payload.raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"box_score payload is not valid JSON: {exc}") from exc

    game_status: str | None = body.get("gameStatus")
    if not isinstance(game_status, str) or game_status.upper() != "FINAL":
        return BoxScoreNormalizeResult(
            snapshot_id=None,
            rows_created=0,
            rows_skipped=0,
            skipped_not_final=True,
            needs_review_reasons=(),
        )

    game_external_id: str | None = body.get("game_external_id")
    taken_at_str: str | None = body.get("taken_at")

    if not game_external_id:
        raise ValueError("box_score payload missing 'game_external_id'")
    if not taken_at_str:
        raise ValueError("box_score payload missing 'taken_at'")

    try:
        taken_at = to_utc(datetime.fromisoformat(taken_at_str))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"box_score payload has invalid taken_at={taken_at_str!r}: {exc}") from exc

    game = session.execute(
        select(Game).where(Game.external_id == game_external_id)
    ).scalar_one_or_none()
    if game is None:
        raise ValueError(f"box_score payload references unknown game: {game_external_id!r}")

    content_hash = _compute_content_hash(body)

    existing_snapshot = session.execute(
        select(BoxScoreSnapshot).where(BoxScoreSnapshot.content_hash == content_hash)
    ).scalar_one_or_none()
    if existing_snapshot is not None:
        return BoxScoreNormalizeResult(
            snapshot_id=existing_snapshot.id,
            rows_created=0,
            rows_skipped=0,
            skipped_not_final=False,
            needs_review_reasons=(),
        )

    new_snapshot = BoxScoreSnapshot(
        game_id=game.id,
        ingestion_run_id=raw_payload.ingestion_run_id,
        taken_at=taken_at,
        content_hash=content_hash,
    )
    session.add(new_snapshot)
    session.flush()
    snapshot_id = new_snapshot.id

    rows_created = 0
    rows_skipped = 0
    needs_review_reasons: list[str] = []

    # lg_hitters: 타자 행
    lg_hitters: list[object] = body.get("lg_hitters") or []
    # lg_team_code 결정:
    # - 페이로드에 team_code가 있으면 사용
    # - 없으면 경기의 홈/어웨이 중 LG_TEAM_CODE에 해당하는 팀을 조회하여 추론하고,
    #   needs_review_reasons에 가정을 기록한다 (감사 추적용)
    explicit_team_code = _str_or_none(body.get("team_code"))
    if explicit_team_code:
        lg_team_code: str = explicit_team_code
    else:
        derived = _derive_lg_team_code_from_game(session, game)
        if derived is None:
            raise ValueError(
                f"box_score payload missing 'team_code' and game {game.external_id!r} "
                f"has no team matching LG_TEAM_CODE={LG_TEAM_CODE!r}"
            )
        lg_team_code = derived
        needs_review_reasons.append(
            "team_code not specified in payload; inferred from game roster "
            f"(lg_team_code={lg_team_code!r})"
        )

    for entry in _iter_dicts(lg_hitters):
        external_id: str | None = _str_or_none(entry.get("player_external_id"))
        match = match_player(
            session,
            team_code=lg_team_code,
            external_id=external_id,
            name=_str_or_none(entry.get("name")),
        )

        if match.status == MatchStatus.NOT_FOUND:
            rows_skipped += 1
            needs_review_reasons.append(
                f"box_score hitter skipped — {match.reason} (player_external_id={external_id!r})"
            )
            continue

        if match.status == MatchStatus.NEEDS_REVIEW:
            needs_review_reasons.append(match.reason)
            if match.player_id is None:
                rows_skipped += 1
                continue

        assert match.player_id is not None
        session.add(
            BoxScoreRow(
                snapshot_id=snapshot_id,
                player_id=match.player_id,
                at_bats=_int_or_none(entry.get("at_bats")),
                hits=_int_or_none(entry.get("hits")),
                runs=_int_or_none(entry.get("runs")),
                rbis=_int_or_none(entry.get("rbis")),
                extra_stats_json=entry.get("extra_stats_json") or {},
                innings_pitched=None,
            )
        )
        rows_created += 1

    # opponent_pitchers: 상대 투수 행
    opponent_pitchers: list[object] = body.get("opponent_pitchers") or []
    # 상대 팀 코드 — 페이로드에 없으면 경기에서 LG가 아닌 팀으로 추론
    opponent_team_code: str | None = _str_or_none(body.get("opponent_team_code"))

    for entry in _iter_dicts(opponent_pitchers):
        external_id = _str_or_none(entry.get("player_external_id"))
        name: str | None = _str_or_none(entry.get("name"))

        # 상대 팀 코드 결정
        resolved_team_code = opponent_team_code or _infer_opponent_team_code(
            session, game, lg_team_code
        )

        match = match_player(
            session,
            team_code=resolved_team_code,
            external_id=external_id,
            name=name,
        )

        if match.status == MatchStatus.NOT_FOUND:
            rows_skipped += 1
            needs_review_reasons.append(
                f"box_score pitcher skipped — {match.reason} (player_external_id={external_id!r})"
            )
            continue

        if match.status == MatchStatus.NEEDS_REVIEW:
            needs_review_reasons.append(match.reason)
            if match.player_id is None:
                rows_skipped += 1
                continue

        assert match.player_id is not None
        ip_raw = entry.get("innings_pitched")
        innings_pitched: float | None = None
        if isinstance(ip_raw, (int, float)):
            innings_pitched = float(ip_raw)
        session.add(
            BoxScoreRow(
                snapshot_id=snapshot_id,
                player_id=match.player_id,
                at_bats=None,
                hits=None,
                runs=None,
                rbis=None,
                extra_stats_json=entry.get("extra_stats_json") or {},
                innings_pitched=innings_pitched,
            )
        )
        rows_created += 1

    session.flush()
    return BoxScoreNormalizeResult(
        snapshot_id=snapshot_id,
        rows_created=rows_created,
        rows_skipped=rows_skipped,
        skipped_not_final=False,
        needs_review_reasons=tuple(needs_review_reasons),
    )


def _iter_dicts(entries: list[object]) -> list[dict[str, object]]:
    """dict 타입의 항목만 필터링하여 반환한다."""
    return [e for e in entries if isinstance(e, dict)]


def _str_or_none(value: object) -> str | None:
    """str 타입이면 반환하고 아니면 None을 반환한다."""
    return value if isinstance(value, str) else None


def _int_or_none(value: object) -> int | None:
    """int나 float 타입이면 int로 변환하고 아니면 None을 반환한다."""
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _derive_lg_team_code_from_game(session: Session, game: Game) -> str | None:
    """경기의 홈/어웨이 팀 중 LG_TEAM_CODE에 매칭되는 팀 코드를 반환한다.

    홈 또는 어웨이 팀이 LG_TEAM_CODE와 일치하면 그 코드를 반환한다. 둘 다
    아니면 None을 반환한다 (호출자가 ValueError를 던질지 결정).
    """
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)
    if home is not None and home.code == LG_TEAM_CODE:
        return home.code
    if away is not None and away.code == LG_TEAM_CODE:
        return away.code
    return None


def _infer_opponent_team_code(session: Session, game: Game, lg_team_code: str) -> str:
    """경기에서 LG 팀의 상대 팀 코드를 추론한다."""
    if game.home_team_id != game.away_team_id:
        # LG가 홈이면 어웨이 팀이 상대, 아니면 홈 팀이 상대
        lg_team = session.execute(
            select(Team).where(Team.code == lg_team_code)
        ).scalar_one_or_none()
        if lg_team is not None:
            if game.home_team_id == lg_team.id:
                opponent = session.get(Team, game.away_team_id)
            else:
                opponent = session.get(Team, game.home_team_id)
            if opponent is not None:
                return opponent.code
    return "OPP"

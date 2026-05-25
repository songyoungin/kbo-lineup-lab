"""Pydantic schemas for validating the LG Twins fixture JSON format."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _to_utc(dt: datetime) -> datetime:
    """tz-aware datetime을 UTC로 정규화한다. naive datetime은 예외를 발생시킨다."""
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(UTC)


class TeamFixture(BaseModel):
    """단일 팀 항목."""

    code: str
    name: str


class PlayerFixture(BaseModel):
    """단일 선수 항목."""

    external_id: str
    team_code: str
    name: str
    position: str


class GameFixture(BaseModel):
    """단일 경기 항목."""

    external_id: str
    game_date: date
    home_team_code: str
    away_team_code: str
    venue: str | None = None


class IngestionFixture(BaseModel):
    """픽스처 파일에 대한 인제스트 실행 메타데이터."""

    source: str
    started_at: datetime
    finished_at: datetime

    @field_validator("started_at", "finished_at")
    @classmethod
    def _normalize_tz(cls, v: datetime) -> datetime:
        return _to_utc(v)


class StatRowFixture(BaseModel):
    """한 선수의 스탯 행."""

    player_external_id: str
    stats: dict[str, float | int]


class StatSnapshotFixture(BaseModel):
    """특정 시점의 선수 스탯 스냅샷."""

    snapshot_at: datetime
    rows: list[StatRowFixture]

    @field_validator("snapshot_at")
    @classmethod
    def _normalize_tz(cls, v: datetime) -> datetime:
        return _to_utc(v)


class LineupRowFixture(BaseModel):
    """타순 내 단일 선수 슬롯."""

    batting_order: int = Field(ge=1, le=9)
    player_external_id: str
    position: str


class ActualLineupSnapshotFixture(BaseModel):
    """발표된 실제 선발 라인업 스냅샷."""

    team_code: str
    announced_at: datetime
    rows: list[LineupRowFixture]

    @field_validator("announced_at")
    @classmethod
    def _normalize_tz(cls, v: datetime) -> datetime:
        return _to_utc(v)


class BoxScoreRowFixture(BaseModel):
    """박스스코어 내 한 선수의 기록 행."""

    player_external_id: str
    at_bats: int | None = None
    hits: int | None = None
    runs: int | None = None
    rbis: int | None = None
    extra_stats_json: dict[str, float | int] | None = None
    innings_pitched: float | None = None


class BoxScoreSnapshotFixture(BaseModel):
    """경기 종료 후 박스스코어 스냅샷."""

    taken_at: datetime
    rows: list[BoxScoreRowFixture]

    @field_validator("taken_at")
    @classmethod
    def _normalize_tz(cls, v: datetime) -> datetime:
        return _to_utc(v)


class LineupLabFixture(BaseModel):
    """픽스처 JSON 파일 전체 구조 및 교차 참조 검증."""

    schema_version: Literal[1]
    teams: list[TeamFixture]
    players: list[PlayerFixture]
    game: GameFixture
    ingestion: IngestionFixture
    stat_snapshot: StatSnapshotFixture
    actual_lineup_snapshot: ActualLineupSnapshotFixture
    box_score_snapshot: BoxScoreSnapshotFixture

    @model_validator(mode="after")
    def validate_cross_references(self) -> LineupLabFixture:
        """팀 코드, 선수 external_id 등 교차 참조를 검증한다."""
        team_codes = {t.code for t in self.teams}
        player_external_ids = {p.external_id for p in self.players}

        # Players must reference valid team codes
        for player in self.players:
            if player.team_code not in team_codes:
                raise ValueError(
                    f"Player '{player.external_id}' references"
                    f" unknown team_code '{player.team_code}'"
                )

        # Game home/away teams must exist
        if self.game.home_team_code not in team_codes:
            raise ValueError(f"Game home_team_code '{self.game.home_team_code}' not in teams")
        if self.game.away_team_code not in team_codes:
            raise ValueError(f"Game away_team_code '{self.game.away_team_code}' not in teams")

        # Lineup snapshot team must exist
        if self.actual_lineup_snapshot.team_code not in team_codes:
            raise ValueError(
                f"Lineup snapshot team_code '{self.actual_lineup_snapshot.team_code}' not in teams"
            )

        # Stat snapshot rows must reference valid player external_ids
        for stat_row in self.stat_snapshot.rows:
            if stat_row.player_external_id not in player_external_ids:
                raise ValueError(
                    f"Stat row references unknown player '{stat_row.player_external_id}'"
                )

        # Lineup snapshot rows must reference valid player external_ids
        for lineup_row in self.actual_lineup_snapshot.rows:
            if lineup_row.player_external_id not in player_external_ids:
                raise ValueError(
                    f"Lineup row references unknown player '{lineup_row.player_external_id}'"
                )

        # Box score rows must reference valid player external_ids
        for bs_row in self.box_score_snapshot.rows:
            if bs_row.player_external_id not in player_external_ids:
                raise ValueError(
                    f"Box score row references unknown player '{bs_row.player_external_id}'"
                )

        return self

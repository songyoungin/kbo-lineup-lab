"""Tests for the `kbo-lab run` CLI command exit-code semantics.

Verifies that a KBO off-day (no LG game scheduled) exits 0 so the
ingestion-canary stays green, while genuine failures still exit 1.
"""

from __future__ import annotations

from datetime import date

import pytest
from typer.testing import CliRunner

import app.cli as cli_module
from app.cli import app as cli_app
from app.jobs.full_pipeline import FullPipelineResult

runner = CliRunner()


def test_run_exits_zero_on_off_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """No LG game scheduled (completed + games_found 0) exits 0, not a failure.

    Monkeypatches app.cli.run_full_pipeline to return an off-day FullPipelineResult
    (daily_status="completed", game_id=None, games_found=0).
    """

    def fake_run_full_pipeline(target_date: date) -> FullPipelineResult:
        return FullPipelineResult(
            target_date=target_date,
            daily_status="completed",
            teams_created=0,
            game_id=None,
            evaluation_run_id=None,
            postgame_review_run_id=None,
            games_found=0,
        )

    monkeypatch.setattr(cli_module, "run_full_pipeline", fake_run_full_pipeline)
    result = runner.invoke(cli_app, ["run", "--date", "2026-06-01"])
    assert result.exit_code == 0, result.output


def test_run_exits_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fully successful run exits 0.

    Monkeypatches app.cli.run_full_pipeline to return a succeeded FullPipelineResult
    (game_id, eval id present, games_found=1).
    """

    def fake_run_full_pipeline(target_date: date) -> FullPipelineResult:
        return FullPipelineResult(
            target_date=target_date,
            daily_status="completed",
            teams_created=10,
            game_id=1,
            evaluation_run_id=2,
            postgame_review_run_id=3,
            games_found=1,
        )

    monkeypatch.setattr(cli_module, "run_full_pipeline", fake_run_full_pipeline)
    result = runner.invoke(cli_app, ["run", "--date", "2026-05-30"])
    assert result.exit_code == 0, result.output


def test_run_exits_one_on_genuine_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine failure (daily failed, games_found 0) still exits 1.

    Monkeypatches app.cli.run_full_pipeline to return a failed FullPipelineResult
    (daily_status="failed", games_found=0).
    """

    def fake_run_full_pipeline(target_date: date) -> FullPipelineResult:
        return FullPipelineResult(
            target_date=target_date,
            daily_status="failed",
            teams_created=0,
            game_id=None,
            evaluation_run_id=None,
            postgame_review_run_id=None,
            games_found=0,
        )

    monkeypatch.setattr(cli_module, "run_full_pipeline", fake_run_full_pipeline)
    result = runner.invoke(cli_app, ["run", "--date", "2026-06-01"])
    assert result.exit_code == 1, result.output


def test_run_exits_one_on_analysis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A game present but analysis error still exits 1.

    An analysis failure (game found, evaluation errored) is a genuine failure
    even when games_found > 0 — the canary must alert.
    """

    def fake_run_full_pipeline(target_date: date) -> FullPipelineResult:
        return FullPipelineResult(
            target_date=target_date,
            daily_status="completed",
            teams_created=0,
            game_id=1,
            evaluation_run_id=None,
            postgame_review_run_id=None,
            games_found=1,
            error="500: boom",
        )

    monkeypatch.setattr(cli_module, "run_full_pipeline", fake_run_full_pipeline)
    result = runner.invoke(cli_app, ["run", "--date", "2026-06-01"])
    assert result.exit_code == 1, result.output

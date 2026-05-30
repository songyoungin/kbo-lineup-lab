"""Tests for the full-pipeline orchestration result type."""

from __future__ import annotations

from datetime import date

import pytest

from app.jobs.full_pipeline import FullPipelineResult


def test_full_pipeline_result_succeeded_true_when_all_present() -> None:
    """succeeded is True when the daily run completed and a game was ingested."""
    result = FullPipelineResult(
        target_date=date(2026, 5, 30),
        daily_status="completed",
        teams_created=10,
        game_id=1,
        evaluation_run_id=2,
        postgame_review_run_id=3,
    )
    assert result.succeeded is True
    text = result.summary()
    assert "2026-05-30" in text
    assert "completed" in text
    assert "eval_run=2" in text
    assert "postgame_run=3" in text


def test_full_pipeline_result_not_succeeded_when_daily_failed() -> None:
    """succeeded is False when the daily pipeline did not complete."""
    result = FullPipelineResult(
        target_date=date(2026, 5, 30),
        daily_status="failed",
        teams_created=0,
        game_id=None,
        evaluation_run_id=None,
        postgame_review_run_id=None,
    )
    assert result.succeeded is False
    assert "failed" in result.summary()


def test_full_pipeline_result_not_succeeded_when_no_game() -> None:
    """succeeded is False when the daily run completed but no LG game was found."""
    result = FullPipelineResult(
        target_date=date(2026, 5, 30),
        daily_status="completed",
        teams_created=0,
        game_id=None,
        evaluation_run_id=None,
        postgame_review_run_id=None,
    )
    assert result.succeeded is False


def test_run_cli_command_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """`kbo-lab run --date` echoes the summary and exits 0 on success."""
    from typer.testing import CliRunner

    import app.cli as cli_module
    from app.cli import app as cli_app

    captured: dict[str, date] = {}

    def fake_run_full_pipeline(target_date: date) -> FullPipelineResult:
        captured["date"] = target_date
        return FullPipelineResult(
            target_date=target_date,
            daily_status="completed",
            teams_created=10,
            game_id=1,
            evaluation_run_id=2,
            postgame_review_run_id=3,
        )

    monkeypatch.setattr(cli_module, "run_full_pipeline", fake_run_full_pipeline)
    result = CliRunner().invoke(cli_app, ["run", "--date", "2026-05-30"])

    assert result.exit_code == 0, result.output
    assert captured["date"] == date(2026, 5, 30)
    assert "eval_run=2" in result.output


def test_run_cli_command_fails_nonzero_when_no_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """`kbo-lab run --date` exits non-zero when no game was ingested."""
    from typer.testing import CliRunner

    import app.cli as cli_module
    from app.cli import app as cli_app

    def fake_run_full_pipeline(target_date: date) -> FullPipelineResult:
        return FullPipelineResult(
            target_date=target_date,
            daily_status="completed",
            teams_created=0,
            game_id=None,
            evaluation_run_id=None,
            postgame_review_run_id=None,
        )

    monkeypatch.setattr(cli_module, "run_full_pipeline", fake_run_full_pipeline)
    result = CliRunner().invoke(cli_app, ["run", "--date", "2026-05-30"])

    assert result.exit_code == 1
    assert "run 2026-05-30" in result.output

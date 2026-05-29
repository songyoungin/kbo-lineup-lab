"""kbo-lab CLI entry point."""

from __future__ import annotations

from datetime import date

import typer

from app.jobs.daily_pipeline import run_daily_pipeline
from app.jobs.postgame_pipeline import run_postgame_pipeline
from app.jobs.pregame_pipeline import run_pregame_pipeline

app = typer.Typer(help="LG Twins ingestion + evaluation pipeline runner.")


@app.command("ingest-daily")
def ingest_daily(
    date_arg: str = typer.Option(..., "--date", help="ISO date (YYYY-MM-DD) to ingest for."),
) -> None:
    """Collect the schedule and, for each LG game, lineup, stats, and box score."""
    target = date.fromisoformat(date_arg)
    result = run_daily_pipeline(target_date=target)
    typer.echo(result.summary())


@app.command("ingest-pregame")
def ingest_pregame(
    game_id: str = typer.Option(..., "--game-id"),
) -> None:
    """경기 라인업을 수집·정규화하고 프리게임 평가를 실행한다."""
    result = run_pregame_pipeline(game_id=game_id)
    typer.echo(result.summary())


@app.command("ingest-postgame")
def ingest_postgame(
    game_id: str = typer.Option(..., "--game-id"),
) -> None:
    """박스스코어를 수집·정규화하고 포스트게임 리뷰를 생성한다."""
    result = run_postgame_pipeline(game_id=game_id)
    typer.echo(result.summary())


def main() -> None:  # pragma: no cover — Typer's own entry-point shim
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

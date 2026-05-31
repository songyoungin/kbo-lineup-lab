"""Subprocess tests for the project-local Claude Code harness scripts.

The harness scripts live outside the apps/api package, so they are exercised
via subprocess (never imported) to keep them covered without polluting the
mypy/ruff scope.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS = REPO_ROOT / ".claude" / "harness"
HOOK = REPO_ROOT / ".claude" / "hooks" / "pretooluse_bash.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=cwd)


def test_record_and_check_audit_marker(tmp_path: Path) -> None:
    head = "deadbeef"
    rec = _run(
        [str(HARNESS / "record_audit.py"), "true", "true", "--root", str(tmp_path), "--head", head]
    )
    assert rec.returncode == 0, rec.stderr
    marker = json.loads((tmp_path / ".claude" / "harness" / ".audit-state.json").read_text())
    assert marker["git_head"] == head
    assert marker["structural_ok"] is True
    assert marker["semantic_ok"] is True

    fresh = _run(
        [str(HARNESS / "audit_state.py"), "check", "--root", str(tmp_path), "--head", head]
    )
    assert fresh.returncode == 0

    stale = _run(
        [str(HARNESS / "audit_state.py"), "check", "--root", str(tmp_path), "--head", "other"]
    )
    assert stale.returncode == 1


def test_audit_check_fails_when_semantic_false(tmp_path: Path) -> None:
    head = "abc123"
    _run(
        [str(HARNESS / "record_audit.py"), "true", "false", "--root", str(tmp_path), "--head", head]
    )
    chk = _run([str(HARNESS / "audit_state.py"), "check", "--root", str(tmp_path), "--head", head])
    assert chk.returncode == 1


def _init_fake_repo(root: Path) -> None:
    (root / ".git").mkdir()


def test_check_drift_passes_on_clean_repo(tmp_path: Path) -> None:
    _init_fake_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Repo\nNo tracked-path references here.\n")
    result = _run([str(HARNESS / "check_drift.py")], cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_drift_flags_missing_path(tmp_path: Path) -> None:
    _init_fake_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("See `apps/api/app/does_not_exist.py` for details.\n")
    result = _run([str(HARNESS / "check_drift.py")], cwd=tmp_path)
    assert result.returncode == 1
    assert "does_not_exist.py" in result.stdout


def test_check_drift_flags_unknown_cli_command(tmp_path: Path) -> None:
    _init_fake_repo(tmp_path)
    app_dir = tmp_path / "apps" / "api" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "cli.py").write_text('@app.command("run")\ndef run() -> None: ...\n')
    (tmp_path / "CLAUDE.md").write_text(
        "Entry point `apps/api/app/cli.py`. Use kbo-lab frobnicate to ingest.\n"
    )
    result = _run([str(HARNESS / "check_drift.py")], cwd=tmp_path)
    assert result.returncode == 1
    assert "frobnicate" in result.stdout


def test_check_drift_ignores_bare_scripts_path(tmp_path: Path) -> None:
    """A bare `scripts/...` reference (apps/api-relative) is not repo-root validated."""
    _init_fake_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("Run `scripts/seed_demo.py` from the api app dir.\n")
    result = _run([str(HARNESS / "check_drift.py")], cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr

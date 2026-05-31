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

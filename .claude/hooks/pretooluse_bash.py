"""Single PreToolUse(Bash) hook: repo guards + PR audit gate (stdlib only).

Fails OPEN: any internal error exits 0 (never hard-block all Bash usage).
Explicit rule violations exit 2 with a stderr message. Test-only env overrides:
HARNESS_ROOT, HARNESS_HEAD, HARNESS_BRANCH.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import audit_state  # noqa: E402  (sibling stdlib module)


def _deny(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def _branch(root: Path) -> str:
    override = os.environ.get("HARNESS_BRANCH")
    if override is not None:
        return override
    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, capture_output=True, text=True
    )
    return out.stdout.strip()


def _check_guards(command: str, root: Path) -> None:
    if re.search(r"\bgit\s+-C\b", command):
        _deny("Repo rule: do not use `git -C`. Run git from the working directory.")
    if re.search(r"\bgit\s+push\b.*(--force\b|\s-f\b)", command):
        _deny("Repo rule: force-push is blocked.")
    if re.search(r"\bgit\s+commit\b", command) and _branch(root) == "main":
        _deny("Repo rule: don't commit directly to main. Create a feature/fix branch first.")
    if re.search(r"(?<![\w./])(black|ruff|mypy)\b", command) and "pre-commit" not in command:
        _deny("Repo rule: run lint/type-check via `pre-commit run --all-files`, not raw black/ruff/mypy.")


def _check_pr_gate(command: str, root: Path) -> None:
    if re.search(r"\bgh\s+pr\s+create\b", command):
        head = os.environ.get("HARNESS_HEAD") or audit_state.current_head(root)
        if not audit_state.is_fresh_and_passing(root, head):
            _deny(
                "Harness audit stale or missing for current HEAD — "
                "run /harness-audit before creating a PR."
            )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # fail open
    command = str(payload.get("tool_input", {}).get("command", ""))
    if not command:
        return 0
    try:
        root = Path(os.environ.get("HARNESS_ROOT") or audit_state.find_repo_root(Path.cwd()))
        _check_guards(command, root)
        _check_pr_gate(command, root)
    except SystemExit:
        raise
    except Exception as exc:  # fail open on internal error
        print(f"pretooluse_bash hook warning (allowing): {exc}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())

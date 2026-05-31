"""Harness audit-state marker: read/write/freshness helpers (stdlib only)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    """Return the nearest ancestor of start containing a .git entry."""
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return start


def state_path(root: Path) -> Path:
    return root / ".claude" / "harness" / ".audit-state.json"


def current_head(root: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def read_state(root: Path) -> dict | None:
    path = state_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_state(root: Path, structural_ok: bool, semantic_ok: bool, head: str) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "git_head": head,
        "structural_ok": structural_ok,
        "semantic_ok": semantic_ok,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def is_fresh_and_passing(root: Path, head: str) -> bool:
    state = read_state(root)
    return bool(
        state
        and state.get("git_head") == head
        and state.get("structural_ok") is True
        and state.get("semantic_ok") is True
    )


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Harness audit-state helper")
    parser.add_argument("action", choices=["check"])
    parser.add_argument("--root", default=None)
    parser.add_argument("--head", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else find_repo_root(Path.cwd())
    head = args.head if args.head else current_head(root)
    return 0 if is_fresh_and_passing(root, head) else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

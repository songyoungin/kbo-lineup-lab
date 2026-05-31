"""Write the harness audit marker for the current HEAD (stdlib only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_state  # noqa: E402  (sibling stdlib module)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "ok", "pass"}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Record the harness audit marker")
    parser.add_argument("structural_ok")
    parser.add_argument("semantic_ok")
    parser.add_argument("--root", default=None)
    parser.add_argument("--head", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else audit_state.find_repo_root(Path.cwd())
    head = args.head if args.head else audit_state.current_head(root)
    structural = _parse_bool(args.structural_ok)
    semantic = _parse_bool(args.semantic_ok)
    audit_state.write_state(root, structural, semantic, head)
    print(f"audit marker written: head={head[:8]} structural={structural} semantic={semantic}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

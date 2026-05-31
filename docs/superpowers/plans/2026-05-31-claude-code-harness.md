# Project-Local Claude Code Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a committed project-local Claude Code harness (root `CLAUDE.md` + `.claude/` agents/commands/hooks) with a three-tier drift-detection system: a deterministic structural linter (CI + pre-commit), an on-demand semantic `harness-auditor` agent, and a `gh pr create` PreToolUse gate backed by a HEAD-bound audit marker.

**Architecture:** All executable harness pieces (`.claude/harness/*.py`, `.claude/hooks/*.py`) are stdlib-only scripts run with `python3` (no `uv`), so the PreToolUse hook is fast and CI needs no install. Shared marker logic lives in `audit_state.py`; the single PreToolUse(Bash) hook composes repo guards + the PR audit gate. Because the scripts live outside the `apps/api` package, they are covered by subprocess tests in `apps/api/tests/test_harness.py`.

**Tech Stack:** Python 3.13 stdlib, pytest (via `uv run`), GitHub Actions, Claude Code project config (`.claude/`). Reference spec: `docs/superpowers/specs/2026-05-31-claude-code-harness-design.md`.

**Working directory:** repo root is `/Users/serena/Documents/kbo-lineup-lab`. Run pytest from `apps/api`. Branch: `feature/claude-code-harness` (already created) — do NOT switch branches. Run git from the working directory (no `git -C`). Commit messages: English commitizen.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `.claude/harness/audit_state.py` | Marker read/write/freshness + repo-root/HEAD helpers (shared) |
| `.claude/harness/record_audit.py` | CLI to write the marker (used by `/harness-audit`) |
| `.claude/harness/check_drift.py` | Deterministic structural drift linter (CLI) |
| `.claude/hooks/pretooluse_bash.py` | Single PreToolUse(Bash) hook: repo guards + PR audit gate |
| `.claude/settings.json` | Registers the PreToolUse(Bash) hook |
| `.github/workflows/harness.yaml` | CI structural-drift gate |
| `.gitignore` | Ignore the local audit marker |
| `.claude/agents/lineup-model-reviewer.md` | Determinism/boundary reviewer |
| `.claude/agents/ingestion-helper.md` | Ingestion CLI/pipeline helper |
| `.claude/agents/web-ui-reviewer.md` | Next.js web reviewer |
| `.claude/agents/harness-auditor.md` | Semantic drift auditor |
| `.claude/commands/lab-check.md` | Run pytest + pre-commit |
| `.claude/commands/lab-ingest.md` | Run kbo-lab ingest commands |
| `.claude/commands/harness-audit.md` | Structural + semantic audit, write marker |
| `CLAUDE.md` | Repo conventions anchor |
| `apps/api/tests/test_harness.py` | Subprocess tests for the scripts/hook |

---

## Task 1: Audit-state marker (`audit_state.py`, `record_audit.py`) + gitignore

**Files:**
- Create: `.claude/harness/audit_state.py`
- Create: `.claude/harness/record_audit.py`
- Modify: `.gitignore`
- Test: `apps/api/tests/test_harness.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_harness.py`:

```python
"""Subprocess tests for the project-local Claude Code harness scripts.

The harness scripts live outside the apps/api package, so they are exercised
via subprocess (never imported) to keep them covered without polluting the
mypy/ruff scope.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS = REPO_ROOT / ".claude" / "harness"
HOOK = REPO_ROOT / ".claude" / "hooks" / "pretooluse_bash.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, cwd=cwd
    )


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

    fresh = _run([str(HARNESS / "audit_state.py"), "check", "--root", str(tmp_path), "--head", head])
    assert fresh.returncode == 0

    stale = _run([str(HARNESS / "audit_state.py"), "check", "--root", str(tmp_path), "--head", "other"])
    assert stale.returncode == 1


def test_audit_check_fails_when_semantic_false(tmp_path: Path) -> None:
    head = "abc123"
    _run(
        [str(HARNESS / "record_audit.py"), "true", "false", "--root", str(tmp_path), "--head", head]
    )
    chk = _run([str(HARNESS / "audit_state.py"), "check", "--root", str(tmp_path), "--head", head])
    assert chk.returncode == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_harness.py -k audit -v`
Expected: FAIL (scripts do not exist yet → non-zero returncode / FileNotFound).

- [ ] **Step 3: Create `.claude/harness/audit_state.py`**

```python
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
```

- [ ] **Step 4: Create `.claude/harness/record_audit.py`**

```python
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
```

- [ ] **Step 5: Add the marker to `.gitignore`**

Append this line to `/Users/serena/Documents/kbo-lineup-lab/.gitignore`:

```
.claude/harness/.audit-state.json
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_harness.py -k audit -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add .claude/harness/audit_state.py .claude/harness/record_audit.py .gitignore apps/api/tests/test_harness.py
git commit -m "feat(harness): add audit-state marker scripts"
```

---

## Task 2: Structural drift linter (`check_drift.py`)

**Files:**
- Create: `.claude/harness/check_drift.py`
- Test: `apps/api/tests/test_harness.py`

- [ ] **Step 1: Add the failing tests**

Append to `apps/api/tests/test_harness.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_harness.py -k drift -v`
Expected: FAIL (check_drift.py does not exist).

- [ ] **Step 3: Create `.claude/harness/check_drift.py`**

```python
"""Deterministic structural drift linter for the Claude Code harness (stdlib only).

Exit codes: 0 = no drift, 1 = drift found, 2 = checker error.

Two CLAUDE.md marker lines are part of the contract and parsed verbatim
(case-insensitive):
  "Pre-commit runs: <comma-separated hook ids>"
  "CI workflows: <comma-separated workflow names>"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_DOC_GLOBS = (
    "CLAUDE.md",
    ".claude/agents/*.md",
    ".claude/commands/*.md",
    ".claude/skills/*/SKILL.md",
)
_PATH_TOKEN = re.compile(r"`?((?:apps/|docs/|\.claude/|\.github/|scripts/)[\w./\-]+)`?")
_ENV_TOKEN = re.compile(r"\b((?:KBO|LINEUP|OPENAI)_[A-Z0-9_]+)\b")


def find_repo_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / ".git").exists():
            return parent
    return start


def _doc_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in _DOC_GLOBS:
        if "*" in pattern:
            files.extend(sorted(root.glob(pattern)))
        else:
            candidate = root / pattern
            if candidate.exists():
                files.append(candidate)
    return files


def check_referenced_paths(root: Path) -> list[str]:
    findings: list[str] = []
    for doc in _doc_files(root):
        text = doc.read_text(encoding="utf-8")
        for match in _PATH_TOKEN.finditer(text):
            raw = match.group(1).rstrip("/.,)")
            if "*" in raw:
                continue
            if not (root / raw).exists():
                findings.append(f"{doc.relative_to(root)}: references missing path '{raw}'")
    return findings


def check_cli_commands(root: Path) -> list[str]:
    cli = root / "apps/api/app/cli.py"
    if not cli.exists():
        return []
    declared = set(re.findall(r'@app\.command\("([\w\-]+)"\)', cli.read_text(encoding="utf-8")))
    findings: list[str] = []
    for doc in _doc_files(root):
        for sub in re.findall(r"kbo-lab\s+([a-z][\w\-]+)", doc.read_text(encoding="utf-8")):
            if sub not in declared:
                findings.append(
                    f"{doc.relative_to(root)}: unknown CLI command 'kbo-lab {sub}' "
                    f"(declared: {sorted(declared)})"
                )
    return findings


def check_python_version(root: Path) -> list[str]:
    claude = root / "CLAUDE.md"
    pyproject = root / "apps/api/pyproject.toml"
    if not claude.exists() or not pyproject.exists():
        return []
    m = re.search(r'requires-python\s*=\s*"[^0-9]*([0-9]+\.[0-9]+)', pyproject.read_text())
    if not m:
        return []
    declared = m.group(1)
    findings: list[str] = []
    for ver in re.findall(r"Python\s+([0-9]+\.[0-9]+)", claude.read_text()):
        if ver != declared:
            findings.append(f"CLAUDE.md: states Python {ver} but requires-python is {declared}")
    return findings


def check_lint_tools(root: Path) -> list[str]:
    claude = root / "CLAUDE.md"
    config = root / ".pre-commit-config.yaml"
    if not claude.exists() or not config.exists():
        return []
    hook_ids = set(re.findall(r"-\s*id:\s*([\w\-]+)", config.read_text()))
    findings: list[str] = []
    for line in re.findall(r"(?i)pre-commit runs:\s*([^\n]+)", claude.read_text()):
        for tool in re.findall(r"[A-Za-z][\w\-]+", line):
            if tool not in hook_ids:
                findings.append(
                    f"CLAUDE.md: claims pre-commit runs '{tool}' but no such hook id exists"
                )
    return findings


def check_env_vars(root: Path) -> list[str]:
    example = root / "apps/api/.env.example"
    if not example.exists():
        return []
    documented = set(_ENV_TOKEN.findall(example.read_text()))
    findings: list[str] = []
    for doc in _doc_files(root):
        for var in _ENV_TOKEN.findall(doc.read_text(encoding="utf-8")):
            if var not in documented:
                findings.append(
                    f"{doc.relative_to(root)}: env var '{var}' not in apps/api/.env.example"
                )
    return findings


def check_frontmatter(root: Path) -> list[str]:
    findings: list[str] = []
    md_files: list[Path] = []
    for pattern in (".claude/agents/*.md", ".claude/commands/*.md", ".claude/skills/*/SKILL.md"):
        md_files.extend(sorted(root.glob(pattern)))
    for md in md_files:
        text = md.read_text(encoding="utf-8")
        block = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        if not block:
            findings.append(f"{md.relative_to(root)}: missing YAML frontmatter")
            continue
        front = block.group(1)
        if not re.search(r"(?m)^name:\s*\S", front) and "skills/" not in str(md):
            findings.append(f"{md.relative_to(root)}: frontmatter missing 'name'")
        if not re.search(r"(?m)^description:\s*\S", front):
            findings.append(f"{md.relative_to(root)}: frontmatter missing 'description'")
    return findings


def check_workflows(root: Path) -> list[str]:
    claude = root / "CLAUDE.md"
    wf_dir = root / ".github/workflows"
    if not claude.exists() or not wf_dir.exists():
        return []
    names: set[str] = set()
    for wf in wf_dir.glob("*.y*ml"):
        m = re.search(r"(?m)^name:\s*(\S+)", wf.read_text())
        if m:
            names.add(m.group(1))
    findings: list[str] = []
    for line in re.findall(r"(?i)CI workflows:\s*([^\n]+)", claude.read_text()):
        for token in re.findall(r"[A-Za-z][\w\-]+", line):
            if token not in names:
                findings.append(f"CLAUDE.md: references CI workflow '{token}' not found in {wf_dir.name}")
    return findings


_CHECKS = (
    check_referenced_paths,
    check_cli_commands,
    check_python_version,
    check_lint_tools,
    check_env_vars,
    check_frontmatter,
    check_workflows,
)


def run_all(root: Path) -> list[str]:
    findings: list[str] = []
    for check in _CHECKS:
        findings.extend(check(root))
    return findings


def main() -> int:
    try:
        root = find_repo_root(Path.cwd())
        findings = run_all(root)
    except Exception as exc:  # checker error, distinct from drift
        print(f"check_drift error: {exc}", file=sys.stderr)
        return 2
    if findings:
        print("Harness drift detected:")
        for f in findings:
            print(f"  - {f}")
        return 1
    print("Harness drift check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_harness.py -k drift -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add .claude/harness/check_drift.py apps/api/tests/test_harness.py
git commit -m "feat(harness): add structural drift linter"
```

---

## Task 3: PreToolUse(Bash) hook + settings.json

**Files:**
- Create: `.claude/hooks/pretooluse_bash.py`
- Create: `.claude/settings.json`
- Test: `apps/api/tests/test_harness.py`

- [ ] **Step 1: Add the failing tests**

Append to `apps/api/tests/test_harness.py`:

```python
def _hook(command: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_input": {"command": command}})
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=env
    )


def test_hook_blocks_commit_on_main(tmp_path: Path) -> None:
    r = _hook("git commit -m x", {"HARNESS_ROOT": str(tmp_path), "HARNESS_BRANCH": "main"})
    assert r.returncode == 2
    assert "main" in r.stderr


def test_hook_allows_commit_on_feature_branch(tmp_path: Path) -> None:
    r = _hook("git commit -m x", {"HARNESS_ROOT": str(tmp_path), "HARNESS_BRANCH": "feature/x"})
    assert r.returncode == 0


def test_hook_blocks_git_dash_c(tmp_path: Path) -> None:
    r = _hook("git -C /x status", {"HARNESS_ROOT": str(tmp_path), "HARNESS_BRANCH": "feature/x"})
    assert r.returncode == 2


def test_hook_blocks_force_push(tmp_path: Path) -> None:
    r = _hook("git push --force origin x", {"HARNESS_ROOT": str(tmp_path), "HARNESS_BRANCH": "feature/x"})
    assert r.returncode == 2


def test_hook_blocks_raw_ruff(tmp_path: Path) -> None:
    r = _hook("uv run ruff check .", {"HARNESS_ROOT": str(tmp_path), "HARNESS_BRANCH": "feature/x"})
    assert r.returncode == 2
    assert "pre-commit" in r.stderr


def test_hook_blocks_gh_pr_create_without_marker(tmp_path: Path) -> None:
    r = _hook(
        "gh pr create --fill",
        {"HARNESS_ROOT": str(tmp_path), "HARNESS_BRANCH": "feature/x", "HARNESS_HEAD": "h1"},
    )
    assert r.returncode == 2
    assert "/harness-audit" in r.stderr


def test_hook_allows_gh_pr_create_with_fresh_marker(tmp_path: Path) -> None:
    _run([str(HARNESS / "record_audit.py"), "true", "true", "--root", str(tmp_path), "--head", "h1"])
    r = _hook(
        "gh pr create --fill",
        {"HARNESS_ROOT": str(tmp_path), "HARNESS_BRANCH": "feature/x", "HARNESS_HEAD": "h1"},
    )
    assert r.returncode == 0


def test_hook_allows_plain_command(tmp_path: Path) -> None:
    r = _hook("ls -la", {"HARNESS_ROOT": str(tmp_path), "HARNESS_BRANCH": "feature/x"})
    assert r.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_harness.py -k hook -v`
Expected: FAIL (pretooluse_bash.py does not exist).

- [ ] **Step 3: Create `.claude/hooks/pretooluse_bash.py`**

```python
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
```

- [ ] **Step 4: Create `.claude/settings.json`**

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/pretooluse_bash.py\""
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_harness.py -k hook -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add .claude/hooks/pretooluse_bash.py .claude/settings.json apps/api/tests/test_harness.py
git commit -m "feat(harness): add PreToolUse bash hook with guards and PR audit gate"
```

---

## Task 4: CI structural-drift workflow

**Files:**
- Create: `.github/workflows/harness.yaml`

- [ ] **Step 1: Create the workflow**

`/Users/serena/Documents/kbo-lineup-lab/.github/workflows/harness.yaml`:

```yaml
name: harness
on:
  push:
  pull_request:
jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: Structural harness drift check
        run: python3 .claude/harness/check_drift.py
```

- [ ] **Step 2: Verify it runs locally the same way CI will**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && python3 .claude/harness/check_drift.py`
Expected: exit 0 with "Harness drift check passed." (No CLAUDE.md/agents/commands exist yet, so there is nothing to contradict — the checker passes on the current tree.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/harness.yaml
git commit -m "ci(harness): add structural drift gate workflow"
```

---

## Task 5: Domain + auditor agents

**Files:**
- Create: `.claude/agents/lineup-model-reviewer.md`
- Create: `.claude/agents/ingestion-helper.md`
- Create: `.claude/agents/web-ui-reviewer.md`
- Create: `.claude/agents/harness-auditor.md`

No unit tests (markdown config); correctness is enforced by `check_drift.py` in Task 8. Every file MUST have `name` + `description` frontmatter and only reference paths that exist.

- [ ] **Step 1: Create `.claude/agents/lineup-model-reviewer.md`**

```markdown
---
name: lineup-model-reviewer
description: Use when reviewing changes under apps/api/app/lineup_model/ or apps/api/app/services/lineup_evaluator.py — verifies scoring and defensive position assignment stay deterministic, output_hash stability is preserved, and the deterministic-vs-LLM boundary holds.
tools: Read, Grep, Glob, Bash
---

You review changes to the KBO lineup scoring model. The deterministic engine is the source of truth.

Check every change for:
- **Determinism**: no wall-clock, randomness, or ordering instability introduced into scoring or position assignment in apps/api/app/lineup_model/. Tie-breaks must stay deterministic (e.g., by ascending player_id).
- **output_hash stability**: when the LLM batting-order path is disabled (the default), the recommended lineup and its output_hash must be byte-identical to the prior rule-based behavior. Confirm tests in apps/api/tests/test_recommendation.py still pass.
- **Deterministic-vs-LLM boundary**: the LLM layer in apps/api/app/lineup_model/batting_order/ may only reorder the nine already-selected players. Player scoring and position assignment must remain deterministic and must not be delegated to the LLM.
- **Tests**: new behavior has tests; fakes are injected for any provider (no real API calls).

Report findings as Critical/Important/Minor with file:line references. Do not modify code — review only.
```

- [ ] **Step 2: Create `.claude/agents/ingestion-helper.md`**

```markdown
---
name: ingestion-helper
description: Use when running or debugging KBO data ingestion — the kbo-lab ingest-daily / ingest-pregame / ingest-postgame CLI commands, apps/api/app/jobs/daily_pipeline.py, or the ingestion-canary workflow.
tools: Read, Grep, Glob, Bash
---

You help run and debug KBO data ingestion.

Key facts:
- The CLI is `kbo-lab` (apps/api/app/cli.py): subcommands bootstrap, run, ingest-daily, ingest-pregame, ingest-postgame. Run from apps/api via `uv run kbo-lab <sub>`.
- The orchestrator is apps/api/app/jobs/daily_pipeline.py.
- For database setup, follow the running-supabase-dev skill (real Supabase) or running-fixture-demo skill (SQLite fixtures).
- KBO_DATABASE_URL configures the database; never hardcode secrets.

When debugging, start by reproducing with the smallest ingest subcommand, inspect logs, and confirm which database (fixture vs Supabase) is in use before proposing changes.
```

- [ ] **Step 3: Create `.claude/agents/web-ui-reviewer.md`**

```markdown
---
name: web-ui-reviewer
description: Use when reviewing changes under apps/web (Next.js 16) — runs the web lint/format/build checks and looks for obvious UI and data-fetch regressions.
tools: Read, Grep, Glob, Bash
---

You review changes to the Next.js 16 web app in apps/web.

For any web change:
- Run `npm run lint`, `npm run format:check`, and `npm run build` from apps/web and report failures.
- Check that client components which fetch data handle empty/loading states (the pregame "선수 비교" panel has rendered empty before — see the running-supabase-dev skill).
- Flag accessibility and obvious layout regressions.

Report findings as Critical/Important/Minor with file references. Review only — do not modify code.
```

- [ ] **Step 4: Create `.claude/agents/harness-auditor.md`**

```markdown
---
name: harness-auditor
description: Use via /harness-audit to detect semantic drift between the Claude Code harness (CLAUDE.md, .claude/agents, .claude/commands, .claude/hooks, .claude/skills) and the actual codebase — divergences a structural linter cannot catch. Returns a semantic_ok verdict with findings.
tools: Read, Grep, Glob, Bash
---

You audit the project-local Claude Code harness for SEMANTIC drift — claims that are structurally valid (paths exist, names parse) but no longer describe how the code actually works.

Read CLAUDE.md, .claude/agents/*, .claude/commands/*, .claude/hooks/*, and .claude/skills/*, then verify against the real codebase:
- Do described workflows still match the code (e.g., does the LLM-batting-order description still reflect apps/api/app/lineup_model/batting_order/)?
- Do agent/command instructions reference commands and flows that still behave as described?
- Are stated invariants (deterministic scoring, off-by-default LLM) still true in the code?
- Are there NEW major areas of the codebase the harness should mention but doesn't?

The deterministic structural linter (.claude/harness/check_drift.py) already covers missing paths, unknown CLI subcommands, version/tool/env/frontmatter/workflow consistency — do NOT duplicate it. Focus on meaning.

End your report with a single line exactly: `SEMANTIC_OK: true` or `SEMANTIC_OK: false`, followed by a bulleted findings list (empty if none).
```

- [ ] **Step 5: Commit**

```bash
git add .claude/agents/
git commit -m "feat(harness): add domain reviewer agents and semantic harness-auditor"
```

---

## Task 6: Workflow commands

**Files:**
- Create: `.claude/commands/lab-check.md`
- Create: `.claude/commands/lab-ingest.md`
- Create: `.claude/commands/harness-audit.md`

- [ ] **Step 1: Create `.claude/commands/lab-check.md`**

```markdown
---
description: Run the API test suite and pre-commit across the repo, then report results.
---

Run both checks and summarize results (do not fix issues unless asked):

1. From `apps/api`: `uv run pytest -q`
2. From the repo root: `pre-commit run --all-files`

Report failures with file/line and the failing hook/test name.
```

- [ ] **Step 2: Create `.claude/commands/lab-ingest.md`**

```markdown
---
description: Run the kbo-lab data ingestion commands against the configured database.
---

Run KBO ingestion using the `kbo-lab` CLI from `apps/api` (e.g. `uv run kbo-lab ingest-daily`). Subcommands: ingest-daily, ingest-pregame, ingest-postgame.

Before running, confirm which database is configured (KBO_DATABASE_URL) — for the real Supabase database follow the running-supabase-dev skill; for local SQLite fixtures follow the running-fixture-demo skill. Report what was ingested and any errors.
```

- [ ] **Step 3: Create `.claude/commands/harness-audit.md`**

```markdown
---
description: Audit the Claude Code harness for drift (structural + semantic) and record the audit marker.
---

Audit the harness and record the result so PR creation is unblocked:

1. Run `python3 .claude/harness/check_drift.py`. Treat exit 0 as STRUCTURAL=true, non-zero as STRUCTURAL=false (report the findings).
2. Launch the harness-auditor agent to assess semantic drift. Read its final `SEMANTIC_OK: true|false` line as SEMANTIC.
3. Record the marker for the current HEAD: `python3 .claude/harness/record_audit.py <STRUCTURAL> <SEMANTIC>` (pass `true`/`false`).
4. Report all structural and semantic findings.

Note: the `gh pr create` hook blocks PR creation unless both STRUCTURAL and SEMANTIC are true for the current HEAD. Any new commit invalidates the marker — re-run this command before opening a PR.
```

- [ ] **Step 4: Commit**

```bash
git add .claude/commands/
git commit -m "feat(harness): add lab-check, lab-ingest, harness-audit commands"
```

---

## Task 7: Root `CLAUDE.md`

**Files:**
- Create: `CLAUDE.md`

This is the conventions anchor. It MUST be drift-clean: every `apps/`, `docs/`, `.claude/`, `.github/`, `scripts/` path token must exist; every `kbo-lab <sub>` must be a real subcommand; the `Pre-commit runs:` and `CI workflows:` marker lines must list only real hook ids / workflow names; mentioned env vars must be in `apps/api/.env.example`; Python version must match `requires-python` (3.13).

- [ ] **Step 1: Create `CLAUDE.md`**

```markdown
# KBO Lineup Lab — Project Guide

## Layout
Monorepo. `apps/api` is the FastAPI backend (Python 3.13, managed with `uv`); `apps/web` is the Next.js 16 frontend. Run API commands from `apps/api`.

## Running locally
- Fixtures (SQLite sample data): follow the running-fixture-demo skill.
- Real Supabase Postgres: follow the running-supabase-dev skill.
- CLI: `kbo-lab` (`apps/api/app/cli.py`) — subcommands `bootstrap`, `run`, `ingest-daily`, `ingest-pregame`, `ingest-postgame`. Invoke via `uv run kbo-lab <sub>` from `apps/api`.

## Conventions
- English for code, docs, comments, and commit messages.
- Commit messages follow commitizen; branches use `feature/`, `fix/`, `chore/`, `refactor/`, `docs/`.
- Never use `git -C`; run git from the working directory.
- Secrets come from env / `.env` (gitignored), never hardcoded. Templates live in `apps/api/.env.example` (e.g. `LINEUP_LLM_ENABLED`, `OPENAI_API_KEY`).

## Quality
- Tests: `uv run pytest` from `apps/api`.
- Lint/type-check ONLY through pre-commit — never invoke black/ruff/mypy directly. Pre-commit runs: ruff, mypy, bandit, vulture.
- CI workflows: test, pre-commit, ingestion-canary, harness.

## Architecture invariant
Deterministic scoring and defensive position assignment in `apps/api/app/lineup_model/` are the source of truth and must stay deterministic (stable `output_hash`). The LLM batting-order layer in `apps/api/app/lineup_model/batting_order/` is additive and OFF by default (`LINEUP_LLM_ENABLED`); on any failure it falls back to the deterministic order.

## Harness discipline
This repo ships a Claude Code harness: this `CLAUDE.md`, plus agents (`.claude/agents/`), commands (`.claude/commands/`), a hook (`.claude/hooks/pretooluse_bash.py`), and drift tooling (`.claude/harness/check_drift.py`). When you change paths, commands, versions, or architecture, update the harness to match. Structural drift is enforced by `.claude/harness/check_drift.py` (pre-commit + the harness CI workflow); semantic drift is checked by the `/harness-audit` command, which also gates `gh pr create`.
```

- [ ] **Step 2: Verify drift-clean against the real repo**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && python3 .claude/harness/check_drift.py`
Expected: exit 0, "Harness drift check passed." If any finding prints, fix the offending reference in `CLAUDE.md` (or the agent/command file) so the claim matches reality, then re-run until clean.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(harness): add root CLAUDE.md conventions anchor"
```

---

## Task 8: Wire drift into pre-commit + full integration verification

**Files:**
- Modify: `.pre-commit-config.yaml`

- [ ] **Step 1: Add a local pre-commit hook for structural drift**

Add this repo to `/Users/serena/Documents/kbo-lineup-lab/.pre-commit-config.yaml` under `repos:` (a `local` repo using the system Python so no extra install is needed):

```yaml
  - repo: local
    hooks:
      - id: harness-drift
        name: harness structural drift
        entry: python3 .claude/harness/check_drift.py
        language: system
        pass_filenames: false
        always_run: true
```

- [ ] **Step 2: Run the drift hook via pre-commit**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && pre-commit run harness-drift --all-files`
Expected: Passed. If it fails, the printed findings name the offending file/reference — fix the harness file so the claim is true, then re-run.

- [ ] **Step 3: Run the full test suite**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest -q`
Expected: all tests pass (367 prior + the new `test_harness.py` tests).

- [ ] **Step 4: Run all pre-commit hooks**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && pre-commit run --all-files`
Expected: all hooks pass (including the new `harness-drift`). Re-stage and re-run if any auto-fixer modifies files.

- [ ] **Step 5: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "ci(harness): enforce structural drift check in pre-commit"
```

---

## Notes for the implementer

- **Why scripts live in `.claude/` not `apps/api`**: the hook runs on every Bash call and must be fast (stdlib `python3`, no `uv`); CI runs the drift check with no project install. They are covered by subprocess tests in `apps/api/tests/test_harness.py`, so they are not unreachable code.
- **Marker freshness = HEAD binding**: `is_fresh_and_passing` requires `git_head == current HEAD`, so any commit after an audit invalidates the marker — this is what makes the semantic audit "enforced right before PR creation."
- **Fail-open hook**: the PreToolUse hook must never hard-block all Bash usage on its own bug; only explicit rule violations exit 2.
- **Drift-linter marker contract**: `CLAUDE.md` must contain the literal lines `Pre-commit runs: <ids>` and `CI workflows: <names>` (case-insensitive) listing only real hook ids / workflow names, or `check_lint_tools`/`check_workflows` will report drift.
- **Do not** reference the gitignored `.claude/harness/.audit-state.json` from `CLAUDE.md`/agents/commands — it will not exist in a fresh CI checkout and would trip the path check.
- **Out of scope**: plugin packaging; extending ruff/mypy scope to `.claude/`; putting semantic audit in CI.
```

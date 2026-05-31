"""Deterministic structural drift linter for the Claude Code harness (stdlib only).

Exit codes: 0 = no drift, 1 = drift found, 2 = checker error.

Two CLAUDE.md marker lines are part of the contract and parsed verbatim
(case-insensitive):
  "Pre-commit runs: <comma-separated hook ids>"
  "CI workflows: <comma-separated workflow names>"
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_DOC_GLOBS = (
    "CLAUDE.md",
    ".claude/agents/*.md",
    ".claude/commands/*.md",
    ".claude/skills/*/SKILL.md",
)
# Only repo-root-relative prefixes are validated. Bare ``scripts/...`` references
# are intentionally excluded: scripts live under ``apps/api/scripts/`` and are
# cited apps/api-relative in skills, so they would resolve falsely at the repo root.
_PATH_TOKEN = re.compile(r"`?((?:apps/|docs/|\.claude/|\.github/)[\w./\-]+)`?")
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


def _git_ignored(root: Path, rel: str) -> bool:
    """Return True if rel is git-ignored under root (runtime/secret files like
    .env or *.db legitimately referenced by docs but absent in a fresh checkout)."""
    result = subprocess.run(
        ["git", "check-ignore", rel], cwd=root, capture_output=True, text=True
    )
    return result.returncode == 0


def check_referenced_paths(root: Path) -> list[str]:
    findings: list[str] = []
    for doc in _doc_files(root):
        text = doc.read_text(encoding="utf-8")
        for match in _PATH_TOKEN.finditer(text):
            raw = match.group(1).rstrip("/.,)")
            if "*" in raw:
                continue
            if not (root / raw).exists() and not _git_ignored(root, raw):
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
        # Only agents require a `name`; slash commands are named by filename and
        # skills carry their own name already.
        if "/agents/" in str(md) and not re.search(r"(?m)^name:\s*\S", front):
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

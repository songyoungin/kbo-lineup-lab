# Project-Local Claude Code Harness — Design

- Date: 2026-05-31
- Status: Proposed
- Scope: One implementation plan

## 1. Summary

Add a project-local Claude Code harness to the repo so any agent (or person)
running Claude Code in `kbo-lineup-lab` inherits the project's conventions,
domain helpers, quality gates, and automation. The harness lives under version
control as a root `CLAUDE.md` plus `.claude/` components (settings, agents,
commands, hooks) alongside the existing `.claude/skills/`.

A first-class concern is **harness drift** — the risk that the harness
documents paths, commands, versions, or architecture that no longer match the
codebase. The harness therefore ships with a self-auditing mechanism in three
tiers:

- **Structural drift** — a deterministic linter (`check_drift.py`) enforced as
  a CI gate and a pre-commit hook.
- **Semantic drift** — an LLM `harness-auditor` agent run via `/harness-audit`.
- **PR gate** — a `gh pr create` PreToolUse hook that blocks PR creation unless
  a fresh, passing audit marker exists for the current HEAD.

## 2. Goals / Non-goals

Goals:
- Repo conventions captured once in `CLAUDE.md` (the per-session anchor).
- Domain agents for the areas of this codebase that benefit from focused review.
- Quality-gate hooks enforcing the few hard repo rules.
- Repo-specific workflow commands (not duplicating existing global skills).
- A drift-detection system so the harness cannot silently rot.

Non-goals:
- Distributable plugin packaging (`plugin.json`, marketplace) — project-local only.
- Re-implementing global skills already available (`commit-helper`, `pr-helper`,
  `save-progress`, etc.). The harness references/uses them, not replaces them.
- Putting the LLM `harness-auditor` in CI (cost/non-determinism). Semantic audit
  is on-demand + the PR gate.

## 3. Decisions (from brainstorming)

| Topic | Decision |
| --- | --- |
| Form | Project-local `.claude/` + root `CLAUDE.md`, committed |
| Breadth | Comprehensive (approach B): CLAUDE.md + agents + hooks + commands |
| Drift checker | Added as a first-class component |
| Structural drift | Deterministic linter; CI gate + pre-commit |
| Semantic drift | LLM agent, on-demand via `/harness-audit` |
| Semantic enforcement | Gated right before PR creation via a `gh pr create` hook + marker |
| Avoid duplication | Reuse existing global skills/commands; harness adds repo-specific only |

## 4. Directory Layout

```
kbo-lineup-lab/
├── CLAUDE.md                          # repo conventions anchor (new)
├── .claude/
│   ├── settings.json                  # registers the PreToolUse(Bash) hook (new)
│   ├── skills/                        # existing: running-fixture-demo, running-supabase-dev
│   ├── agents/                        # new
│   │   ├── lineup-model-reviewer.md
│   │   ├── ingestion-helper.md
│   │   ├── web-ui-reviewer.md
│   │   └── harness-auditor.md
│   ├── commands/                      # new
│   │   ├── lab-check.md
│   │   ├── lab-ingest.md
│   │   └── harness-audit.md
│   ├── hooks/                         # new
│   │   └── pretooluse_bash.py         # single PreToolUse(Bash): guards + PR audit gate
│   └── harness/                       # new (stdlib-only scripts)
│       ├── check_drift.py             # deterministic structural linter (CLI)
│       ├── audit_state.py             # marker read/write/freshness helpers
│       ├── record_audit.py            # CLI to write the marker
│       └── .audit-state.json          # local marker (gitignored)
├── .github/workflows/harness.yaml     # CI: run check_drift.py (structural gate) (new)
└── apps/api/tests/test_harness.py     # subprocess tests for the scripts (new)
```

All `.claude/harness/` and `.claude/hooks/` scripts are **stdlib-only** and run
with `python3` (no `uv`), so the PreToolUse hook adds negligible latency to Bash
calls and CI needs no project install to run the structural check.

## 5. Components

### 5.1 `CLAUDE.md` (root)

The session anchor. Sections (kept concise, all facts verified against the repo):
- **Layout**: monorepo — `apps/api` (FastAPI, Python 3.13, `uv`), `apps/web`
  (Next.js 16). Run API commands from `apps/api`.
- **Running locally**: defer to skills `running-fixture-demo` (SQLite fixtures)
  and `running-supabase-dev` (real Supabase). The `kbo-lab` CLI exposes
  `bootstrap`, `run`, `ingest-daily`, `ingest-pregame`, `ingest-postgame`.
- **Conventions**: English for code/docs/comments/commits; commitizen messages;
  branch naming (`feature/`, `fix/`, `chore/`, `refactor/`, `docs/`); never use
  `git -C` (run git from the working directory).
- **Quality**: run `uv run pytest` from `apps/api`; lint/type-check ONLY via
  `pre-commit run --all-files` (never invoke `black`/`ruff`/`mypy` directly). CI
  workflows: `test`, `pre-commit`, `ingestion-canary`, `harness`.
- **Architecture invariant**: deterministic scoring and defensive position
  assignment in `apps/api/app/lineup_model/` are the source of truth and must
  stay deterministic (stable `output_hash`). The LLM batting-order layer
  (`app/lineup_model/batting_order/`) is additive and OFF by default
  (`LINEUP_LLM_ENABLED`); on any failure it falls back to the deterministic order.
- **Secrets**: read from env / `.env` (gitignored); never hardcode. `OPENAI_API_KEY`
  etc. live in `apps/api/.env.example` as blank templates.
- **Harness discipline**: when you change paths, commands, versions, or
  architecture, update the harness (CLAUDE.md / agents / commands). Drift is
  enforced by `check_drift.py` (CI + pre-commit) and `/harness-audit`.

### 5.2 Agents (`.claude/agents/*.md`)

Each is a markdown file with frontmatter (`name`, `description`, `tools`) and a
focused system prompt. `description` is written to trigger the agent at the
right time.

- **`lineup-model-reviewer`** — reviews diffs touching
  `apps/api/app/lineup_model/**` or `services/lineup_evaluator.py`. Checks: no
  non-determinism introduced into scoring/position assignment; `output_hash`
  stability preserved; the deterministic-vs-LLM boundary intact (LLM only orders
  the nine selected players; scoring/selection stay deterministic); test
  coverage for new behavior. Read-only (tools: Read, Grep, Glob, Bash for tests).
- **`ingestion-helper`** — assists running/debugging ingestion: the `kbo-lab`
  ingest subcommands, `apps/api/app/jobs/daily_pipeline.py`, and the
  `ingestion-canary` workflow. Knows fixture-vs-Supabase via the existing skills.
- **`web-ui-reviewer`** — lean reviewer for `apps/web` (Next.js 16) changes;
  runs `npm run lint` / `format:check` / `build`. Used only when web files change.
- **`harness-auditor`** — the semantic drift auditor. Reads `CLAUDE.md`,
  `.claude/agents/*`, `.claude/commands/*`, `.claude/hooks/*`, `.claude/skills/*`
  and compares their claims against the actual codebase, reporting semantic
  divergences a structural linter cannot catch (e.g., a documented workflow that
  no longer reflects how the code works). Returns a structured verdict
  (`semantic_ok: bool`, `findings: [...]`).

### 5.3 Commands (`.claude/commands/*.md`)

Repo-specific only:
- **`/lab-check`** — run `uv run pytest -q` (from `apps/api`) and
  `pre-commit run --all-files` (from repo root); report results.
- **`/lab-ingest`** — run the `kbo-lab` ingest subcommands with arguments;
  reference `running-supabase-dev` for DB setup.
- **`/harness-audit`** — (1) run `python3 .claude/harness/check_drift.py`;
  (2) dispatch the `harness-auditor` agent; (3) call
  `python3 .claude/harness/record_audit.py <structural_ok> <semantic_ok>` to
  write the marker for the current HEAD; (4) report all findings.

### 5.4 Hook (`.claude/hooks/pretooluse_bash.py`)

A single PreToolUse hook with matcher `Bash`, registered in
`.claude/settings.json` as
`python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/pretooluse_bash.py"`. Reads the hook
JSON from stdin, inspects `tool_input.command`, and **denies** (exit code 2 with
a stderr message) on any of:

Guard rules:
- A `git commit` while the current branch is `main` (encourage a branch first).
- Any `git -C ` usage (repo rule: run git from the working directory).
- `git push` with `--force`/`-f` (force-push).
- Direct invocation of `black`/`ruff`/`mypy` (suggest `pre-commit run --all-files`).

PR audit gate:
- If the command matches `gh pr create` (allowing flags/whitespace), require a
  fresh, passing marker via `audit_state.is_fresh_and_passing()`; otherwise deny
  with: "Harness audit stale or missing for current HEAD — run /harness-audit
  before creating a PR."

Otherwise it allows the command (exit 0). The script imports `audit_state` from
the sibling `.claude/harness/` directory (via `sys.path`).

### 5.5 Structural linter (`.claude/harness/check_drift.py`)

A stdlib CLI that locates the repo root (walk up to the dir containing `.git`)
and runs these checks, printing each finding and exiting non-zero if any:

1. **Referenced paths exist** — extract repo-relative path tokens (only those
   beginning `apps/`, `docs/`, `.claude/`, `.github/`, `scripts/`) from
   `CLAUDE.md`, `.claude/agents/*.md`, `.claude/commands/*.md`,
   `.claude/skills/*/SKILL.md`; verify each exists.
2. **CLI commands exist** — every `kbo-lab <sub>` referenced in the docs is a
   registered `@app.command(...)` in `apps/api/app/cli.py`.
3. **Python version matches** — the Python version stated in `CLAUDE.md` equals
   `pyproject.toml [project].requires-python` (`apps/api/pyproject.toml`).
4. **Lint tools match** — the tools `CLAUDE.md` says pre-commit runs are a subset
   of the hook ids in `.pre-commit-config.yaml`.
5. **Env vars documented** — env vars referenced in `CLAUDE.md`/agents exist in
   `apps/api/.env.example`.
6. **Frontmatter valid** — every agent/command/skill `.md` has `name` +
   `description` frontmatter.
7. **Workflows referenced exist** — workflow names mentioned in `CLAUDE.md` exist
   under `.github/workflows/`.

Checks are conservative to avoid false positives (path extraction is restricted
to the known top-level prefixes above). Each check is a small function returning
a list of finding strings, so it is unit-testable.

### 5.6 Audit state (`audit_state.py`, `record_audit.py`)

`.claude/harness/.audit-state.json` (gitignored):
```json
{ "git_head": "<sha>", "structural_ok": true, "semantic_ok": true, "timestamp": "<iso8601>" }
```
- `audit_state.current_head()` → `git rev-parse HEAD`.
- `audit_state.read_state()` → dict or None.
- `audit_state.write_state(structural_ok, semantic_ok)` → writes the marker with
  the current HEAD and a timestamp.
- `audit_state.is_fresh_and_passing()` → True iff a marker exists, its `git_head`
  equals the current HEAD, and both flags are True.
- `record_audit.py <structural_ok> <semantic_ok>` → CLI wrapper around
  `write_state` (called by `/harness-audit`).

HEAD-binding gives "enforce right before PR" semantics: any commit added after
the audit changes HEAD, so the marker goes stale and re-audit is required.
Uncommitted working-tree changes do not change HEAD and are out of scope (a PR
is created from committed state).

### 5.7 CI workflow (`.github/workflows/harness.yaml`)

On push and pull_request: checkout, set up Python, run
`python3 .claude/harness/check_drift.py`. Build fails on structural drift. No
project install or `uv` needed (stdlib-only). This is the structural CI gate;
the semantic audit is NOT in CI.

## 6. Testing

`apps/api/tests/test_harness.py` (functions, not classes), using `subprocess` to
exercise the stdlib scripts so they get coverage without being importable as the
app package. Repo root is `Path(__file__).parents[3]`.

- **check_drift**: clean repo state passes (exit 0); a crafted drift (e.g., a
  temp CLAUDE.md referencing a missing `apps/nope.py`, or a bad `kbo-lab`
  subcommand) is detected (exit non-zero, finding mentions the offending item).
  Drive these against a temp directory fixture so they do not depend on the live
  repo contents.
- **pretooluse_bash**: feeding crafted stdin JSON →
  - `git commit ...` while on `main` → deny; on a feature branch → allow.
  - `git -C /x status` → deny; `git status` → allow.
  - `git push --force` → deny.
  - `ruff check` → deny with pre-commit suggestion.
  - `gh pr create ...` with no/stale marker → deny; with a fresh passing marker →
    allow. (Branch/marker conditions are simulated via env/args the script reads.)
- **audit_state**: `write_state` then `is_fresh_and_passing()` is True for the
  current HEAD; mutating the recorded head makes it False.

The drift scripts/hook are stdlib-only; they are validated via these subprocess
tests (mypy/ruff scope remains `apps/api`).

## 7. Error Handling & Safety

- The PreToolUse hook fails OPEN on internal error (if the script itself errors,
  it must not hard-block all Bash usage): on an unexpected exception it prints a
  warning to stderr and exits 0. Only explicit rule violations exit 2.
- `check_drift.py` distinguishes "drift found" (exit 1) from "checker error"
  (exit 2) so CI surfaces tooling bugs separately.
- The PR gate only matches `gh pr create`; other `gh` commands are unaffected.

## 8. Out of Scope / Future

- Distributable plugin packaging.
- Linting/type-checking the `.claude/` scripts (ruff/mypy scope stays `apps/api`).
- Additional agents (e.g., a dedicated migrations agent) until a need is proven.
- Putting semantic audit into CI.

## 9. Risks

- **Hook friction**: a PreToolUse(Bash) hook runs on every Bash call — mitigated
  by stdlib-only, single-process, fail-open design.
- **Drift-linter false positives**: mitigated by conservative path extraction and
  unit tests; the linter is easy to tune.
- **Marker staleness UX**: contributors may be surprised PR creation is blocked —
  mitigated by a clear deny message pointing to `/harness-audit`.

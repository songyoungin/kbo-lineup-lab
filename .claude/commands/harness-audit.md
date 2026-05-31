---
description: Audit the Claude Code harness for drift (structural + semantic) and record the audit marker.
---

Audit the harness and record the result so PR creation is unblocked:

1. Run `python3 .claude/harness/check_drift.py`. Treat exit 0 as STRUCTURAL=true, non-zero as STRUCTURAL=false (report the findings).
2. Launch the harness-auditor agent to assess semantic drift. Read its final `SEMANTIC_OK: true|false` line as SEMANTIC.
3. Record the marker for the current HEAD: `python3 .claude/harness/record_audit.py <STRUCTURAL> <SEMANTIC>` (pass `true`/`false`).
4. Report all structural and semantic findings.

Note: the `gh pr create` hook blocks PR creation unless both STRUCTURAL and SEMANTIC are true for the current HEAD. Any new commit invalidates the marker — re-run this command before opening a PR.

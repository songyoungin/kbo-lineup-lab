---
description: Run the API test suite and pre-commit across the repo, then report results.
---

Run both checks and summarize results (do not fix issues unless asked):

1. From `apps/api`: `uv run pytest -q`
2. From the repo root: `pre-commit run --all-files`

Report failures with file/line and the failing hook/test name.

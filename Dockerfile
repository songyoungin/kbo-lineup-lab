# FastAPI serving image. Build context must be the repo root: the uv
# workspace lock (uv.lock) and root pyproject.toml live there.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /srv

# Install third-party deps first so this layer caches across app-code edits.
# The repo root is a virtual workspace (package = false) whose only member is
# apps/api, so target that package explicitly; a bare `uv sync` would sync the
# empty root project and install nothing.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN uv sync --frozen --no-dev --package kbo-lineup-lab-api --no-install-workspace

# Then install the workspace package itself.
COPY apps/api apps/api
RUN uv sync --frozen --no-dev --package kbo-lineup-lab-api

ENV PATH="/srv/.venv/bin:$PATH"

WORKDIR /srv/apps/api

# Cloud Run injects PORT (defaults to 8080).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

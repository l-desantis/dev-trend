# syntax=docker/dockerfile:1.7

# ----- Builder stage: install deps with uv -----
FROM python:3.12-slim AS builder

# Pin uv version explicitly so reproducible builds don't drift.
COPY --from=ghcr.io/astral-sh/uv:0.4.27 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install deps first (cached) without the project source.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now copy source and finalize the venv with the project installed.
COPY app/ ./app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ----- Runtime stage: minimal image -----
FROM python:3.12-slim AS runtime

# curl is used by the compose healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/app /app/app
COPY --chown=app:app alembic/ /app/alembic/
COPY --chown=app:app alembic.ini /app/alembic.ini
COPY --chown=app:app scripts/ /app/scripts/

# Data directory for SQLite + read-only YAML config bind-mount.
RUN mkdir -p /data /app/data && chown -R app:app /data /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

CMD ["devtrend"]

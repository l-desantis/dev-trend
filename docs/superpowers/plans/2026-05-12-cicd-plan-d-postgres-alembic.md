# CI/CD Plan D — Migrate SQLite → Postgres + introduce Alembic

> **For agentic workers:** REQUIRED SUB-SKILL — use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to work through this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

## Context

After Plans A–C, DevTrend deploys SQLite-backed code through CI/CD with health-gated rollback. This plan replaces the runtime DB with **Postgres + Alembic**, riding the same pipeline as the first real change. Production data is **not** preserved: backfill is allowed to repopulate the new database from sources (`BACKFILL_ON_EMPTY=true`).

Key shifts from the SQLite world:
- `init_db()` (which called `Base.metadata.create_all`) is replaced with a one-shot `migrate` compose service running `alembic upgrade head`. The `app` container `depends_on` its successful completion.
- The deploy pipeline now matters more: with shared persistent state, a rollback to an older image must still work against the newer schema. That introduces a **forward-only migration discipline** documented in this plan and codified in a runbook.
- CI tests now run against a real Postgres service container.

See the design doc: `docs/superpowers/specs/2026-05-12-cicd-infrastructure-design.md` (especially "Forward-only migrations").

**Goal:** A `docker compose up -d` produces `postgres` + `migrate` + `app` in dependency order; the app boots green; the deploy pipeline from Plan C ships the change to the VPS unchanged (the operator does **one** manual step at cutover to drop the old SQLite volume).

**Architecture:** `asyncpg` replaces `aiosqlite`. Alembic owns schema lifecycle. A new `postgres:16-alpine` service holds data in a `pg_data` named volume; a new short-lived `migrate` service runs migrations; `app` depends on both. Tests use a Postgres service container in CI (`postgres:16-alpine`) and `testcontainers-python` locally.

**Tech Stack:** PostgreSQL 16, `asyncpg`, Alembic 1.13+, `testcontainers[postgres]`, SQLAlchemy 2.0 async.

**Environment note:** Per `CLAUDE.md`, the assistant cannot run `uv`/`python`/`alembic` directly. At every "run …" step, ask the operator to run the command and paste output. Wait before continuing.

---

## File Structure

| File | Status | Responsibility |
| --- | --- | --- |
| `pyproject.toml` | Modify | Remove `aiosqlite`; add `asyncpg`, `alembic`. Add `testcontainers[postgres]` to dev deps. |
| `.env.example` | Modify | Change `DATABASE_URL` default to Postgres URL. |
| `alembic.ini` | Create | Standard Alembic config; `script_location = alembic`. |
| `alembic/env.py` | Create (replace template) | Read `settings.database_url`, use async runner, point `target_metadata` at the project's `DeclarativeBase`. |
| `alembic/versions/<rev>_initial_schema.py` | Create | Initial migration generated from current models. |
| `app/main.py` | Modify | Drop `await init_db()`; replace with a `SELECT 1` reachability check. |
| `app/db.py` | Modify | Remove `init_db` (or mark deprecated) — depends on what `app/db.py` exposes today; confirm during Task 5. |
| `tests/conftest.py` | Modify | Replace any SQLite-based fixture with a Postgres fixture (testcontainers locally; service-container env vars in CI). |
| `docker-compose.yml` | Modify | Add `postgres` + `migrate` services; update `app` `depends_on`; drop `devtrend_db` volume. |
| `docker-compose.override.yml` | Modify | Expose Postgres on `5432` for local inspection. |
| `.github/workflows/ci.yml` | Modify | Add a `postgres:16-alpine` service to the `test` job; pass `DATABASE_URL` to pytest. |
| `docs/superpowers/runbooks/migration-safety.md` | Create | Forward-only migration rules + examples. |
| `docs/superpowers/runbooks/plan-d-cutover.md` | Create | Operator runbook for the one manual cutover step. |

---

## Task 1: Swap deps (asyncpg in, aiosqlite out)

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Edit `pyproject.toml` dependencies**

Remove the `aiosqlite>=0.20` line. Add `asyncpg>=0.29` and `alembic>=1.13`. The dependencies block becomes:

```toml
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "python-telegram-bot>=20.0",
    "apscheduler>=3.10",
    "sqlalchemy>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "httpx>=0.27",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",
    "ollama>=0.2",
    "google-play-scraper==1.2.7",
    "numpy>=1.26",
    "scikit-learn>=1.4",
    "scipy>=1.13",
    "structlog>=24.0",
    "rich>=13",
    "openai>=1.0",
]
```

Add `testcontainers[postgres]>=4.0` to `[project.optional-dependencies].dev`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
    "mypy>=1.10",
    "testcontainers[postgres]>=4.0",
]
```

- [ ] **Step 2: Operator runs `uv sync`**

```
! uv sync
```

Expected: `aiosqlite` is uninstalled; `asyncpg`, `alembic`, `testcontainers` (with the postgres extra and `psycopg`/`docker` transitives) are installed; `uv.lock` is updated.

- [ ] **Step 3: Commit (lock file included)**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: swap aiosqlite for asyncpg + alembic; add testcontainers[postgres] (dev)"
```

---

## Task 2: Change `DATABASE_URL` default

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Update `.env.example`**

Replace the line:

```
DATABASE_URL=sqlite+aiosqlite:///./devtrend.db
```

with:

```
# Production / docker-compose: postgres service is reachable as `postgres:5432`.
# Local development without docker: use a local Postgres, e.g. postgresql+asyncpg://devtrend:devtrend@localhost:5432/devtrend
DATABASE_URL=postgresql+asyncpg://devtrend:devtrend@postgres:5432/devtrend

# Postgres container credentials (used by docker-compose and CI).
POSTGRES_USER=devtrend
POSTGRES_PASSWORD=devtrend
POSTGRES_DB=devtrend
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "chore(env): switch DATABASE_URL default to postgres; add POSTGRES_* vars"
```

---

## Task 3: Bootstrap Alembic

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py` (replaces the template generated by `alembic init`)
- Create: `alembic/script.py.mako` (default from `alembic init`)
- Create: `alembic/README` (default from `alembic init`)

- [ ] **Step 1: Operator runs `alembic init alembic`**

```
! uv run alembic init alembic
```

Expected: creates `alembic.ini` at repo root and an `alembic/` directory containing `env.py`, `README`, `script.py.mako`, and an empty `versions/` folder.

- [ ] **Step 2: Trim `alembic.ini`**

Open `alembic.ini` and:
- Comment out `sqlalchemy.url = driver://user:pass@localhost/dbname` (env.py will set it at runtime).
- Confirm `script_location = alembic`.
- Confirm `prepend_sys_path = .` (so `from app.db import ...` works).

- [ ] **Step 3: Replace `alembic/env.py` with an async-aware version**

Open `alembic/env.py` and replace its contents with:

```python
"""Alembic env.py — async-aware, reads DATABASE_URL from app settings."""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make `app.*` importable; alembic.ini already has prepend_sys_path = .
from app.config import get_settings
from app.db import Base  # NOTE: confirm this import path in Task 4 Step 1.

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Generate SQL without a live connection (`alembic upgrade --sql ...`)."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Commit Alembic scaffolding (no migration revisions yet)**

```bash
git add alembic.ini alembic/
git commit -m "feat(alembic): scaffold async Alembic env"
```

---

## Task 4: Confirm the SQLAlchemy `DeclarativeBase` import path

`alembic/env.py` imports `from app.db import Base`. Reality check that this is the right symbol before generating migrations.

- [ ] **Step 1: Operator opens `app/db.py` and confirms**

```
! grep -nE "class\s+Base|DeclarativeBase" app/db.py
```

There should be a line like `class Base(DeclarativeBase): ...` or `Base = declarative_base()`. If `Base` isn't exposed from `app.db` directly, locate where the project's metadata lives (try `grep -rnE "DeclarativeBase|declarative_base" app/`).

- [ ] **Step 2: Verify all model modules are imported before Alembic introspects `Base.metadata`**

Open `app/db.py` and confirm that the models module(s) (`app/models/...` or wherever the `Mapped[...]` columns live) are imported as a side effect. If not, add the imports near the top:

```python
# Side-effect imports so Base.metadata sees every table.
from app.models import (  # noqa: F401
    # list all submodules here
)
```

Alembic's autogenerate will silently miss tables whose modules haven't been imported. **This is the single most common Alembic foot-gun** — get it right now.

- [ ] **Step 3: If env.py's `from app.db import Base` was wrong, fix it**

Update `alembic/env.py` to import from wherever `Base` actually lives.

- [ ] **Step 4: Commit any changes**

```bash
git add app/db.py alembic/env.py
git commit -m "fix(alembic): ensure all model modules are imported for metadata"
```

(Skip the commit if nothing needed changing.)

---

## Task 5: Drop `init_db()`, replace with a reachability check

**Files:**
- Modify: `app/main.py`
- Modify: `app/db.py`

- [ ] **Step 1: Read the existing `init_db()` body**

```
! grep -nA 20 "async def init_db" app/db.py
```

Confirm it's `create_all`-based (it should be — that's what we're replacing). Note the export so we can remove its call site.

- [ ] **Step 2: Replace `init_db()` with `check_db_reachable()`**

In `app/db.py`, replace:

```python
async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

with:

```python
async def check_db_reachable() -> None:
    """Fail fast if the database is unreachable; rely on Alembic for schema."""
    from sqlalchemy import text
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
```

- [ ] **Step 3: Update `app/main.py` lifespan**

Find the line `await init_db()` and change it to:

```python
from app.db import check_db_reachable
await check_db_reachable()
log.info("Database reachable", component="main")
```

(Drop the `init_db` import.)

- [ ] **Step 4: Commit**

```bash
git add app/db.py app/main.py
git commit -m "refactor(db): replace init_db with check_db_reachable (alembic owns schema now)"
```

---

## Task 6: Generate the initial Alembic revision

**Files:**
- Create: `alembic/versions/<auto>_initial_schema.py`

- [ ] **Step 1: Operator starts a Postgres for autogenerate to inspect**

We need a Postgres reachable at the URL `app.config` resolves to. Quickest way:

```
! docker run --rm -d --name dt-pg-bootstrap -e POSTGRES_USER=devtrend -e POSTGRES_PASSWORD=devtrend -e POSTGRES_DB=devtrend -p 5432:5432 postgres:16-alpine
```

Wait ~5 s, then set `DATABASE_URL` for the autogenerate run only:

```
! DATABASE_URL=postgresql+asyncpg://devtrend:devtrend@localhost:5432/devtrend uv run alembic revision --autogenerate -m "initial schema"
```

Expected: a new file appears under `alembic/versions/`, named like `<rev>_initial_schema.py`. It contains `upgrade()` with `op.create_table(...)` calls for every model.

- [ ] **Step 2: Operator opens the generated revision and hand-reviews it**

Common issues to look for:
- Missing tables → likely Task 4 Step 2 import problem.
- `server_default` mismatches → autogenerate sometimes flags spurious diffs; fix or accept.
- Index / foreign-key names with autogenerated suffixes → fine for the initial revision.
- JSON / array columns → confirm types are sensible (`JSONB`, `ARRAY(...)`, not `VARIANT`).

Edit the file directly to fix anything obviously wrong. Run `! uv run alembic upgrade head` against the bootstrap Postgres to confirm it applies cleanly:

```
! DATABASE_URL=postgresql+asyncpg://devtrend:devtrend@localhost:5432/devtrend uv run alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade  -> <rev>, initial schema`.

Verify by introspecting:

```
! docker exec -it dt-pg-bootstrap psql -U devtrend -d devtrend -c "\dt"
```

Expected: all your tables listed.

- [ ] **Step 3: Tear down the bootstrap Postgres**

```
! docker rm -f dt-pg-bootstrap
```

- [ ] **Step 4: Commit the revision**

```bash
git add alembic/versions/
git commit -m "feat(alembic): initial schema revision"
```

---

## Task 7: Add a Postgres-backed test fixture

**Files:**
- Modify: `tests/conftest.py` (or create if it doesn't exist)

- [ ] **Step 1: Read the existing `tests/conftest.py`**

```
! cat tests/conftest.py 2>/dev/null || echo "no conftest yet"
```

Identify any existing DB fixture (likely SQLite-based). We'll replace it.

- [ ] **Step 2: Write the new fixture**

The fixture has two modes:
- **Local dev:** `testcontainers.postgres.PostgresContainer` spins up a real Postgres per test session.
- **CI:** when `DATABASE_URL` is already set in the env (CI service container), use it directly and skip testcontainers.

Add (or modify) `tests/conftest.py`:

```python
"""Shared test fixtures."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture(scope="session")
def database_url() -> str:
    """Return a Postgres URL for the test session.

    In CI we read DATABASE_URL from the env (a service container). Locally we
    start a Postgres container via testcontainers so developers don't need to
    manage one themselves.
    """
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.start()

    # Close on session teardown.
    import atexit
    atexit.register(container.stop)

    raw = container.get_connection_url()  # postgresql+psycopg2://...
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations(database_url: str) -> None:
    """Run `alembic upgrade head` once per test session."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    os.environ["DATABASE_URL"] = database_url
    command.upgrade(cfg, "head")


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A fresh AsyncSession per test, rolled back at teardown."""
    engine = create_async_engine(database_url, future=True)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        try:
            yield s
        finally:
            await s.rollback()
    await engine.dispose()
```

(If the existing conftest has fixtures or imports that conflict, integrate them — don't blindly overwrite. Keep any non-DB fixtures intact.)

- [ ] **Step 3: Operator runs the test suite locally**

```
! uv run pytest -m "not integration" -q
```

Expected: all tests pass. The first run takes longer (testcontainers pulls `postgres:16-alpine`).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "test: postgres-backed session fixture (testcontainers locally, env URL in CI)"
```

---

## Task 8: Update CI to use a Postgres service container

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Update the `test` job in `.github/workflows/ci.yml`**

Replace the existing `test:` block with:

```yaml
  test:
    name: Tests (pytest, unit only)
    runs-on: ubuntu-24.04
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: devtrend
          POSTGRES_PASSWORD: devtrend
          POSTGRES_DB: devtrend
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U devtrend"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.4.27"
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Sync dev deps
        run: uv sync --frozen
      - name: pytest
        env:
          DATABASE_URL: postgresql+asyncpg://devtrend:devtrend@localhost:5432/devtrend
          LLM_PROVIDER: mock
          EMBEDDING_PROVIDER: mock
        run: uv run pytest -m "not integration" -q
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: switch test job to postgres service container"
```

- [ ] **Step 3: Push branch + open a PR to watch CI**

```bash
git checkout -b feat/postgres-alembic
git push -u origin feat/postgres-alembic
```

Open PR. Expected: `test` job spins up `postgres:16-alpine`, runs migrations via the conftest fixture, and pytest passes.

**Do not merge yet** — the compose file changes come next.

---

## Task 9: Update `docker-compose.yml` for Postgres + migrate

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.override.yml`

- [ ] **Step 1: Replace `docker-compose.yml` (production-shape)**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: dev-trend-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-devtrend}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-devtrend}
      POSTGRES_DB: ${POSTGRES_DB:-devtrend}
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-devtrend}"]
      interval: 5s
      timeout: 3s
      retries: 10
    deploy:
      resources:
        limits:
          memory: 512M

  migrate:
    image: ghcr.io/l-desantis/dev-trend:${IMAGE_TAG:-latest}
    container_name: dev-trend-migrate
    restart: "no"
    env_file:
      - .env
    command: ["alembic", "upgrade", "head"]
    depends_on:
      postgres:
        condition: service_healthy

  app:
    image: ghcr.io/l-desantis/dev-trend:${IMAGE_TAG:-latest}
    container_name: dev-trend-app
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      postgres:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
    volumes:
      - ./data:/app/data:ro
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 10s
      timeout: 3s
      retries: 6
      start_period: 30s
    deploy:
      resources:
        limits:
          memory: 2048M
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  pg_data:
    name: devtrend_pg_data
```

Key changes from Plan A's compose:
- New `postgres` service + `pg_data` volume; the SQLite `devtrend_db` volume is gone.
- New `migrate` service runs `alembic upgrade head` and exits.
- `app.depends_on` now waits for **both** healthy postgres **and** completed migration.
- `DATABASE_URL` is no longer overridden in `environment:` — the value comes from `.env` and points at the `postgres` service hostname.

- [ ] **Step 2: Update `docker-compose.override.yml` for local dev**

```yaml
services:
  postgres:
    ports:
      - "5432:5432"  # expose for local psql / IDE access

  app:
    build: .
    image: dev-trend:dev
    volumes:
      - ./app:/app/app:ro
      - ./data:/app/data:ro
    environment:
      LOG_LEVEL: DEBUG
    ports:
      - "0.0.0.0:8000:8000"

  migrate:
    build: .
    image: dev-trend:dev
```

The SQLite-era bind-mount under `data/dev/` is removed entirely.

- [ ] **Step 3: Operator runs the new stack locally**

```
! cp .env.example .env  # if .env was wiped between plans; otherwise update DATABASE_URL
! docker compose down -v  # drop any old volumes from Plan A
! docker compose up -d --build
! sleep 30 && docker compose ps
! curl -fsS http://127.0.0.1:8000/health
```

Expected: `postgres` healthy; `migrate` shows status `exited (0)`; `app` running and healthy; `/health` returns `200`.

- [ ] **Step 4: Verify migrations actually ran in Postgres**

```
! docker compose exec postgres psql -U devtrend -d devtrend -c "\dt"
! docker compose exec postgres psql -U devtrend -d devtrend -c "SELECT version_num FROM alembic_version"
```

Expected: tables listed; `alembic_version` shows the initial revision id.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml docker-compose.override.yml
git commit -m "build: add postgres + migrate services to compose"
```

---

## Task 10: Write the migration-safety runbook

**Files:**
- Create: `docs/superpowers/runbooks/migration-safety.md`

- [ ] **Step 1: Write the runbook**

````markdown
# Migration safety — forward-only rules

After Plan D ships, deploy rollback restores a **previous container image** against the **current Postgres data**. This means every migration must be backward-compatible with the immediately previous image.

## Hard rules

1. **New columns are nullable** (or have a server-side default). Never `NOT NULL` without a default in the same release that introduces the column.
2. **Renames take two releases**:
   - Release N: add new column, dual-write (app writes to both, reads prefer old).
   - Release N+1 (after stability is confirmed): drop the old column.
3. **No destructive drops** alongside code that depends on the absence of the dropped column/table. Drops happen in a release **after** the code that stops referencing them has been deployed and stable.
4. **Foreign keys**: add the column first, backfill, **then** add the FK constraint in a follow-up release. Don't do both in one migration.
5. **Index creation on large tables**: use `CREATE INDEX CONCURRENTLY`. Alembic supports this via `op.create_index(..., postgresql_concurrently=True)` combined with `op.execute("COMMIT")` to leave the transaction Alembic wraps around it.

## Soft rules

- Every migration should be tested by running `alembic upgrade head` then `alembic downgrade -1` against a non-empty database, even if `downgrade()` is the auto-generated stub. If downgrade doesn't work, write a comment explaining why.
- Mass-rewrites (`UPDATE` over millions of rows) must be chunked or moved out of the migration into a one-shot script.

## Rollback procedure when a migration goes wrong

Two scenarios:

### A. The migration applied but the app is unhappy

Use the manual rollback workflow (`.github/workflows/rollback.yml`) to revert to the previous image. The new schema stays in place — that's the whole point of forward-only migrations. Then ship a follow-up release that either fixes the app or rolls forward the schema.

**Never** run `alembic downgrade` against production unless you've prepared a careful, tested downgrade and have read-write coordination with anything else touching the DB.

### B. The migration itself failed (left the DB partially migrated)

This is much worse and is what `CREATE INDEX CONCURRENTLY` etc. exist to prevent. Recovery is bespoke:
1. Stop the app (`docker compose stop app`).
2. Inspect `alembic_version` and the DB state.
3. Apply manual SQL to bring the schema to a known-good revision.
4. Update `alembic_version` to match.
5. Restart.

If you reach this state, write a postmortem and add a rule above so it doesn't happen again.
````

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/runbooks/migration-safety.md
git commit -m "doc: add migration-safety runbook (forward-only rules)"
```

---

## Task 11: Write the cutover runbook

The only manual step in this whole CI/CD project: the operator stops the old SQLite stack and drops the old volume **before** the Plan D deploy fires. Document it so it's repeatable.

**Files:**
- Create: `docs/superpowers/runbooks/plan-d-cutover.md`

- [ ] **Step 1: Write the runbook**

````markdown
# Plan D cutover — drop SQLite, welcome Postgres

Run this **once**, on the VPS, **after** the Plan D PR is merged but **before** the resulting deploy runs.

You have ~5 minutes between merge and deploy (CI + Build and Push runtime). If you don't make it in time, the deploy will fail because the new compose file expects a `postgres` service and a fresh `.env` pointing at it.

If you miss the window, follow the recovery section below.

## Pre-flight

1. Confirm the merge commit is on GitHub and CI is running.
2. SSH into the VPS as `deploy`.

## Cutover steps

```bash
cd /opt/dev-trend

# 1. Stop the running SQLite stack.
docker compose down

# 2. Drop the SQLite volume. Production starts fresh — the daily backfill
#    repopulates everything within 24h.
docker volume rm devtrend_db

# 3. Pull the new compose file (will be there once the merge commit lands).
#    If the deploy already ran and failed, `git status` will show divergence
#    from origin/main — `git reset --hard origin/main` to align.
git pull --ff-only

# 4. Update `.env` to include the new Postgres credentials.
#    Open secrets.enc.env via sops (locally), add:
#      POSTGRES_USER=devtrend
#      POSTGRES_PASSWORD=<generate a strong one>
#      POSTGRES_DB=devtrend
#      DATABASE_URL=postgresql+asyncpg://devtrend:<same password>@postgres:5432/devtrend
#    Commit and push. The deploy workflow re-pulls the encrypted file.
#
#    Alternative if you need to be on-VPS only:
#      SOPS_AGE_KEY_FILE=/etc/devtrend/age.key sops secrets.enc.env
#    edits the encrypted file in-place; `git commit` and push from there.
```

After step 4's commit lands on `main`, the deploy workflow re-runs (or you can manually trigger "Deploy" via `workflow_dispatch`). The first deploy of Plan D will:
1. SSH in, `git pull`.
2. Decrypt `secrets.enc.env`.
3. `docker compose pull` (pulls postgres:16-alpine + the new app image).
4. `docker compose up -d` brings up `postgres` → `migrate` → `app` in order.
5. `/health` returns green.

## Recovery if the deploy fired before you cut over

The deploy will have failed (`migrate` can't connect because `POSTGRES_*` env vars aren't set yet, or because the SQLite volume is still mounted). The auto-rollback kicks in and pins the previous image. You'll get a Telegram alert.

Recover by:
1. SSH in. Confirm the old SQLite-era container is running (rollback succeeded).
2. Do the cutover steps above (down → drop volume → update secrets).
3. Manually trigger "Deploy" via the GitHub UI (`workflow_dispatch`).

## After the deploy is green

Verify:

```bash
docker compose ps                           # postgres healthy, migrate exited 0, app healthy
docker compose exec postgres psql -U devtrend -d devtrend -c "SELECT version_num FROM alembic_version"
curl -fsS http://127.0.0.1:8000/health
```

The 24-hour backfill (`BACKFILL_ON_EMPTY=true`) populates `SourceItem` overnight; the daily pipeline at 03:30 UTC begins producing candidates the next morning.
````

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/runbooks/plan-d-cutover.md
git commit -m "doc: add Plan D cutover runbook"
```

---

## Task 12: Open the PR, watch CI, perform the cutover, watch the deploy

This is the final step — production switches from SQLite to Postgres.

- [ ] **Step 1: Push the branch and open a PR**

```bash
git push -u origin feat/postgres-alembic
```

Open the PR. CI runs against the Postgres service container.

- [ ] **Step 2: Get CI green**

If anything fails, fix and push. Don't merge until everything is green.

- [ ] **Step 3: Operator does a final dry-run locally**

```
! docker compose down -v
! docker compose up -d --build
! sleep 30
! docker compose ps
! curl -fsS http://127.0.0.1:8000/health
! docker compose down
```

All steps must succeed.

- [ ] **Step 4: Decide the cutover window**

Pick a moment when you can be at the keyboard for ~10 minutes (no scheduled pipeline runs imminent — the pipeline cron is at 03:30 UTC, so any other time is fine).

- [ ] **Step 5: Pre-stage the secret update**

Open `secrets.enc.env` locally with `sops`, add `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, and update `DATABASE_URL` to the Postgres URL. **Don't commit yet.** Save and close — SOPS re-encrypts.

```
! sops secrets.enc.env
```

- [ ] **Step 6: Merge the PR**

This kicks off CI (already green from the PR) + Build and Push. Total ~5 minutes.

- [ ] **Step 7: Within that 5-minute window, run the cutover**

Follow `docs/superpowers/runbooks/plan-d-cutover.md`:
1. SSH to VPS.
2. `docker compose down` and `docker volume rm devtrend_db`.
3. Push the staged `secrets.enc.env` commit (on the local laptop, `git push`).

The deploy workflow will pick up the new `secrets.enc.env` along with the merge commit when it runs.

- [ ] **Step 8: Watch the deploy**

GitHub → Actions → "Deploy". Watch the SSH session output. Health gate should pass. Telegram should fire the success notification.

- [ ] **Step 9: Post-deploy verification on the VPS**

```
! ssh deploy@<VPS_IP>
$ cd /opt/dev-trend
$ docker compose ps
$ docker compose exec postgres psql -U devtrend -d devtrend -c "\dt"
$ docker compose exec postgres psql -U devtrend -d devtrend -c "SELECT version_num FROM alembic_version"
$ curl -fsS http://127.0.0.1:8000/health
```

All four commands should report sensible output. The DB is empty (no `SourceItem` rows yet) — `BACKFILL_ON_EMPTY=true` will populate it on first scheduled run.

- [ ] **Step 10: Next-day check**

The next morning, verify:
- Reddit / HN / GitHub backfill ran overnight.
- The pipeline cron at 03:30 UTC ran.
- Telegram digest at 08:00 local arrived as expected.

---

## Verification (run by operator — see CLAUDE.md)

End-to-end after Plan D ships:

1. **Local stack still works:**
   ```
   ! docker compose down -v
   ! docker compose up -d --build
   ! sleep 30
   ! curl -fsS http://127.0.0.1:8000/health
   ```
   Expected: green.

2. **Migrations are deterministic:**
   ```
   ! docker compose run --rm migrate alembic current
   ! docker compose run --rm migrate alembic upgrade head  # idempotent
   ```
   Expected: no errors; revision id matches the initial migration.

3. **Tests run against postgres service in CI:**
   - Verify the latest CI run on `main` shows the `postgres` service container in the `test` job.

4. **Rollback-with-shared-schema works:**
   - Push a trivial code-only change to `main` (no migration).
   - Confirm a clean deploy lands.
   - Trigger "Manual Rollback" to the previous sha (the Plan D initial deploy).
   - Confirm the rollback succeeds — the old image runs cleanly against the new schema (because we added no destructive changes between).

5. **First real migration (post-Plan-D):**
   - Whenever you next change a model, generate a migration:
     ```
     ! uv run alembic revision --autogenerate -m "<description>"
     ```
   - Hand-review against the migration-safety runbook before committing.

---

## Out of scope (explicit)

- **Preserving data from the SQLite `devtrend.db`.** Production starts fresh; the daily backfill (`BACKFILL_ON_EMPTY=true`, 30-day window) repopulates source items overnight. Lifecycle history and clusters from the dev DB are not migrated.
- **Postgres backups.** Deferred to a follow-up plan. Recommended path documented in the design doc (nightly `pg_dump` → Hetzner Storage Box via restic).
- **Postgres tuning beyond memory limits.** Defaults are fine for this workload at this scale.
- **Multi-tenant DB layout, read replicas, partitioning.** All out of scope.
- **Migration of historical embeddings.** None to migrate from SQLite (the dev DB is wiped); future provider switches re-embed from scratch (ADR-011 already handles this via the provider-prefixed `model_name` cache key).

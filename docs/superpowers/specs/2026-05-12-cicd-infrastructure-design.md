# Design — CI/CD infrastructure for DevTrend v4

**Date:** 2026-05-12
**Owner:** Lorenzo De Santis
**Status:** Approved (pending user review of this document)
**GitHub repo:** `l-desantis/dev-trend` — image path `ghcr.io/l-desantis/dev-trend`

## Context

DevTrend v4 is a daily, fully automated opportunity-discovery pipeline for indie developers. It ingests from Reddit, Hacker News, GitHub Issues, and Google Play; extracts pain points via an LLM (NIM or OpenAI); clusters them with HDBSCAN; scores and tracks candidates through a lifecycle; surfaces results via a Telegram bot. The codebase is Python 3.12, fully async, FastAPI + APScheduler in a single process, managed with `uv` + `pyproject.toml`.

Today the project has **no CI/CD**. We need to ship the full pipeline: containers, GitHub Actions CI, automated SSH deploy to a Hetzner CX22 VPS, encrypted secrets, and health-gated rollback.

Two pre-existing assumptions in the user's brief turned out to be inaccurate after inspecting the repo and were corrected during brainstorming:

- The DB is **SQLite (`sqlite+aiosqlite`)**, not Postgres. There is no Alembic. The user has chosen to **ship CI/CD against SQLite first** and migrate to Postgres at the end of this work (see Plan order below).
- The app **does have an HTTP server** — FastAPI is mounted with a `/health` endpoint at `app/api/routes_health.py`. We will use this endpoint as the deploy health gate.

The work is decomposed into **four self-contained, sequenced plans** (A → B → C → D). Each can be merged independently and leaves the repo in a working state. CI/CD lands first (Plans A–C) against the current SQLite codebase; the Postgres migration is Plan D and is the only plan that materially mutates production state.

## Goals

1. Every push to `main` is automatically built, tested, and (if green) deployed to production on a Hetzner CX22 VPS.
2. A bad deploy is detected within ~60 s and automatically rolled back to the previous good image.
3. Secrets are versioned, encrypted, and rotatable without manual SSH.
4. A single operator can run, observe, and recover the system without a laptop in front of them.

## Non-goals

- Multi-region or HA. Single-VPS by design.
- Staging environment. Health checks + auto-rollback + Telegram alerts are the safety net.
- Zero-downtime deploys. Brief downtime (a few seconds) during `compose up -d` is acceptable.
- Automated Postgres backups. Deferred — see "Open questions" below.

## Decisions captured from brainstorming

| Area | Decision |
| --- | --- |
| Database | SQLite for Plans A–C; migrate to Postgres + Alembic as **final** Plan D |
| Postgres host (Plan D) | Sibling container in `docker-compose` on the VPS |
| LLM runtime | Always remote (NIM / OpenAI) — no Ollama in production compose |
| Environments | Production only; deploy on push to `main` |
| Data migration | Fresh start in prod — re-backfill from sources |
| Deploy method | SSH from GitHub Actions to VPS, `docker compose pull && up -d` |
| CI scope | Lint (ruff), format check, mypy, `pytest -m 'not integration'` |
| Image registry | `ghcr.io/l-desantis/dev-trend` |
| Image tags | `sha-<short>` per build + `latest`; keep last 10 sha tags |
| Secrets | SOPS + age, `secrets.enc.env` committed to the repo |
| Rollback | Health-gated; on `/health` failure, redeploy previous sha |
| Migrations on deploy (Plan D) | One-shot `migrate` service in compose that runs `alembic upgrade head` and exits; `app` `depends_on` its success |
| Notifications | Telegram message via existing bot on deploy success/failure |
| Backups | **Deferred** (open question) |

## Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub                                                     │
│  ┌─────────┐   push to main    ┌──────────────────────┐     │
│  │ Repo    │ ─────────────────▶│ Actions: ci.yml      │     │
│  └─────────┘                   │  - lint              │     │
│       │                        │  - typecheck         │     │
│       │                        │  - test (postgres    │     │
│       │                        │    service container)│     │
│       │                        └──────────┬───────────┘     │
│       │                                   │ green            │
│       │                                   ▼                  │
│       │                        ┌──────────────────────┐     │
│       │                        │ build-and-push.yml   │     │
│       │                        │  → ghcr.io:sha-<x>   │     │
│       │                        │  → ghcr.io:latest    │     │
│       │                        └──────────┬───────────┘     │
│       │                                   ▼                  │
│       │                        ┌──────────────────────┐     │
│       │                        │ deploy.yml           │     │
│       │                        │  SSH → VPS           │     │
│       │                        └──────────┬───────────┘     │
└───────┼───────────────────────────────────┼─────────────────┘
        │                                   │
        │ git pull                          │ SSH (deploy key)
        ▼                                   ▼
┌───────────────────────────────────────────────────────────────┐
│  Hetzner CX22 VPS (Ubuntu 24.04)                              │
│                                                               │
│  /opt/devtrend/                                               │
│   ├── docker-compose.yml         (from git)                   │
│   ├── secrets.enc.env            (from git, SOPS-encrypted)   │
│   ├── .env                       (decrypted at deploy time)   │
│   └── .deploy/                                                │
│       ├── current_tag                                         │
│       └── previous_tag                                        │
│                                                               │
│  /etc/devtrend/age.key           (private key, 0400 root)     │
│                                                               │
│  docker compose stack (Plans A–C — SQLite):                   │
│                                  ┌────────────────────┐       │
│                                  │       app          │       │
│                                  │ FastAPI :8000      │       │
│                                  │ APScheduler        │       │
│                                  │ Telegram bot       │       │
│                                  │ /health            │       │
│                                  │ volume:            │       │
│                                  │  devtrend_db       │       │
│                                  │   /data/devtrend.db│       │
│                                  └────────────────────┘       │
│                                                               │
│  After Plan D adds:                                           │
│   ┌──────────┐  ┌─────────────┐                               │
│   │ postgres │◀─│   migrate   │  (app gains depends_on these) │
│   │   :16    │  │ alembic     │                               │
│   │ pg_data  │  │ upgrade head│                               │
│   └──────────┘  └─────────────┘                               │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

## Plan decomposition

### Plan A — Containerize with Docker + docker-compose (SQLite)

**Why first:** the user's priority is shipping CI/CD. The fastest path to that is to package the existing SQLite app in a container that CI can build and CD can deploy. We come back for Postgres in Plan D.

**In scope:**
- **Multi-stage `Dockerfile`:**
  - Stage `builder`: `python:3.12-slim`, install `uv` from the official image (`COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv`), `uv sync --frozen --no-dev` into `/app/.venv`.
  - Stage `runtime`: `python:3.12-slim`, copy `/app/.venv` and `/app/app/` from builder, create a non-root `app` user, `WORKDIR /app`, `ENV PATH="/app/.venv/bin:$PATH"`. Default `CMD ["python", "-m", "app.main"]`.
  - The `app` user owns `/data` so SQLite can write to the mounted volume.
  - `HEALTHCHECK` lives in the compose file rather than the Dockerfile (single source of truth).
- **`.dockerignore`** excluding `.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `devtrend.db`, `.env`, `.git`, `tests/`, `docs/`, `data/` (data is bind-mounted in dev).
- **`docker-compose.yml`** at repo root (production-shape, SQLite):
  - `app` service:
    - image `ghcr.io/l-desantis/dev-trend:${IMAGE_TAG:-latest}`
    - env from `.env`; **override `DATABASE_URL=sqlite+aiosqlite:////data/devtrend.db`** so the DB lives in the mounted volume regardless of the value in `.env`
    - named volume `devtrend_db:/data` for the SQLite file (Plan D will replace this with `pg_data` on the postgres service)
    - bind-mount `./data:/app/data:ro` for `categories.yaml` (read at startup by `sync_categories_from_yaml`)
    - healthcheck: `CMD curl -fsS http://localhost:8000/health || exit 1`, `interval: 10s`, `retries: 6`, `start_period: 30s`
    - port `127.0.0.1:8000:8000` (loopback only — outbound Telegram doesn't need inbound)
    - memory limit 2 GB
    - `restart: unless-stopped`
  - Top-level `volumes: { devtrend_db: {} }`.
- **`docker-compose.override.yml`** (local dev): bind-mount `./app` into the container, expose `8000` on all interfaces, swap the volume for a bind-mount to `./data/dev/` so the DB file is inspectable from the host.
- **Schema creation:** the existing `await init_db()` in the lifespan stays for Plans A–C. It's idempotent (uses `Base.metadata.create_all`). Plan D replaces it with Alembic.
- **README:** add a "Run with docker compose" section.

**Out of scope:**
- Nginx / TLS termination. No inbound traffic.
- Multi-host orchestration.
- Postgres (Plan D).

**Acceptance:**
- `docker compose up -d --build` from a clean clone produces a running container; `curl http://127.0.0.1:8000/health` returns `200`.
- The image is < 400 MB compressed.
- The image runs as a non-root user (`docker exec ... id` reports uid != 0).
- Restarting the container preserves the SQLite DB (volume survives).

### Plan B — CI pipeline (lint, type-check, test, build, push)

**In scope:**

- `.github/workflows/ci.yml` — triggered on `push` to `main` and on `pull_request`:
  - Job `lint`: `astral-sh/setup-uv@v3` with cache, `uv sync --frozen`, `uv run ruff check .`, `uv run ruff format --check .`.
  - Job `typecheck`: same setup, `uv run mypy app/`.
  - Job `test`: same setup, `uv run pytest -m 'not integration' -q`. No service container needed — tests use SQLite (current state).
  - All three jobs run in parallel.

- `.github/workflows/build-and-push.yml` — triggered on `push` to `main` via `workflow_run` after `ci.yml` completes successfully:
  - Login to ghcr.io with the workflow token (`packages: write` permission).
  - `docker/setup-buildx-action`, `docker/build-push-action` with GHA cache (`cache-from: type=gha`, `cache-to: type=gha,mode=max`).
  - Tag with both `ghcr.io/l-desantis/dev-trend:sha-${GITHUB_SHA::7}` and `ghcr.io/l-desantis/dev-trend:latest`.
  - Output the short sha as a workflow output so the deploy workflow can consume it.

- `.github/workflows/prune-ghcr.yml` — weekly cron:
  - Use `actions/delete-package-versions` (or the GitHub API directly) to keep the most recent 10 `sha-*` tags and `latest`. Untagged dangling versions are deleted.

- `concurrency:` keys on `build-and-push` and (later) `deploy` keyed on `${{ github.ref }}` to prevent overlapping runs.

**Out of scope:**
- Coverage reporting / Codecov.
- Multi-arch builds. CX22 is amd64; building amd64 only.
- Security scanning (Trivy, etc.). Deferred.

**Acceptance:**
- A push to `main` produces a green CI run and an image at `ghcr.io/l-desantis/dev-trend:sha-<short>`.
- Total CI runtime (lint + typecheck + test + build/push) is under 8 minutes.

### Plan C — CD pipeline (SSH deploy, SOPS secrets, health-gated rollback, Telegram)

**In scope:**

- **VPS bootstrap runbook** at `docs/superpowers/runbooks/vps-bootstrap.md`:
  - Install Docker + Docker Compose plugin, `sops`, `age`, `curl`.
  - Create `deploy` user with `docker` group membership.
  - Generate VPS-side age key, store private key at `/etc/devtrend/age.key` (mode `0400`, owner `root` — readable by `deploy` via group or sudo wrapper; runbook picks the simplest workable permission scheme).
  - Add the age **public key** to the repo's `.sops.yaml` as the recipient.
  - Generate an ed25519 SSH keypair for GHA → VPS. Place the public key in `/home/deploy/.ssh/authorized_keys`. Add the private key to the repo as the `DEPLOY_SSH_KEY` GitHub Secret.
  - Clone the repo to `/opt/dev-trend`.
  - First-time bootstrap (one-shot, manual): `docker login ghcr.io` (with a PAT or `GITHUB_TOKEN` from a personal scope), `sops -d secrets.enc.env > .env`, `docker compose pull`, `docker compose up -d`.

- **`secrets.enc.env`** at repo root, encrypted with SOPS. `.sops.yaml` config pins the age recipient. README documents:
  - Rotation flow: `sops secrets.enc.env` opens `$EDITOR` with the decrypted content; saving re-encrypts on close.
  - Adding a contributor: append their age public key to `.sops.yaml` and re-encrypt.

- **`.github/workflows/deploy.yml`** — triggered by `workflow_run` on successful completion of `build-and-push`:
  1. Check out the repo (the workflow needs the short sha that build-and-push exported; it does **not** ship code — the VPS pulls via `git pull`).
  2. Set up SSH agent with `DEPLOY_SSH_KEY`.
  3. SSH to VPS with `NEW_SHA` as an env var, run on the box:
     ```bash
     cd /opt/dev-trend
     mkdir -p .deploy
     [ -f .deploy/current_tag ] && cp .deploy/current_tag .deploy/previous_tag
     echo "sha-${NEW_SHA}" > .deploy/current_tag
     git pull --ff-only
     SOPS_AGE_KEY_FILE=/etc/devtrend/age.key sops -d secrets.enc.env > .env
     chmod 600 .env
     export IMAGE_TAG="sha-${NEW_SHA}"
     docker compose pull
     docker compose up -d --remove-orphans
     ```
  4. **Health gate** (still inside the SSH session):
     ```bash
     for i in $(seq 1 30); do
       if curl -fsS http://127.0.0.1:8000/health > /dev/null; then exit 0; fi
       sleep 2
     done
     exit 1
     ```
  5. **On health-gate failure** the deploy workflow opens a second SSH session that rolls back:
     ```bash
     cd /opt/dev-trend
     export IMAGE_TAG=$(cat .deploy/previous_tag)
     docker compose up -d
     ```
     and re-runs the health gate before notifying Telegram.
  6. **Telegram notification** (always runs, even on failure): `POST https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage` with `chat_id=${TELEGRAM_CHAT_ID}`. Body: git sha, commit subject, status (deployed / rolled back / failed completely), workflow run URL.

- **Manual rollback workflow** `.github/workflows/rollback.yml`:
  - `workflow_dispatch` with input `target_sha`.
  - SSH to VPS, set `IMAGE_TAG=sha-${target_sha}`, `docker compose up -d`, health-check, Telegram-notify.

- **`concurrency:`** on `deploy.yml`: `group: deploy-prod`, `cancel-in-progress: false` (never cancel a deploy mid-flight).

**Out of scope:**
- Blue/green or canary. Out of scope for single-VPS.
- DB backups. Deferred until after Plan D (when there's a real DB worth backing up). Recommended path documented under "Open questions."

**Acceptance:**
- A push to `main` results in a deployed `sha-<short>` image on the VPS within ~5 minutes of CI green.
- A commit that intentionally breaks `/health` triggers an automatic rollback to the previous sha within ~90 s and a Telegram alert.
- `workflow_dispatch` rollback to any `sha-*` tag still on ghcr.io works end-to-end.

### Plan D — Migrate SQLite → Postgres + introduce Alembic

**Why last:** by this point CI and CD are in place. Migrating the DB is now a single, isolated change that goes through the same pipeline — we get the safety net before we use it.

**In scope:**
- Add `asyncpg` and `alembic` to deps; remove `aiosqlite`.
- Change `DATABASE_URL` default in `.env.example` to `postgresql+asyncpg://devtrend:devtrend@postgres:5432/devtrend` (compose-internal hostname).
- Bootstrap Alembic:
  - `alembic init alembic` at repo root.
  - Wire `alembic/env.py` to read `settings.database_url` and pull `target_metadata` from the project's SQLAlchemy `DeclarativeBase` (Plan D confirms the exact import by reading `app/db.py`). Async or sync runner — Plan D picks whichever is simpler against `asyncpg`.
  - Generate the initial revision (`alembic revision --autogenerate -m "initial schema"`) and hand-review before committing.
- Remove `await init_db()` from `app/main.py` lifespan. Replace with a `SELECT 1` reachability check that fails fast and loud if Postgres is down.
- **`docker-compose.yml` updates:**
  - Add `postgres` service: `postgres:16-alpine`, named volume `pg_data:/var/lib/postgresql/data`, healthcheck on `pg_isready`, env from `.env`, no host port exposure, memory limit 512 MB.
  - Add `migrate` service: same image as `app`, `command: alembic upgrade head`, `depends_on: postgres: { condition: service_healthy }`, `restart: "no"`. Exits 0 on success.
  - Update `app` service: drop the `devtrend_db` volume, add `depends_on: { migrate: { condition: service_completed_successfully }, postgres: { condition: service_healthy } }`.
  - Remove the `DATABASE_URL` override (the value from `.env` now points at `postgres:5432`).
- **CI update:** `ci.yml` test job gains a `postgres:16-alpine` service container; `DATABASE_URL` env wired to it.
- **Tests:** introduce a session-scoped fixture in `tests/conftest.py` that runs `alembic upgrade head` against the CI Postgres service (or testcontainers locally) and provides a fresh transaction per test.
- **Production rollout:** the deploy workflow runs unchanged (the new compose file does the work). Because we chose "fresh start in prod," the operator manually stops the existing stack and removes the `devtrend_db` volume in the same maintenance window the Plan D deploy lands. Plan D's runbook spells this out — it's the only manual step in the whole CI/CD work.
- **Migration safety rules** documented in `docs/superpowers/runbooks/migration-safety.md` (created here, not earlier — see cross-cutting note).

**Out of scope:**
- Preserving the contents of the existing `devtrend.db` file. Production starts empty; backfill runs on first boot (`BACKFILL_ON_EMPTY=true`).
- Backups (still deferred).

**Acceptance:**
- A push to `main` containing the Plan D changes results in a green CI run, a built image, and a deploy that brings up `postgres` + `migrate` + `app` on the VPS.
- After deploy, `curl http://127.0.0.1:8000/health` returns `200` and the app starts performing its scheduled jobs against Postgres.
- Pulling an older `sha-*` image deploys cleanly **against the new Postgres** (validating the forward-only migration discipline starting now).

## Cross-cutting design notes

### Forward-only migrations (applies from Plan D onward)
Plans A–C use SQLite with `Base.metadata.create_all` — schema changes there are not deploy-coupled because the volume holds the DB file and `create_all` is additive at startup. Once Plan D lands, rollback flips back to a previous image while the **Postgres data volume stays put**, so any migration must be **backward-compatible with the immediately previous image**:
- New columns: nullable, or with server-side defaults.
- Renames: introduce-new + dual-write + drop-old across **two** releases, never one.
- No destructive `DROP COLUMN`/`DROP TABLE` in the same release that depends on the absence of the column/table.

These rules are documented in `docs/superpowers/runbooks/migration-safety.md` (created in Plan D).

### Resource sizing on CX22 (2 vCPU / 4 GB RAM)
- Postgres: 512 MB limit, `shared_buffers=256MB`, `effective_cache_size=1GB`.
- App: 2 GB limit. Embedding batches (`EMBEDDING_BATCH_SIZE=64`) and HDBSCAN clustering can briefly spike; 2 GB gives headroom.
- Buildtime: GHA builds in the cloud, not on the VPS, so VPS RAM is unaffected by builds.

### Health endpoint
Current `GET /health` returns `{"status": "ok", "version": ...}` without touching the DB. This is **sufficient for now** — if the app starts, FastAPI binds the port, the scheduler is alive. If we later see false positives (app up but DB unreachable), we'll add `GET /ready` that does a `SELECT 1`. Out of scope for this work.

### Secrets in the deploy job
The GHA `deploy` job never sees decrypted secrets. The SOPS decryption happens **on the VPS**, using a key that never leaves the VPS. GHA only needs:
- `DEPLOY_SSH_KEY` (the SSH private key for the `deploy` user).
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (only for the notification step — these are duplicated from the encrypted env, which is fine; they're not high-impact secrets).

## Open questions / deferred

1. **Postgres backups.** Deferred. Not blocking the four-plan rollout, but should be the first follow-up after Plan D ships. Recommended path: nightly `pg_dump` running as a sidecar cron container, output piped through `restic` to a Hetzner Storage Box (BX11 ~€4/mo), retention daily-14 + weekly-8. Alternative: `pg_dump` to Backblaze B2 or Cloudflare R2 if you prefer S3-compatible storage with pay-per-use pricing.
2. **Monitoring / metrics.** No Prometheus, no Sentry today. We rely on structured JSON logs + Telegram alerts. Worth revisiting once the system has been running for a few weeks.
3. **Log shipping.** Same — `docker compose logs` is enough today.

## Order and dependencies

```
Plan A (Docker + compose, SQLite)
   │
   ▼
Plan B (CI: lint, typecheck, test, build, push to ghcr.io)
   │
   ▼
Plan C (CD: SSH deploy, SOPS secrets, health-gated rollback, Telegram)
   │
   ▼
Plan D (Postgres + Alembic — uses the CI/CD pipeline built in A–C)
```

Each plan is a single PR. Plans A–C make the project deployable; Plan D is the first real change to ride the pipeline.

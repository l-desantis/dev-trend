# CI/CD Plan A — Containerize with Docker + docker-compose (SQLite)

> **For agentic workers:** REQUIRED SUB-SKILL — use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to work through this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

## Context

DevTrend currently runs as a Python process under `uv` on a developer laptop. There is no container, no compose file, no production deploy. This plan is the first of four (A → B → C → D) that together build out CI/CD. **It packages the existing SQLite-based app in a Docker container that the rest of the pipeline (Plans B, C) can build, push, and deploy.** Postgres migration is deferred to Plan D — Plan A keeps SQLite + `init_db()` exactly as they are today.

See the design doc: `docs/superpowers/specs/2026-05-12-cicd-infrastructure-design.md`.

**Goal:** A working `docker compose up -d` from a clean clone produces a healthy container reachable at `http://127.0.0.1:8000/health`, with the SQLite DB persisted in a named volume.

**Architecture:** Multi-stage `Dockerfile` using `python:3.12-slim` and the official `uv` image. Production `docker-compose.yml` runs a single `app` service with a `devtrend_db` named volume for the SQLite file. A `docker-compose.override.yml` adapts the stack for local development (bind-mount source, expose port on all interfaces).

**Tech Stack:** Docker, Docker Compose, `uv`, Python 3.12.

**Environment note:** Per `CLAUDE.md`, the assistant cannot run `uv` / `python` / `docker` directly. At every "run …" step, ask the operator to run the command and paste the output. Wait for output before continuing.

---

## File Structure

| File | Status | Responsibility |
| --- | --- | --- |
| `Dockerfile` | Create | Multi-stage build; final image runs as non-root `app` user, `CMD ["python", "-m", "app.main"]`. |
| `.dockerignore` | Create | Exclude virtualenvs, caches, the local SQLite file, git/docs, and tests from the build context. |
| `docker-compose.yml` | Create | Production-shape: single `app` service, named volume for SQLite, healthcheck on `/health`, loopback-only port. |
| `docker-compose.override.yml` | Create | Local-dev shape: bind-mount `./app`, expose `8000` on all interfaces, swap volume for local bind-mount. |
| `README.md` | Modify | Add "Run with docker compose" section. |
| `app/main.py` | Modify (one line) | Bind FastAPI to `0.0.0.0` (currently the entrypoint is `app.main:main` which already runs uvicorn; confirm bind address during Task 2). |

---

## Task 1: Add `.dockerignore`

**Files:**
- Create: `.dockerignore`

- [ ] **Step 1: Create `.dockerignore`**

```
# Python
.venv/
__pycache__/
*.pyc
*.pyo

# Tool caches
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Local DB and env
devtrend.db
devtrend.db-*
.env
.env.local

# VCS / IDE
.git/
.gitignore
.idea/
.agents/
.claude/

# Tests and docs (not needed in the runtime image)
tests/
docs/
KANBAN.md
devtrend-project-document.md

# Data dir is bind-mounted at runtime, not baked in
data/
```

- [ ] **Step 2: Commit**

```bash
git add .dockerignore
git commit -m "build: add .dockerignore for container builds"
```

---

## Task 2: Confirm the FastAPI bind address

Before writing the Dockerfile we need to know what `python -m app.main` actually does so the `EXPOSE` and healthcheck make sense. The existing `app/main.py` builds a FastAPI app but the **entrypoint** is `app.main:main` per `pyproject.toml`. Step 1 finds it; Step 2 ensures it binds to `0.0.0.0:8000`.

**Files:**
- Read: `app/main.py`
- Modify (if needed): `app/main.py` — the `main()` / `uvicorn.run(...)` call

- [ ] **Step 1: Locate the `main()` function and the `uvicorn.run` call**

Open `app/main.py`. Find the bottom of the file (or wherever the `main()` function lives). It should look something like:

```python
def main() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
```

If `host` is `"127.0.0.1"` or `"localhost"`, the container will refuse external connections — including from the host bridge — and the healthcheck will fail. Change it to `"0.0.0.0"`. If it's already `"0.0.0.0"`, skip Step 2.

- [ ] **Step 2: If needed, change the bind host**

```python
def main() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
```

- [ ] **Step 3: Commit (only if you changed it)**

```bash
git add app/main.py
git commit -m "fix(main): bind uvicorn to 0.0.0.0 for container networking"
```

---

## Task 3: Create the `Dockerfile`

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
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

# Data directory for SQLite + read-only YAML config bind-mount.
RUN mkdir -p /data /app/data && chown -R app:app /data /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

CMD ["python", "-m", "app.main"]
```

- [ ] **Step 2: Operator builds the image locally**

Ask the operator to run:

```
! docker build -t dev-trend:local .
```

Expected: build succeeds; final image lists `python:3.12-slim` as base; no warnings about running as root.

- [ ] **Step 3: Operator smoke-tests the image**

Ask the operator to run:

```
! docker run --rm -p 127.0.0.1:8000:8000 -e LLM_PROVIDER=mock -e EMBEDDING_PROVIDER=mock -e TELEGRAM_BOT_TOKEN=dummy -e TELEGRAM_CHAT_ID=0 dev-trend:local
```

In a second terminal:

```
! curl -fsS http://127.0.0.1:8000/health
```

Expected: `{"status":"ok","version":"..."}`. Stop the container with `Ctrl+C`.

- [ ] **Step 4: Verify non-root**

Ask the operator to run:

```
! docker run --rm dev-trend:local id
```

Expected: `uid=...(app) gid=...(app)` — not `uid=0(root)`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile
git commit -m "build: add multi-stage Dockerfile (uv builder + slim runtime)"
```

---

## Task 4: Create the production `docker-compose.yml`

**Files:**
- Create: `docker-compose.yml`

The production-shape compose file. SQLite lives in a named volume so it survives container recreation. `categories.yaml` is bind-mounted read-only because the app reads it at startup.

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  app:
    image: ghcr.io/l-desantis/dev-trend:${IMAGE_TAG:-latest}
    container_name: dev-trend-app
    restart: unless-stopped
    env_file:
      - .env
    environment:
      # Override DATABASE_URL so the SQLite file always lives in the volume.
      DATABASE_URL: sqlite+aiosqlite:////data/devtrend.db
    volumes:
      - devtrend_db:/data
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
  devtrend_db:
    name: devtrend_db
```

- [ ] **Step 2: Operator runs compose against the locally-built image**

Compose uses the `ghcr.io/...` image by default. For local testing we override `IMAGE_TAG` to point at the image built in Task 3. Ask the operator to run:

```
! docker tag dev-trend:local ghcr.io/l-desantis/dev-trend:latest
! cp .env.example .env  # if .env doesn't exist yet
! docker compose up -d
! sleep 15 && docker compose ps
! curl -fsS http://127.0.0.1:8000/health
```

Expected: `dev-trend-app` is `running (healthy)`; `/health` returns `200 ok`.

- [ ] **Step 3: Operator verifies SQLite persistence**

```
! docker compose exec app ls -la /data/
```

Expected: a `devtrend.db` file owned by `app`.

- [ ] **Step 4: Operator stops the stack**

```
! docker compose down
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "build: add production docker-compose.yml (single app service + SQLite volume)"
```

---

## Task 5: Create the local-dev `docker-compose.override.yml`

`docker-compose.override.yml` is loaded automatically by `docker compose` when present in the project directory. It customises the prod compose file for developer ergonomics: source bind-mount, port exposed on all interfaces for browser access from the host, DB swapped to a local bind-mounted file so the developer can inspect it.

**Files:**
- Create: `docker-compose.override.yml`

- [ ] **Step 1: Write `docker-compose.override.yml`**

```yaml
services:
  app:
    build: .
    image: dev-trend:dev
    # Bind-mount source so code edits show up without rebuilding.
    # The `./data/dev:/data` mount overrides the production named volume at the same path
    # so the SQLite file lives on the host where the developer can inspect it.
    volumes:
      - ./app:/app/app:ro
      - ./data:/app/data:ro
      - ./data/dev:/data
    environment:
      # Local DB lives in ./data/dev/devtrend.db on the host.
      DATABASE_URL: sqlite+aiosqlite:////data/devtrend.db
      LOG_LEVEL: DEBUG
    ports:
      - "0.0.0.0:8000:8000"
```

- [ ] **Step 2: Operator creates the dev data dir**

```
! mkdir -p data/dev
```

- [ ] **Step 3: Operator runs and smoke-tests**

```
! docker compose up -d --build
! sleep 20 && curl -fsS http://127.0.0.1:8000/health
! ls -la data/dev/
```

Expected: healthy response; `devtrend.db` visible on the host under `data/dev/`.

- [ ] **Step 4: Operator stops the stack**

```
! docker compose down
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.override.yml
git commit -m "build: add docker-compose.override.yml for local dev (bind-mount source + DB)"
```

---

## Task 6: README — "Run with docker compose" section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the existing "Run" / "Quickstart" section in README**

Find where the README documents `uv run devtrend` or local startup.

- [ ] **Step 2: Add a new subsection just below it**

```markdown
### Run with Docker Compose (local)

DevTrend ships with a production-shape `docker-compose.yml` and a dev-friendly `docker-compose.override.yml`. With Docker Desktop (or any modern Docker engine) installed:

```bash
cp .env.example .env       # fill in TELEGRAM_BOT_TOKEN, NIM/OpenAI keys, etc.
mkdir -p data/dev
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

The override file bind-mounts `./app/` into the container so code edits are picked up after a `docker compose restart app`. The SQLite DB lives at `./data/dev/devtrend.db` on the host.

To stop:

```bash
docker compose down
```

To run the production-shape stack (no source bind-mount, image pulled from `ghcr.io/l-desantis/dev-trend`):

```bash
docker compose -f docker-compose.yml up -d
```
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "doc: README — add Docker Compose quickstart"
```

---

## Verification (run by operator — see CLAUDE.md)

Run from a clean working tree, all changes committed:

1. **Build:**
   ```
   ! docker compose build
   ```
   Expected: builds successfully, no errors.

2. **Up + health:**
   ```
   ! docker compose up -d
   ! sleep 20
   ! docker compose ps
   ! curl -fsS http://127.0.0.1:8000/health
   ```
   Expected: container `running (healthy)`, `/health` returns `200 ok`.

3. **Non-root user:**
   ```
   ! docker compose exec app id
   ```
   Expected: `uid=...(app)`, not root.

4. **DB persistence:**
   ```
   ! docker compose restart app
   ! sleep 15
   ! docker compose exec app ls -la /data/
   ```
   Expected: `devtrend.db` still present after restart.

5. **Image size:**
   ```
   ! docker images dev-trend
   ```
   Expected: image size < 400 MB.

6. **Cleanup:**
   ```
   ! docker compose down
   ```

---

## Out of scope (explicit)

- **Postgres / Alembic.** Deferred to Plan D.
- **Nginx / TLS termination.** The app has no inbound traffic from the public internet (Telegram is outbound long-polling).
- **Multi-arch builds.** CX22 is amd64; building amd64 only.
- **CI integration (`docker build` in GitHub Actions).** That's Plan B.
- **Pushing to ghcr.io.** Plan B.
- **VPS deployment.** Plan C.

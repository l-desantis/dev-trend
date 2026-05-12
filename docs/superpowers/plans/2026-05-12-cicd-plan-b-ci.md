# CI/CD Plan B — GitHub Actions CI: lint, type-check, test, build, push

> **For agentic workers:** REQUIRED SUB-SKILL — use `superpowers:executing-plans` (or `superpowers:subagent-driven-development`) to work through this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

## Context

Plan A produced a Dockerfile and a compose file. This plan adds the GitHub Actions pipeline that builds, tests, and publishes the container image. It introduces three workflows:

1. **`ci.yml`** — runs on every PR and on push to `main`: `ruff check`, `ruff format --check`, `mypy`, `pytest -m 'not integration'`. No service container; tests still use SQLite.
2. **`build-and-push.yml`** — runs only after a `main` CI run goes green. Builds the Docker image (using Plan A's `Dockerfile`) and pushes it to `ghcr.io/l-desantis/dev-trend:sha-<short>` plus `:latest`, with GHA build cache for speed.
3. **`prune-ghcr.yml`** — weekly cron job that keeps only the most recent 10 `sha-*` tags plus `latest`.

See the design doc: `docs/superpowers/specs/2026-05-12-cicd-infrastructure-design.md`.

**Goal:** A push to `main` results in (a) a green CI run and (b) a new image at `ghcr.io/l-desantis/dev-trend:sha-<short>` within 8 minutes.

**Architecture:** Three independent workflows linked by `workflow_run` triggers. `build-and-push` depends on `ci` (only fires when CI is green on `main`). `prune-ghcr` is a standalone weekly cron.

**Tech Stack:** GitHub Actions, `astral-sh/setup-uv@v3`, `docker/setup-buildx-action`, `docker/build-push-action`.

**Environment note:** All commands run in CI — the operator does NOT need to install anything locally. Verification steps in this plan involve creating a test PR and watching the workflow run in the GitHub UI.

---

## File Structure

| File | Status | Responsibility |
| --- | --- | --- |
| `.github/workflows/ci.yml` | Create | PR + main CI: lint, typecheck, test. Three parallel jobs. |
| `.github/workflows/build-and-push.yml` | Create | On `main` after CI green: build container, push to ghcr.io. |
| `.github/workflows/prune-ghcr.yml` | Create | Weekly cron: keep last 10 `sha-*` tags + `latest`, delete the rest. |
| `pyproject.toml` | (No change) | `uv.lock` is already committed; CI uses `--frozen` against it. |

---

## Task 1: Create `ci.yml` — lint + typecheck + test

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the directory if needed**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

# Cancel in-progress CI runs for the same ref when a new push arrives.
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  lint:
    name: Lint (ruff)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.4.27"
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Sync dev deps
        run: uv sync --frozen
      - name: ruff check
        run: uv run ruff check .
      - name: ruff format --check
        run: uv run ruff format --check .

  typecheck:
    name: Type check (mypy)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.4.27"
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Sync dev deps
        run: uv sync --frozen
      - name: mypy
        run: uv run mypy app/

  test:
    name: Tests (pytest, unit only)
    runs-on: ubuntu-24.04
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
          # Tests in Plan B still use SQLite. Plan D will replace this with a postgres service.
          DATABASE_URL: sqlite+aiosqlite:///./test.db
          LLM_PROVIDER: mock
          EMBEDDING_PROVIDER: mock
        run: uv run pytest -m "not integration" -q
```

- [ ] **Step 3: Commit and push on a feature branch**

```bash
git checkout -b ci/add-ci-workflow
git add .github/workflows/ci.yml
git commit -m "ci: add lint + typecheck + test workflow"
git push -u origin ci/add-ci-workflow
```

- [ ] **Step 4: Open a draft PR and watch the workflow run**

Visit the repository on GitHub → Pull Requests → New PR from `ci/add-ci-workflow` → mark draft. Watch the "CI" check appear.

Expected: three jobs (`Lint`, `Type check`, `Tests`) run in parallel and all turn green within ~5 minutes. If any fail, fix the underlying issue (lint errors, type errors, failing tests) — **don't bypass them**.

- [ ] **Step 5: Merge to main**

Once green, mark the PR ready and merge.

---

## Task 2: Configure GHCR permissions (manual, one-shot)

GitHub Packages permissions are repo-level. For the next workflow to push to `ghcr.io/l-desantis/dev-trend`, the workflow's `GITHUB_TOKEN` needs `packages: write`. We set this at the workflow level (Step 1 of Task 3) but the **first** image push also needs the user to allow GitHub Actions to write to packages at the org/account level.

- [ ] **Step 1: Operator enables GHA → GHCR write at the account level**

Visit:

```
https://github.com/settings/packages
```

Under "Package settings" → ensure "Inherit access from source repository" is the default for new packages. (This is the default for personal accounts — the first push creates the package automatically and inherits repo permissions.)

- [ ] **Step 2: Operator confirms repository workflow permissions**

Visit:

```
https://github.com/l-desantis/dev-trend/settings/actions
```

Under "Workflow permissions":
- "Read and write permissions" — **selected**.
- "Allow GitHub Actions to create and approve pull requests" — leave unchecked (not needed).

Save.

---

## Task 3: Create `build-and-push.yml`

**Files:**
- Create: `.github/workflows/build-and-push.yml`

- [ ] **Step 1: Write `.github/workflows/build-and-push.yml`**

```yaml
name: Build and Push

on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]

concurrency:
  group: build-and-push-${{ github.ref }}
  cancel-in-progress: false

permissions:
  contents: read
  packages: write

jobs:
  build:
    # Only build when the triggering CI run was successful and on main.
    if: >
      github.event.workflow_run.conclusion == 'success' &&
      github.event.workflow_run.head_branch == 'main'
    runs-on: ubuntu-24.04
    outputs:
      short_sha: ${{ steps.meta.outputs.short_sha }}
    steps:
      - name: Checkout the commit that CI tested
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.workflow_run.head_sha }}

      - name: Compute short SHA
        id: meta
        run: echo "short_sha=$(git rev-parse --short=7 HEAD)" >> "$GITHUB_OUTPUT"

      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to ghcr.io
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          platforms: linux/amd64
          tags: |
            ghcr.io/l-desantis/dev-trend:sha-${{ steps.meta.outputs.short_sha }}
            ghcr.io/l-desantis/dev-trend:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max
          labels: |
            org.opencontainers.image.source=https://github.com/l-desantis/dev-trend
            org.opencontainers.image.revision=${{ github.event.workflow_run.head_sha }}
```

- [ ] **Step 2: Commit on a feature branch**

```bash
git checkout -b ci/add-build-and-push
git add .github/workflows/build-and-push.yml
git commit -m "ci: add build-and-push workflow (ghcr.io)"
git push -u origin ci/add-build-and-push
```

- [ ] **Step 3: Open and merge the PR**

The PR run will only execute the `CI` workflow (since `build-and-push` triggers only on `main`). Once merged, `build-and-push` should fire automatically after the merge-commit CI run finishes green.

- [ ] **Step 4: Watch the build-and-push run**

GitHub → Actions → "Build and Push". Expected: build succeeds in ~3 minutes, pushes both `sha-<short>` and `latest` tags. The `Packages` link on the repo page should now show `dev-trend`.

- [ ] **Step 5: Operator pulls the image to verify it works**

```
! docker pull ghcr.io/l-desantis/dev-trend:latest
! docker run --rm -p 127.0.0.1:8001:8000 -e LLM_PROVIDER=mock -e EMBEDDING_PROVIDER=mock -e TELEGRAM_BOT_TOKEN=dummy -e TELEGRAM_CHAT_ID=0 ghcr.io/l-desantis/dev-trend:latest
```

In another terminal:

```
! curl -fsS http://127.0.0.1:8001/health
```

Expected: `200 ok`.

---

## Task 4: Create `prune-ghcr.yml`

GHCR retains every image version forever by default. We want to keep just the most recent 10 sha-tagged builds plus `latest` (which always points at the most recent build).

**Files:**
- Create: `.github/workflows/prune-ghcr.yml`

- [ ] **Step 1: Write `.github/workflows/prune-ghcr.yml`**

```yaml
name: Prune GHCR

on:
  schedule:
    # Sundays at 04:00 UTC.
    - cron: "0 4 * * 0"
  workflow_dispatch:

permissions:
  packages: write

jobs:
  prune:
    runs-on: ubuntu-24.04
    steps:
      - name: Delete old sha-* versions, keep latest 10 + `latest`
        uses: actions/delete-package-versions@v5
        with:
          package-name: dev-trend
          package-type: container
          # Keep the 10 most recent versions that have at least one tag.
          min-versions-to-keep: 10
          # Skip any version that has the `latest` tag.
          ignore-versions: '^latest$'
```

- [ ] **Step 2: Commit on a feature branch**

```bash
git checkout -b ci/add-prune-ghcr
git add .github/workflows/prune-ghcr.yml
git commit -m "ci: add weekly GHCR prune workflow (keep last 10 sha tags)"
git push -u origin ci/add-prune-ghcr
```

- [ ] **Step 3: Open and merge the PR**

- [ ] **Step 4: Operator triggers a manual run to verify the workflow itself works**

GitHub → Actions → "Prune GHCR" → "Run workflow" on `main`. Expected: run succeeds; if there are fewer than 10 versions, nothing is deleted (which is fine for the first weeks).

---

## Verification (run by operator — see CLAUDE.md)

End-to-end check after all four tasks are merged:

1. **Open a no-op PR** (e.g., add a blank line to README) to verify the CI gate still fires on PRs:
   - Push branch, open PR.
   - Expected: `CI` runs and passes; `Build and Push` does **not** run.

2. **Merge to main:**
   - Expected: `CI` re-runs on the merge commit and passes; `Build and Push` then fires automatically.
   - Total elapsed time from merge → image available on ghcr.io: < 8 minutes.

3. **Verify ghcr.io tags:**

   ```
   ! docker manifest inspect ghcr.io/l-desantis/dev-trend:latest
   ! docker manifest inspect ghcr.io/l-desantis/dev-trend:sha-<short>
   ```

   Expected: both return a valid manifest.

4. **Manually trigger the prune workflow** to confirm it doesn't error:
   - GitHub → Actions → Prune GHCR → Run workflow.

---

## Out of scope (explicit)

- **Deploying the image to the VPS.** That's Plan C (the `deploy.yml` workflow).
- **Secrets management.** Plan C (SOPS + age).
- **Health-gated rollback.** Plan C.
- **Postgres / Alembic in CI.** Plan D — the test job's `DATABASE_URL` will switch to a postgres service container at that point.
- **Coverage reporting, Codecov, security scanning (Trivy).** Out of scope for the first cut.
- **Multi-arch (arm64) builds.** CX22 is amd64; we don't need arm64 yet.

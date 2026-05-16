# Logging Noise Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Silence recurring third-party log noise (`uvicorn.access` health-check lines and `httpx` Telegram `getUpdates` lines that leak the bot token) so `app.*` business-logic events become readable in `docker logs`.

**Architecture:** Add a single 2-line loop inside `_configure_logging()` in `app/main.py` that pins five third-party loggers to `WARNING`. No new env vars, no new sinks, no formatter changes, no structlog changes. Verification is by observation in production logs.

**Tech Stack:** Python stdlib `logging`, `structlog` (already configured), FastAPI / Uvicorn, python-telegram-bot, httpx — all already in use.

**Source spec:** `docs/superpowers/specs/2026-05-16-logging-noise-reduction-design.md`

---

## File Structure

Files touched by this plan:

- **Modify:** `app/main.py` — single function `_configure_logging()` (lines 16-30). Add a mute loop between `logging.basicConfig(...)` (line 19) and `structlog.configure(...)` (line 20).

No new files. No tests added (per spec non-goals — change is too small and observable to warrant a unit test of logging configuration internals).

---

## Task 1: Mute noisy third-party loggers in `_configure_logging`

**Files:**
- Modify: `app/main.py:16-30` (function `_configure_logging`)

- [ ] **Step 1: Open `app/main.py` and locate `_configure_logging`**

Confirm the function currently reads exactly:

```python
def _configure_logging() -> None:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
```

If this block has drifted from the spec baseline, stop and re-sync with the user before editing.

- [ ] **Step 2: Insert the mute loop**

Edit `app/main.py` so the function becomes:

```python
def _configure_logging() -> None:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level)

    # Silence noisy third-party loggers so app.* events stay readable.
    # httpx also logs outbound URLs which include the Telegram bot token.
    for name in ("uvicorn.access", "httpx", "httpcore", "telegram", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
```

Notes on the placement:
- The loop runs AFTER `logging.basicConfig` so the root logger exists when we look up children.
- The loop runs BEFORE `structlog.configure` for readability (group all stdlib `logging` setup together), but the order between these two does not actually matter — `structlog.configure` does not change per-logger levels.

The two-line comment is the only comment added; do not add per-line annotations.

- [ ] **Step 3: Local sanity check (syntax + import)**

The user runs Python; ask them to run:

```bash
! uv run python -c "from app.main import _configure_logging; _configure_logging(); import logging; print({n: logging.getLogger(n).getEffectiveLevel() for n in ('uvicorn.access','httpx','httpcore','telegram','telegram.ext','app','apscheduler')})"
```

Expected output (levels are integer constants: WARNING=30, INFO=20):

```
{'uvicorn.access': 30, 'httpx': 30, 'httpcore': 30, 'telegram': 30, 'telegram.ext': 30, 'app': 20, 'apscheduler': 20}
```

If `app` or `apscheduler` show 30, the change is wrong (it leaked into loggers that should stay at INFO). Stop and fix before continuing.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "chore(logging): mute noisy third-party loggers to WARNING

Silences uvicorn.access (health-check spam), httpx + httpcore
(outbound HTTP traces — also leaked the Telegram bot token in URLs),
and telegram / telegram.ext (long-poll chatter). app.* and apscheduler
keep their existing INFO level."
```

The user runs git themselves — present the diff and the commit command, do not execute.

---

## Task 2: Rotate Telegram bot token

**Files:** none in the repo. Operational task.

The current bot token has been written to logs (and pasted into a prior conversation). Treat it as compromised.

- [ ] **Step 1: Ask the user to rotate the token in @BotFather**

In Telegram, open @BotFather → `/mybots` → select the DevTrend bot → API Token → Revoke current token → Generate new token.

- [ ] **Step 2: Update the deployment secret**

Update `TELEGRAM_BOT_TOKEN` in the SOPS-encrypted `.env` file or wherever the VPS pulls its environment from (see `.sops.yaml` / `docs/superpowers/specs/2026-05-12-cicd-infrastructure-design.md` for the deploy path).

- [ ] **Step 3: Redeploy and confirm the bot still answers**

After the next deploy, send `/help` to the bot from an allow-listed chat. Expected: bot replies. If not, check that the new token was wired into the running container's environment.

This task is independent of Task 1 and can run in parallel. It is part of this plan because the token exposure is what motivates muting `httpx` urgently rather than later.

---

## Task 3: Verify on the VPS after deploy

**Files:** none. Observational task on the deployed container.

- [ ] **Step 1: Trigger a redeploy**

Push the commit from Task 1 to `main` (the existing CI/CD pipeline picks it up — see `.github/workflows/deploy.yml`). The user controls when to push.

- [ ] **Step 2: Tail logs and confirm absence of noise**

On the VPS:

```bash
docker logs -f dev-trend-app 2>&1 | head -200
```

Watch for ~60 seconds (one full Telegram poll interval plus several health checks).

Expected:
- NO lines matching `INFO:.*GET /health HTTP/1.1`
- NO lines matching `HTTP Request: POST https://api.telegram.org/.*getUpdates`
- `app.*` events still present (e.g. scheduler ticks, ingestion runs, bot command handling if the user sends one)
- `apscheduler` INFO lines still present (kept intentionally)

If a noise line still appears, identify its logger name (turn on `logging.basicConfig(level=DEBUG, format="%(name)s %(levelname)s %(message)s")` locally to find it) and add it to the muted tuple in `app/main.py`.

- [ ] **Step 3: Confirm a real warning still surfaces (optional smoke test)**

If easy: temporarily break the Telegram token in `.env`, redeploy, confirm that `httpx` / `telegram` errors do reach the logs (they should — WARNING and above still pass). Then restore the correct token. Skip this step if you trust that the WARNING threshold works; it's the stdlib default behavior.

- [ ] **Step 4: Mark the plan complete**

No further action. Close any related KANBAN entries.

---

## Self-review

**Spec coverage:**
- Spec "Design / Change surface" → Task 1 Step 2 ✓
- Spec "Loggers muted to WARNING" table → Task 1 Step 2 (same five loggers) ✓
- Spec "Loggers explicitly NOT muted" (`apscheduler`, `app.*`) → Task 1 Step 3 asserts both stay at INFO ✓
- Spec "Implementation steps" #1 (edit `app/main.py`) → Task 1 ✓
- Spec "Implementation steps" #2 (rebuild + redeploy) → Task 3 Step 1 ✓
- Spec "Implementation steps" #3 (rotate Telegram bot token) → Task 2 ✓
- Spec "Implementation steps" #4 (verify on VPS) → Task 3 Step 2 ✓
- Spec "Risks" (real lib WARN+ still surfaces) → Task 3 Step 3 ✓

**Placeholder scan:** No TBD / TODO / "implement later". Every code-changing step shows the exact final code.

**Type / identifier consistency:** The tuple of logger names is identical in Task 1 Step 2 (edit), Task 1 Step 3 (verification), and Task 3 Step 2 (observation). The function name `_configure_logging` is consistent across tasks.

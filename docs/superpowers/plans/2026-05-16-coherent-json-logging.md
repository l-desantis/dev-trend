# Coherent JSON Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every stdlib log record (uvicorn, APScheduler, telegram, etc.) through the same structlog JSON formatter the app already uses, so `docker logs dev-trend-app` is a single line-delimited JSON stream — without changing any business logic and without introducing file/external logging.

**Architecture:**
Use `structlog.stdlib.ProcessorFormatter` as the *single* root handler's formatter, with `foreign_pre_chain` to render stdlib `LogRecord`s as JSON. Configure structlog to wrap its own events with `ProcessorFormatter.wrap_for_formatter` so app + foreign logs share one renderer. Move the call to `_configure_logging()` to run inside `main()` *before* `uvicorn.run(...)`, and pass `log_config=None` so uvicorn does not overwrite our setup with its own `dictConfig`. Keep the existing lifespan call as a defensive no-op (handler list is cleared on each call → idempotent).

**Tech Stack:** Python stdlib `logging`, `structlog` (already configured), FastAPI / Uvicorn, APScheduler, python-telegram-bot, httpx, pytest (already in dev deps).

**Source context:**
- Current `_configure_logging()` lives at `app/main.py:16-36`.
- Existing related plan: `docs/superpowers/plans/2026-05-16-logging-noise-reduction.md` (already merged the WARNING mutes for `uvicorn.access`, `httpx`, `httpcore`, `telegram`, `telegram.ext`).
- Entry-point: `pyproject.toml:38` (`devtrend = "app.main:main"`).
- Dockerfile `CMD` is `["devtrend"]` (`Dockerfile:53`), so `main()` is the *very first* Python frame after the console-script shim. Configuring logging at the top of `main()` runs before uvicorn touches `logging`.

---

## Why the old output was mixed

1. `logging.basicConfig(format="%(message)s", level=log_level)` installed a plain-text `StreamHandler` on the root logger. Any stdlib logger (APScheduler, telegram) that propagated to root got that plain text formatter.
2. `structlog.configure(..., logger_factory=structlog.stdlib.LoggerFactory(), processors=[..., JSONRenderer()])` made structlog render its events to a final string and hand them to stdlib. Stdlib then printed that already-JSON string via the same root `StreamHandler` (which only uses `%(message)s`, so the JSON survives). That's why app logs *look* JSON-clean.
3. Uvicorn calls `logging.config.dictConfig(LOGGING_CONFIG)` during `uvicorn.run()`, which **replaces** the handlers on `uvicorn`, `uvicorn.error`, `uvicorn.access` with its own plain-text formatter — independent of the root handler. That's the source of `INFO: Started server process ...` lines.
4. APScheduler's `Scheduler started` / `Added job ...` lines come from logger `apscheduler.scheduler` which propagates to root → plain-text handler → unstructured output.

Net effect: app logs (structlog) emit JSON; uvicorn logs emit uvicorn's plain text; APScheduler/telegram propagate to root's plain-text handler. Three formatters, one stream → mixed output.

The fix: one formatter — `ProcessorFormatter` — on the root, plus `wrap_for_formatter` on structlog, plus `log_config=None` on `uvicorn.run` so uvicorn does not re-install its plain-text handlers.

---

## File Structure

Files touched by this plan:

- **Modify:** `app/main.py` — function `_configure_logging()` (lines 16-36) and function `main()` (lines 136-139). Add an early call to `_configure_logging()` from `main()` and pass `log_config=None` to `uvicorn.run`.
- **Create:** `tests/test_logging_config.py` — unit test that `_configure_logging()` installs the expected formatter/handler shape and mutes the right loggers. Pytest is already configured in this repo (see `tests/` for examples).

No other files change. No new dependencies. No new env vars. No new files in `app/`.

---

## Environment & execution constraints

- Per repo `CLAUDE.md`: the agent must NOT run `uv`, `python`, or `pytest` directly. For every "Run" step, paste the command for the user to execute and wait for their output before continuing.
- Use the existing test discovery convention from `tests/` (plain `pytest` files, `test_*` naming).

---

## Task 1: Add a failing unit test for the new logging configuration

**Files:**
- Create: `tests/test_logging_config.py`

This test pins the *observable* contract:
1. After `_configure_logging()`, the root logger has exactly one handler whose formatter is a `structlog.stdlib.ProcessorFormatter`.
2. Noisy loggers (`uvicorn.access`, `httpx`, `httpcore`, `telegram`, `telegram.ext`) are at `WARNING`.
3. Calling `_configure_logging()` twice does NOT duplicate root handlers (idempotency — important because both `main()` and lifespan call it).
4. A stdlib `logging.getLogger("apscheduler.scheduler").info("Scheduler started")` call produces JSON containing `"event": "Scheduler started"` on stdout.

- [ ] **Step 1: Write the failing test**

Create `tests/test_logging_config.py` with the following content. This is the *complete* file:

```python
import json
import logging
from contextlib import contextmanager

import pytest
import structlog.stdlib

from app.main import _configure_logging


@contextmanager
def _isolated_root_logger():
    """Snapshot/restore root handlers + level so tests don't leak global state."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        root.handlers = []
        yield root
    finally:
        root.handlers = saved_handlers
        root.level = saved_level


def test_configure_logging_installs_single_processor_formatter_handler():
    with _isolated_root_logger() as root:
        _configure_logging()

        assert len(root.handlers) == 1, (
            f"expected exactly one root handler, got {root.handlers!r}"
        )
        handler = root.handlers[0]
        assert isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter), (
            f"expected ProcessorFormatter, got {type(handler.formatter)!r}"
        )


def test_configure_logging_mutes_noisy_loggers():
    with _isolated_root_logger():
        _configure_logging()

        for name in (
            "uvicorn.access",
            "httpx",
            "httpcore",
            "telegram",
            "telegram.ext",
        ):
            assert logging.getLogger(name).level == logging.WARNING, (
                f"logger {name!r} not pinned to WARNING"
            )


def test_configure_logging_is_idempotent():
    with _isolated_root_logger() as root:
        _configure_logging()
        _configure_logging()
        _configure_logging()

        assert len(root.handlers) == 1, (
            f"configure_logging is not idempotent: {root.handlers!r}"
        )


def test_foreign_stdlib_log_renders_as_json(capfd: pytest.CaptureFixture[str]):
    with _isolated_root_logger():
        _configure_logging()
        # apscheduler.scheduler is a stdlib logger that propagates to root.
        logging.getLogger("apscheduler.scheduler").info("Scheduler started")

    captured = capfd.readouterr()
    # The formatted record goes to stderr by default (StreamHandler() default stream).
    line = (captured.err or captured.out).strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "Scheduler started"
    assert payload["logger"] == "apscheduler.scheduler"
```

- [ ] **Step 2: Run the test and confirm it fails**

Ask the user to run:

```
! uv run pytest tests/test_logging_config.py -v
```

Expected: tests fail (most likely `AssertionError` on handler count / formatter type / JSON parse), because `_configure_logging()` currently installs a plain-text `basicConfig` handler.

Do NOT proceed to Task 2 until the user confirms the test failed for the *expected* reason (formatter shape / JSON mismatch), not e.g. an `ImportError`.

- [ ] **Step 3: Commit the failing test**

Ask the user to run:

```
git add tests/test_logging_config.py
git commit -m "test(logging): pin contract for unified ProcessorFormatter setup"
```

---

## Task 2: Rewrite `_configure_logging()` to install `ProcessorFormatter` on the root

**Files:**
- Modify: `app/main.py:16-36` (function `_configure_logging`)

The new function:
- Clears existing root handlers (idempotent + neutralises any prior `basicConfig` / pytest handlers).
- Installs one `StreamHandler` whose formatter is `ProcessorFormatter` rendering JSON.
- Uses `foreign_pre_chain` to add `level`, `logger name`, and ISO timestamp to *stdlib-originated* records (uvicorn, apscheduler, telegram).
- Reconfigures structlog so structlog events go through `ProcessorFormatter.wrap_for_formatter` (single render path).
- Keeps the existing WARNING mutes.

- [ ] **Step 1: Replace `_configure_logging` in `app/main.py`**

Current content (`app/main.py:16-36`):

```python
def _configure_logging() -> None:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level)

    # Silence noisy third-party loggers so app.* events stay readable.
    # httpx also logs outbound URLs which include the Telegram bot token.
    for name in ("uvicorn.access", "httpx", "httpcore"):
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

Replace it with exactly this (note: the WARNING mute list is widened to include `telegram` / `telegram.ext` to match the noise-reduction plan that landed earlier; if those two are already in your tree, this just makes them explicit in one place):

```python
def _configure_logging() -> None:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso")

    # Processors applied to records that did NOT originate from structlog
    # (uvicorn, apscheduler, telegram, etc.). They turn a stdlib LogRecord
    # into the same event_dict shape structlog produces, so the final
    # JSONRenderer can format both kinds identically.
    foreign_pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=foreign_pre_chain,
    )

    handler = logging.StreamHandler()  # stderr by default — Docker captures it.
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Idempotent: wipe any prior handlers (basicConfig, pytest, prior call)
    # so we never double-log.
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(log_level)

    # Silence noisy third-party loggers so app.* events stay readable.
    # httpx also logs outbound URLs which include the Telegram bot token.
    for name in ("uvicorn.access", "httpx", "httpcore", "telegram", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            timestamper,
            # Hand the event_dict to ProcessorFormatter, which finishes the
            # rendering via the JSONRenderer above. Single render path for
            # both stdlib- and structlog-originated records.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

Use the `Edit` tool with the entire current function as `old_string` and the new function as `new_string`.

- [ ] **Step 2: Run the unit tests and confirm they pass**

Ask the user to run:

```
! uv run pytest tests/test_logging_config.py -v
```

Expected: all four tests pass.

If `test_foreign_stdlib_log_renders_as_json` fails because the JSON line lacks `logger` key, double-check that `structlog.stdlib.add_logger_name` is present in `foreign_pre_chain`. If it fails because nothing was captured on stderr, double-check that the handler is `logging.StreamHandler()` (no `sys.stdout` arg) — `capfd` captures both.

- [ ] **Step 3: Run the full test suite to make sure nothing else broke**

Ask the user to run:

```
! uv run pytest -x
```

Expected: full suite still green. (Other tests should not depend on the legacy `basicConfig` plain-text formatter; if any do, treat it as a test bug — they should snapshot/restore root handlers themselves, exactly like the new test does.)

- [ ] **Step 4: Commit**

Ask the user to run:

```
git add app/main.py
git commit -m "feat(logging): unify foreign stdlib logs under structlog ProcessorFormatter"
```

---

## Task 3: Configure Uvicorn launch so it does not overwrite our logging

**Files:**
- Modify: `app/main.py:136-139` (function `main`)

Uvicorn's `run()` calls `logging.config.dictConfig(LOGGING_CONFIG)` during boot, replacing handlers on `uvicorn`, `uvicorn.error`, `uvicorn.access`. We need to:
1. Run `_configure_logging()` *before* `uvicorn.run` so the root handler is in place before uvicorn imports the app.
2. Pass `log_config=None` so uvicorn does not install its own handlers — `uvicorn` and `uvicorn.error` will then propagate to the root handler and be JSON-rendered.
3. Pass `access_log=False` so uvicorn doesn't bother emitting access lines at all (we mute `uvicorn.access` to WARNING anyway, but `access_log=False` is the cleanest "don't even try" knob).

Note: the lifespan also calls `_configure_logging()` (`app/main.py:41`). Leave it — it is now provably idempotent (see Task 1 test) and provides a safety net for any code path that imports `app.main:app` without going through `main()` (e.g. `uvicorn app.main:app` directly).

- [ ] **Step 1: Update `main()` in `app/main.py`**

Current content (`app/main.py:136-139`):

```python
def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
```

Replace with:

```python
def main() -> None:
    import uvicorn

    # Configure logging BEFORE uvicorn boots so our root handler is in place
    # when uvicorn imports the app (and so `log_config=None` below doesn't
    # leave us briefly with no handlers at all).
    _configure_logging()

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        # Don't let uvicorn install its own dictConfig — it would clobber
        # the ProcessorFormatter handler we just set up on the root logger.
        log_config=None,
        # Access logs are noisy and we already pin uvicorn.access to WARNING;
        # turning the access logger off entirely is the cleanest signal.
        access_log=False,
    )
```

- [ ] **Step 2: Run the unit tests again (sanity check)**

Ask the user to run:

```
! uv run pytest tests/test_logging_config.py -v
```

Expected: still green (this task did not touch `_configure_logging`, but we want to confirm the import-time side-effect of running `_configure_logging()` from `main()` is not surfacing in test collection — which it shouldn't, because pytest doesn't call `main()`).

- [ ] **Step 3: Commit**

Ask the user to run:

```
git add app/main.py
git commit -m "feat(logging): wire uvicorn to share the root JSON handler (log_config=None)"
```

---

## Task 4: Manual verification in Docker

This task has no code changes. It is the runbook for confirming the JSON pipeline is end-to-end coherent. The user must run these — record the outputs in the PR description.

- [ ] **Step 1: Rebuild the app image and recreate the container**

Ask the user to run:

```
docker compose up -d --build app
```

Expected: container starts; healthcheck eventually becomes healthy. No errors during build.

- [ ] **Step 2: Inspect startup logs**

Ask the user to run:

```
docker logs dev-trend-app | head -50
```

Confirm every line is a valid JSON object. Specifically, look for JSON-rendered versions of:
- Uvicorn's `Started server process [...]` (should appear as `{"event": "Started server process [...]", "logger": "uvicorn.error", ...}` — uvicorn emits startup messages on `uvicorn.error` despite the name).
- `Application startup complete.` (also `uvicorn.error`).
- `Database reachable`, `Categories synced`, `Scheduler started` (app, via structlog — already JSON).
- APScheduler's `Scheduler started` and `Added job "..."` lines (logger `apscheduler.scheduler` / `apscheduler.executors.default`) — these MUST now be JSON, not plain text. This is the smoke test for Task 2.

If any plain-text line appears *after* `Configuring logging` (i.e. anything beyond the first frame of Python startup), stop and debug — the fix is incomplete. The most common culprit is a third-party library configuring its own handlers; track it down with:

```
docker exec dev-trend-app python -c "import logging; print(logging.getLogger().handlers)"
```

Expected output: exactly one handler, a `StreamHandler` with a `ProcessorFormatter`.

- [ ] **Step 3: Trigger a health check and confirm no plain-text access lines reappear**

Ask the user to run:

```
curl -sf http://localhost:8000/health && echo OK
docker logs --tail 20 dev-trend-app
```

Expected: `OK` printed. The recent log lines should NOT contain `INFO:     127.0.0.1:... - "GET /health HTTP/1.1" 200 OK` (uvicorn.access). If they do, `access_log=False` did not take effect — re-check Task 3 Step 1.

- [ ] **Step 4: Validate JSON parseability of the whole stream**

Ask the user to run:

```
docker logs dev-trend-app 2>&1 | python3 -c "import sys, json; [json.loads(l) for l in sys.stdin if l.strip()]; print('all lines parse as JSON')"
```

Expected: `all lines parse as JSON`. Any `json.JSONDecodeError` means a stray plain-text line slipped through — capture the offending line for debugging.

- [ ] **Step 5: Document and commit the verification record**

Ask the user to paste the (truncated) output of Step 2 into the PR description, then optionally bump KANBAN.md / docs if the repo's convention requires it (check `KANBAN.md` for a "Done" section). If no doc update is needed, skip the doc edit and commit nothing — the runbook itself is the deliverable.

---

## Remaining unavoidable plain-text lines

After this plan lands, the *only* lines that may still appear as non-JSON in `docker logs dev-trend-app` are emitted **before** Python control reaches `main()`:

1. The `devtrend` console-script shim itself — there is none in practice; setuptools/pip generates a 5-line Python wrapper that imports `app.main:main` and calls it. No log output.
2. Any stderr output from Python's own startup (e.g. a `SyntaxError` in our code, or a `DeprecationWarning` printed before `_configure_logging()` runs).
3. Process-level crashes (`Segmentation fault`, OOM killer) — these come from the kernel, not Python.

In normal operation: zero plain-text lines.

Two caveats worth noting in the PR description, not fixes:
- **Uvicorn's startup messages route through `uvicorn.error`** (not `uvicorn`). That's a long-standing uvicorn quirk; the `logger` field in our JSON will say `uvicorn.error` for benign startup messages. Don't be alarmed.
- **APScheduler emits at DEBUG when adding the very first job tentatively** — those won't appear unless `LOG_LEVEL=DEBUG`. The visible APScheduler INFO lines (`Scheduler started`, `Added job ...`) WILL flow through JSON.

---

## Self-Review (filled in by the author of this plan)

**Spec coverage check:**

| Requirement from the spec | Covered by |
| --- | --- |
| Review logging in `app/main.py`, `app/ingestion/scheduler.py`, uvicorn/APScheduler config sites | "Why the old output was mixed" + Task 3 |
| App logs remain JSON via structlog | Task 2 (structlog still configured with `wrap_for_formatter` → JSONRenderer) |
| Stdlib logs from libraries formatted consistently | Task 2 (ProcessorFormatter + foreign_pre_chain) |
| `uvicorn.access` muted | Task 2 (level=WARNING) + Task 3 (`access_log=False`) |
| Noisy loggers pinned to WARNING (`uvicorn.access`, `httpx`, `httpcore`, `telegram`, `telegram.ext`) | Task 2 (mute loop) |
| APScheduler messages flow through formatter | Task 2 (`apscheduler.*` propagates to root → ProcessorFormatter) |
| Uvicorn launch-time config (not app-only) addressed | Task 3 (`log_config=None`, `access_log=False`) |
| No file logging / external services / observability framework | None of the tasks introduces any |
| No business-logic changes | Only `app/main.py` `_configure_logging` and `main` touched |
| Simple and reversible | Whole change is one function rewrite + a few uvicorn kwargs; revert = `git revert` |
| Avoid double logging / propagation issues | Task 2 clears root handlers before adding; lifespan call is idempotent (Task 1 test) |
| Apply the code changes | Tasks 2 & 3 |
| Explain why the old mixed formatting happened | "Why the old output was mixed" section |
| Summarize exactly what changed | Task commit messages + this table |
| Note remaining unavoidable plain-text lines | "Remaining unavoidable plain-text lines" section |
| Give commands to verify in Docker | Task 4 |

**Placeholder scan:** none — every code block is complete, every command is concrete.

**Type/name consistency:** `_configure_logging` is referenced by the same name everywhere (Task 1 import, Task 2 rewrite, Task 3 call from `main`). `ProcessorFormatter` is the canonical structlog symbol used in test + impl. No drift.

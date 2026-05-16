# Logging Noise Reduction — Design

**Status**: Approved (pending user review of this document)
**Date**: 2026-05-16
**Owner**: Lorenzo De Santis

## Problem

The application emits one stdout stream at `LOG_LEVEL=INFO`. Third-party libraries —
chiefly `python-telegram-bot` long-polling and the underlying `httpx` client — fill
the stream with two recurring lines on every poll cycle and every Docker healthcheck:

```
dev-trend-app  | INFO:     127.0.0.1:35328 - "GET /health HTTP/1.1" 200 OK
dev-trend-app  | HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates "HTTP/1.1 200 OK"
```

These lines drown out the `app.*` business-logic events the operator actually needs
to see when inspecting the bot or pipeline. The `httpx` line additionally **leaks
the Telegram bot token** into the log stream, which is a real (low-grade) security
concern in addition to a readability one.

## Goal

Silence the recurring third-party noise on stdout so that `app.*` INFO/WARN/ERROR
events are easy to read in `docker logs -f`. Keep current logging architecture
(structlog → JSON to stdout) otherwise unchanged.

## Non-goals

- No new log sinks (no rotating files, no separate stderr stream).
- No formatter changes (stdout stays JSON via `structlog.JSONRenderer`).
- No changes to `structlog.configure(...)`.
- No new environment variables or runtime configuration knobs.
- No changes to call sites in `app/bot/*` or anywhere else.
- No unit tests of `_configure_logging` internals — verification happens by
  observation on the VPS after deploy.

## Design

### Change surface

A single, additive edit inside `_configure_logging()` in `app/main.py`, placed
immediately after `logging.basicConfig(...)` and before `structlog.configure(...)`:

```python
for name in ("uvicorn.access", "httpx", "httpcore", "telegram", "telegram.ext"):
    logging.getLogger(name).setLevel(logging.WARNING)
```

### Loggers muted to WARNING

| Logger          | Noise it produces                                              |
|-----------------|----------------------------------------------------------------|
| `uvicorn.access`| Per-request access log line for `/health` and any HTTP route   |
| `httpx`         | `HTTP Request: ...` line on every outbound call (token leak)   |
| `httpcore`      | Lower-level HTTP chatter — defensive in case root goes to DEBUG|
| `telegram`      | Parent logger for python-telegram-bot                           |
| `telegram.ext`  | Long-poll + handler dispatch chatter                           |

### Loggers explicitly NOT muted

- `apscheduler` — its INFO lines (`Job X added`, `next run at...`, missed-job warnings)
  are useful during ongoing VPS debugging of scheduler behavior. Leave at the global
  `LOG_LEVEL` default.
- All `app.*` loggers — continue to inherit the global `LOG_LEVEL` (default INFO).

### Behavior after change

- `docker logs -f dev-trend-app` shows `app.*` events plus warnings/errors from any
  source. The two recurring noise lines disappear.
- If a third-party library logs a real warning/error (e.g. `httpx` connection failure),
  it still surfaces because the threshold is WARNING, not silenced entirely.
- Setting global `LOG_LEVEL=DEBUG` will NOT re-enable the muted loggers — they
  remain pinned at WARNING. To debug one of them temporarily, the muted tuple must
  be edited or that logger's level explicitly raised in code.

## Implementation steps

1. Edit `app/main.py`: add the 2-line mute loop in `_configure_logging()`.
2. Rebuild + redeploy via existing CI/CD pipeline (no infra/config changes needed).
3. **Rotate the Telegram bot token** via @BotFather (the current token has been
   exposed in logs prior to this fix).
4. Verify on VPS: `docker logs -f dev-trend-app` should no longer show `GET /health`
   access lines or `HTTP Request: ... getUpdates ...` lines, while ingestion /
   pipeline / bot handler events still appear.

## Risks and reversibility

- **Risk**: A real error from one of the muted libraries that only logs at INFO
  would be hidden. Mitigated: WARNING and ERROR still pass through; libraries
  generally use WARNING+ for real problems.
- **Reversibility**: Trivial. Revert the single edit; no migrations, no state.

## Out of scope (deferred)

The following were considered and explicitly deferred:

- `LOG_LEVEL_OVERRIDES` env var for per-logger runtime tuning — YAGNI; edit the
  tuple if needed.
- Separate WARN+ rotating file or stderr stream — was the initial framing but
  superseded by the simpler "mute the noise" approach.
- Switching stdout from JSON to human-readable text — current JSON is acceptable.
- Unit tests on `_configure_logging` — disproportionate for a 5-line change;
  verify by observation on the VPS.

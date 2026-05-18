# Logging Noise Reduction — Design

**Status**: Approved (pending user review of this document)
**Date**: 2026-05-16 (revised 2026-05-18 — added human-readable renderer swap)
**Owner**: Lorenzo De Santis

## Problem

The application emits one stdout stream at `LOG_LEVEL=INFO`. Two issues compound:

**1. Third-party noise.** `python-telegram-bot` long-polling and the underlying
`httpx` client fill the stream with two recurring lines on every poll cycle and
every Docker healthcheck:

```
dev-trend-app  | INFO:     127.0.0.1:35328 - "GET /health HTTP/1.1" 200 OK
dev-trend-app  | HTTP Request: POST https://api.telegram.org/bot<TOKEN>/getUpdates "HTTP/1.1 200 OK"
```

The `httpx` line additionally **leaks the Telegram bot token** into the log
stream — a real (low-grade) security concern on top of the readability problem.

**2. JSON is hard to read by eye.** The current `structlog.JSONRenderer` output
is great for log aggregators but painful for `docker logs -f` tail-watching:

```
{"event":"Scheduler started","logger":"main","level":"info","timestamp":"2026-05-18T10:35:42.789Z","component":"main"}
```

The operator's primary log consumer right now is the human running `docker logs -f`
on the VPS, not an aggregator.

## Goal

Make `docker logs -f dev-trend-app` easy to read by eye:

1. Silence the recurring third-party noise so `app.*` events stand out.
2. Swap the stdout renderer from JSON to a human-readable format that still
   surfaces structured kwargs (so context like `job_id`, `component`,
   `elapsed_s` is not lost).

Keep the architecture (structlog → stdout via a single `ProcessorFormatter`
handler) otherwise unchanged.

## Non-goals

- No new log sinks (no rotating files, no separate stderr stream).
- No changes to `structlog.configure(...)` processor chain (only the final
  renderer inside the `ProcessorFormatter` changes).
- No new environment variables or runtime configuration knobs.
- No changes to call sites in `app/bot/*` or anywhere else.
- No colors in the rendered output (plain text; safe for `docker logs`, grep,
  and any future log-aggregator consumer).

## Design

Two edits inside `_configure_logging()` in `app/main.py`. The mute loop is
already in place; this revision adds the renderer swap.

### Change 1 — Mute noisy third-party loggers (already shipped)

Placed after `root.setLevel(...)` and before `structlog.configure(...)`:

```python
for name in ("uvicorn.access", "httpx", "httpcore", "telegram", "telegram.ext"):
    logging.getLogger(name).setLevel(logging.WARNING)
```

#### Loggers muted to WARNING

| Logger          | Noise it produces                                              |
|-----------------|----------------------------------------------------------------|
| `uvicorn.access`| Per-request access log line for `/health` and any HTTP route   |
| `httpx`         | `HTTP Request: ...` line on every outbound call (token leak)   |
| `httpcore`      | Lower-level HTTP chatter — defensive in case root goes to DEBUG|
| `telegram`      | Parent logger for python-telegram-bot                           |
| `telegram.ext`  | Long-poll + handler dispatch chatter                           |

#### Loggers explicitly NOT muted

- `apscheduler` — its INFO lines (`Job X added`, `next run at...`, missed-job warnings)
  are useful during ongoing VPS debugging of scheduler behavior. Leave at the global
  `LOG_LEVEL` default.
- All `app.*` loggers — continue to inherit the global `LOG_LEVEL` (default INFO).

### Change 2 — Swap JSONRenderer for ConsoleRenderer (this revision)

Replace the final renderer inside the `ProcessorFormatter`:

```python
# before
formatter = structlog.stdlib.ProcessorFormatter(
    processor=structlog.processors.JSONRenderer(),
    foreign_pre_chain=foreign_pre_chain,
)

# after
formatter = structlog.stdlib.ProcessorFormatter(
    processor=structlog.dev.ConsoleRenderer(colors=False),
    foreign_pre_chain=foreign_pre_chain,
)
```

Nothing else in `_configure_logging()` changes. The `foreign_pre_chain` still
normalises stdlib records into the same event_dict shape, so both
structlog-originated and foreign records render through the same single path.

#### Resulting stdout shape

```
2026-05-18T10:35:42.789Z [info     ] Scheduler started              logger=main component=main
2026-05-18T10:35:43.012Z [warning  ] TELEGRAM_BOT_TOKEN not set     logger=main component=main
2026-05-18T10:35:43.450Z [info     ] bulk_backfill_complete         logger=main component=main items_ingested=1240 elapsed_s=87.4
```

Format characteristics:
- ISO timestamp (already produced by the existing `TimeStamper(fmt="iso")` processor).
- Bracketed level, padded for column alignment.
- Event message, padded.
- All structured kwargs trailing as `key=value` — `component`, `job_id`,
  `elapsed_s`, etc. survive the swap and stay greppable.
- No ANSI color codes (`colors=False`) — safe for `docker logs`, files, and pipes.

### Behavior after both changes

- `docker logs -f dev-trend-app` shows `app.*` events plus warnings/errors from any
  source, rendered in the format above. The two recurring noise lines disappear.
- If a third-party library logs a real warning/error (e.g. `httpx` connection failure),
  it still surfaces because the threshold is WARNING, not silenced entirely.
- Setting global `LOG_LEVEL=DEBUG` will NOT re-enable the muted loggers — they
  remain pinned at WARNING. To debug one of them temporarily, the muted tuple must
  be edited or that logger's level explicitly raised in code.
- Structured context fields (`component`, `job_id`, `elapsed_s`, …) remain visible
  on each line and can still be grepped (`docker logs -f app | grep job_id=hn_ingestion`).

## Implementation steps

1. ✅ Edit `app/main.py`: add the mute loop in `_configure_logging()` *(done)*.
2. Edit `app/main.py`: swap `structlog.processors.JSONRenderer()` for
   `structlog.dev.ConsoleRenderer(colors=False)` inside the `ProcessorFormatter`.
3. Update `tests/test_logging_config.py::test_foreign_stdlib_log_renders_as_json`:
   rename to `test_foreign_stdlib_log_renders_as_console_text` and replace the
   `json.loads(...)` assertion with substring checks for the level token
   (`[info     ]`), the event (`Scheduler started`), and `logger=apscheduler.scheduler`.
4. Rebuild + redeploy via existing CI/CD pipeline (no infra/config changes needed).
5. **Rotate the Telegram bot token** via @BotFather (the current token has been
   exposed in logs prior to step 1 shipping).
6. Verify on VPS: `docker logs -f dev-trend-app` should
   - no longer show `GET /health` access lines or `HTTP Request: ... getUpdates ...`,
   - show ingestion / pipeline / bot handler events in the new bracketed format,
   - still expose structured kwargs as trailing `k=v` pairs.

## Risks and reversibility

- **Risk**: A real error from one of the muted libraries that only logs at INFO
  would be hidden. Mitigated: WARNING and ERROR still pass through; libraries
  generally use WARNING+ for real problems.
- **Risk**: Log aggregators that parse JSON will need to be reconfigured.
  Mitigated: no aggregator is currently consuming this stream — the only
  consumer today is a human running `docker logs -f` on the VPS. If a future
  aggregator is added, swap `ConsoleRenderer` back for `JSONRenderer` (single-
  line change) or fork the handler.
- **Reversibility**: Trivial. Revert the renderer line; no migrations, no state.

## Out of scope (deferred)

The following were considered and explicitly deferred:

- `LOG_LEVEL_OVERRIDES` env var for per-logger runtime tuning — YAGNI; edit the
  tuple if needed.
- Separate WARN+ rotating file or stderr stream — was the initial framing but
  superseded by the simpler "mute the noise" approach.
- Renderer toggle via env var (e.g. `LOG_FORMAT=json|console`) — YAGNI; the
  stream has one consumer today. Re-evaluate if a structured-log aggregator is
  ever wired up.

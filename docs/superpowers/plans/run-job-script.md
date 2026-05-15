# Plan: Manual job trigger script

## Context

The app schedules ~10 jobs via APScheduler in `app/ingestion/scheduler.py` (ingestion connectors, daily pipeline / scoring / digest, weekly pruning / recluster, playstore ingestion + app discovery, optional iOS RSS). Today there is no way to trigger one of these jobs out-of-schedule without waiting for cron or restarting the app.

The goal is a CLI script under `scripts/` that mirrors the conventions of `scripts/run_backfill.py`, so the user can manually fire any scheduled job — for local smoke-testing and for ops on the production VPS (re-run a failed digest, force pruning, etc.).

## Design decisions (from brainstorming)

- **Scope:** all jobs registered in `build_scheduler()`.
- **CLI:** one job per invocation via `--job <id>`; `argparse choices=` validates the value; a `--list` flag prints job IDs with their trigger.
- **Mechanism:** reuse `build_scheduler()` to wire everything exactly like production, then look up the job by ID and call `job.func()` once — do NOT call `scheduler.start()`. This avoids duplicating job wiring and guarantees parity with prod behavior.
- **Bot:** always `bot=None`. `daily_digest` will return early; `daily_scoring` runs scoring/lifecycle updates but skips `emit_lifecycle_alerts`. No real Telegram messages from this script — safer for manual triggers.
- **Both local + VPS:** support `--db-url`, `--llm-provider`, `--embedding-provider` env overrides like `run_backfill.py`.

## File to create

`scripts/run_job.py` ✅ **DONE**

## Docker / VPS packaging

No Dockerfile change required. The runtime stage already ships the `scripts/` directory:

```
COPY --chown=app:app scripts/ /app/scripts/
```

(`Dockerfile:41`, added in commit `9dda5d8` for `run_backfill.py`.) Once `scripts/run_job.py` lands in the repo and the image is rebuilt by CI, the script is available inside the container at `/app/scripts/run_job.py` and can be executed via `python -m scripts.run_job ...` (the venv's `python` is on `PATH` thanks to `ENV PATH="/app/.venv/bin:$PATH"`).

VPS invocation pattern (run on the host, against the running app container):

```
docker compose exec app python -m scripts.run_job --list
docker compose exec app python -m scripts.run_job --job weekly_pruning
```

This reuses the container's env (`DATABASE_URL`, LLM provider, etc.) so the manual trigger hits the same Postgres / providers as the scheduled job.

## Implementation outline ✅ **DONE**

1. **Argparse** (mirrors `run_backfill._parse_args`):
   - `--job` (required unless `--list`): `choices` = the fixed list of job IDs below.
   - `--list`: flag — print all job IDs with their trigger repr, then exit 0.
   - `--db-url`, `--llm-provider`, `--embedding-provider`: env overrides.
   - `--verbose / -v`: DEBUG logging.

   Job ID choices (hard-coded — keep in sync with `app/ingestion/scheduler.py`):
   `github_ingestion`, `hn_ingestion`, `reddit_ingestion`, `playstore_ingestion`, `playstore_app_discovery`, `ios_rss_ingestion`, `daily_pipeline`, `daily_scoring`, `daily_digest`, `weekly_pruning`, `weekly_recluster`.

2. **Logging:** plain structlog + stdlib config (no Rich Live progress — the underlying jobs already log via structlog). Pattern: copy the structlog `configure(...)` block from `run_backfill._setup_logging`, omit the `_BackfillProgress` processor.

3. **Setup (in this order, like `run_backfill._run`):**
   - Apply env overrides → `get_settings.cache_clear()` → `settings = get_settings()`.
   - `reset_engine()` + `await check_db_reachable()` (from `app/db.py`).
   - Build connectors and registry exactly as `app/main.py:47-59` (httpx client, `ConnectorRunRegistry`, `GithubConnector`, `HNConnector`, `RedditConnector`).
   - Call `build_scheduler(connectors, registry, settings, bot=None)` from `app/ingestion/scheduler.py`.

4. **If `--list`:** iterate `scheduler.get_jobs()`, print `f"{job.id:<28} {job.trigger}"`, exit 0.

5. **Else trigger one job:**
   - `job = scheduler.get_job(args.job)`.
   - If `job is None` (e.g. user asked for `ios_rss_ingestion` but `enable_ios_rss=False`), log a clear error and exit 1.
   - `await job.func()`. APScheduler's `func` attribute is the coroutine function passed to `add_job` — it executes the same closure that runs on cron.
   - Log start and end timestamps + wall-clock seconds; let the job's own structlog events show progress.

6. **Cleanup:** `await client.aclose()` for the httpx client in `finally` (note: `_playstore_ingestion_job`, `_ios_rss_ingestion_job`, and `_scoring_job` each create their own internal client and close it themselves — the outer client is only used by the github/hn/reddit connectors).

7. **`main()`:** `asyncio.run(_run(args))`; non-zero exit on uncaught exception.

## Critical files

- `scripts/run_job.py` — new file.
- `app/ingestion/scheduler.py` — read-only reuse of `build_scheduler()`. No changes.
- `scripts/run_backfill.py` — reference for CLI/logging conventions.
- `app/main.py:47-110` — reference for connector + scheduler wiring.

## Reused utilities

- `app.ingestion.scheduler.build_scheduler` — single source of truth for job wiring.
- `app.ingestion.base.ConnectorRunRegistry` and the three HTTP connectors.
- `app.config.get_settings` (+ `cache_clear` after env overrides).
- `app.db.reset_engine`, `app.db.check_db_reachable`.

## Verification

Run on a local dev DB (ask the user to execute, per CLAUDE.md):

1. List jobs — sanity check the scheduler builds:
   ```
   uv run python -m scripts.run_job --list
   ```
   Expect all 10 job IDs (11 if `ENABLE_IOS_RSS=true`) with their cron/interval triggers.

2. Trigger a cheap, bot-independent job to validate the invocation path:
   ```
   uv run python -m scripts.run_job --job weekly_pruning --llm-provider mock
   ```
   Expect: DB reachability log, "Weekly pruning" runs, exit 0.

3. Trigger a bot-dependent job to confirm bot=None short-circuit:
   ```
   uv run python -m scripts.run_job --job daily_digest --llm-provider mock
   ```
   Expect: job returns early (no Telegram traffic), exit 0.

4. Trigger an ingestion connector against the dev DB:
   ```
   uv run python -m scripts.run_job --job hn_ingestion --llm-provider mock
   ```
   Expect: HN connector runs, registry records a run, exit 0.

5. Invalid job ID:
   ```
   uv run python -m scripts.run_job --job nope
   ```
   Expect: argparse error listing the valid choices, non-zero exit.

6. Disabled optional job (when `ENABLE_IOS_RSS=false`):
   ```
   uv run python -m scripts.run_job --job ios_rss_ingestion
   ```
   Expect: clear "job not registered" error and exit 1.

# Plan — Bulk Backfill on Empty DB at Startup

## Context

DevTrend's ingestion layer today is purely scheduled: GitHub/HN every 6h, Reddit every 12h, App Store daily. On a fresh install with an empty SQLite, it takes a full day before scoring runs and briefs become useful — and percentile normalisation needs ~30 days of `NicheSignal` history before scores differentiate meaningfully.

Goal: when the database is empty at startup, run a one-shot **bulk backfill** that pulls ~30 days of real history from every source, rebuilds per-day `NicheSignal` aggregates from each item's original `created_at`, runs scoring across that historical window, and generates briefs — so `/briefing` returns meaningful, percentile-normalised output immediately after first launch. Subsequent restarts are no-ops.

## Design

### 1. Per-connector historical fetch (`since: datetime`)

Extend each connector's `fetch()` with an optional `since: datetime | None = None`. When set, fetch as far back as `since` (capped per-source by API limits). The default behaviour (None) is unchanged — the existing scheduled jobs keep working as-is.

| Connector | Capability | Backfill strategy |
|---|---|---|
| GitHub (`github_connector.py`) | Already accepts lookback days via `pushed:>{since}` | Pass `since`; paginate `?page=1..N` with `per_page=100` until empty or hard cap (e.g. 1000 items) |
| HN (`hn_connector.py:6-45`) | Algolia supports `numericFilters=created_at_i>{epoch}` | Pass `since`; paginate `&page=` up to Algolia's 1000-item limit; raise `hitsPerPage` to 1000 |
| Reddit (`reddit_connector.py:8-50`) | `/r/{sub}/new.json` supports `?after=` cursor | Per-sub paginate `?after=` up to ~1000 items or until oldest item is older than `since`. Reddit's hard ~1000-item ceiling per sub is the natural floor — log when reached |
| App Store mock (`appstore_mock_connector.py:9-42`) | Static JSON | Just load all mock files; ignore `since` |

Idempotency relies on the existing `(source_type, external_id)` uniqueness — re-running the backfill is safe.

### 2. New module: `app/ingestion/backfill.py`

```python
async def bulk_backfill(
    connectors: list[BaseConnector],
    history_days: int = 30,
) -> BackfillReport
```

Steps:
1. Compute `since = utcnow() - timedelta(days=history_days)`
2. For each connector, call `await connector.run(since=since)` sequentially (avoid rate-limit pile-ups). Wrap in `asyncio.wait_for()` using the existing `ingestion_job_timeout_s` × N multiplier.
3. After all fetches complete, call `rebuild_historical_signals(history_days)` (see §3).
4. Call existing `score_all_niches()` once across the historical window — but iterate day-by-day so `NicheScoreHistory` gets one row per day per niche, not just today.
5. Call existing `run_brief_for_niche()` once per niche.
6. Return a structured `BackfillReport` (per-source counts, signal rows written, scores written, briefs generated, duration) and emit a single structured log line.

### 3. Historical NicheSignal rebuild

Critical design point: today's scoring assumes `NicheSignal.metric_timestamp` is "now". For percentile normalisation to be meaningful from day one, the bulk path must **bin SourceItems by their `created_at` date** and write one `NicheSignal` row per `(niche_id, source_type, metric_name, day)`.

New helper in `app/features/niche_builder.py` (or a sibling file under `app/features/`):

```python
def rebuild_historical_signals(history_days: int) -> int:
    """For each day in the window, aggregate SourceItem.created_at counts
    per (niche_id, source_type) and upsert NicheSignal rows."""
```

Reuses the existing keyword-match niche attachment (already done at ingestion via `niche_id` on `SourceItem`).

### 4. Startup hook

In `app/main.py` lifespan, **after** `sync_niches_from_yaml()` and connector instantiation, **before** `scheduler.start()`:

```python
if settings.backfill_on_empty and await _db_is_empty():
    report = await bulk_backfill(connectors, history_days=settings.backfill_history_days)
    logger.info("bulk_backfill_complete", **report.to_dict())
```

`_db_is_empty()` = `SELECT 1 FROM source_item LIMIT 1` returns nothing.

### 5. Config additions (`app/config.py`)

```env
BACKFILL_ON_EMPTY=true            # default true; safety opt-out for tests/CI
BACKFILL_HISTORY_DAYS=30
BACKFILL_MAX_ITEMS_PER_SOURCE=1000
```

### 6. CLI parity (small extension)

Extend `scripts/run_ingestion.py` with `--backfill-days N` so the same path is callable manually for dev/recovery without restarting the app. Reuses `bulk_backfill()`.

## Files to Modify

| File | Change |
|---|---|
| `app/ingestion/base.py` | Add optional `since: datetime \| None = None` to `fetch()` / `run()` signatures |
| `app/ingestion/github_connector.py` | Honour `since` for `pushed:>{since}`; paginate via `page` param |
| `app/ingestion/hn_connector.py` | Replace hardcoded 6h lookback with `since`; paginate via `page` |
| `app/ingestion/reddit_connector.py` | Per-sub `after`-cursor pagination until `since` reached or 1000-item ceiling |
| `app/ingestion/appstore_mock_connector.py` | Accept `since` param (no-op for mock) |
| `app/ingestion/backfill.py` | **NEW** — orchestrates bulk fetch + historical signal rebuild + scoring + briefs |
| `app/features/niche_builder.py` (or new `historical_signals.py`) | **NEW helper** — `rebuild_historical_signals(days)` bins SourceItems by `created_at` into per-day NicheSignal rows |
| `app/forecasting/scoring.py` | Add `score_all_niches_for_date(d: date)` so the backfill can populate `NicheScoreHistory` day-by-day across the window (today's `score_all_niches()` is "today only") |
| `app/main.py` (lifespan ~line 49+) | Call `bulk_backfill()` if `settings.backfill_on_empty` and DB is empty, before `scheduler.start()` (line 86) |
| `app/config.py` | Add `backfill_on_empty`, `backfill_history_days`, `backfill_max_items_per_source` settings |
| `.env.example` | Document the three new env vars |
| `scripts/run_ingestion.py` | Add `--backfill-days` flag that calls `bulk_backfill()` |
| `tests/test_connectors.py` (later) | Tests for `since`-aware fetch + pagination per connector |

## Reused Existing Code

- `BaseConnector._request_with_retry()` (`base.py:71-167`) — keep all retry/backoff logic
- `score_all_niches()` (`app/forecasting/scoring.py`) — extract per-date variant
- `run_brief_for_niche()` — call once per niche after scoring
- `rolling_slope()` / `percentile_rank()` (`trend_features.py:4-36`) — already degrade gracefully with sparse history; no change needed
- Existing keyword-match `niche_id` attachment on `SourceItem` ingestion — historical rebuild reads this directly

## Reddit Caveat (must surface)

Reddit's `/r/{sub}/new.json` only exposes ~1000 most recent posts per subreddit. For low-volume subs (`r/SideProject`, `r/iOSProgramming`) 30 days fits comfortably; for `r/startups` we may only get ~7-10 days back. The backfill report will log `oldest_item_age_days` per sub so the gap is visible. Acceptable for Phase 1; documenting it is enough.

## Verification

1. **Unit-level**
   - `pytest tests/test_connectors.py -k since` — each connector honours `since` and paginates
   - `pytest tests/test_scoring.py -k historical` — `rebuild_historical_signals` produces N×days rows

2. **End-to-end smoke (manual)**
   ```bash
   rm devtrend.db
   BACKFILL_ON_EMPTY=true BACKFILL_HISTORY_DAYS=30 uvicorn app.main:app
   ```
   Then in Telegram:
   - `/sources` → all four sources show recent ingestion timestamp
   - `/niches` → niches show non-zero, differentiated scores (not all 50.0)
   - `/briefing` → top 3 briefs present immediately

3. **Idempotency check**
   - Restart the app — startup logs should show `db_not_empty_skip_backfill`; no duplicate `SourceItem` rows (`SELECT source_type, external_id, COUNT(*) FROM source_item GROUP BY 1,2 HAVING COUNT(*) > 1` returns empty)

4. **Inspect the report**
   - Structured log line `bulk_backfill_complete` shows per-source counts, signal rows, briefs generated, total duration

5. **Scheduler still healthy**
   - After backfill, the scheduled GitHub/HN/Reddit/App Store jobs continue firing on their normal cadence — confirmed by `/sources` after 6h.

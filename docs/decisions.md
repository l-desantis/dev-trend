# Architecture Decision Records

> ADRs are written per decision as they happen.
> Format: context → decision → consequences.

---

## ADR-001 — Product name: DevTrend

**Date:** 2026-04-23
**Status:** Accepted

**Context:** The original repo was scaffolded under the name "devradar". The v2.0 project document renamed the product to "DevTrend" in prose but left "devradar" in the repo directory, database filename, and change log, creating a naming drift that would propagate into log output, env vars, and documentation.

**Decision:** Standardise on **DevTrend** everywhere. Repo application directory is `devtrend/`, database file is `devtrend.db`, and all references to "devradar" are scrubbed.

**Consequences:** Consistent naming in logs, config, and docs from day one. Any future rename costs a single find-replace.

---

## ADR-002 — Event-loop model: single asyncio loop

**Date:** 2026-04-23
**Status:** Accepted

**Context:** Phase 1 runs three async subsystems in one process: python-telegram-bot v20+ (async), APScheduler, and FastAPI. A common failure mode is mixing sync and async schedulers, leading to blocked event loops, missed jobs, or deadlocks under load.

**Decision:** Use a **single asyncio event loop** for the entire process:
- `AsyncIOScheduler` from APScheduler (not `BackgroundScheduler`)
- python-telegram-bot in its native async polling mode
- FastAPI via `uvicorn` with the same loop

Scheduler jobs are async coroutines. No `run_coroutine_threadsafe`, no threads.

**Consequences:** Startup and shutdown logic must be coordinated (FastAPI lifespan hook starts/stops the scheduler). All scheduled jobs must be non-blocking — CPU-heavy work must be wrapped in `asyncio.to_thread` if needed. In return: no thread-safety concerns, no hidden deadlocks.

---

## ADR-003 — Forecasting: rolling 7-day slope instead of Prophet

**Date:** 2026-04-23
**Status:** Accepted

**Context:** The original plan specified Facebook Prophet for the Growth dimension. Prophet requires weeks to months of periodic observations to produce meaningful forecasts. The system starts from zero history; seeding Prophet with synthetic data produces decorative, not predictive, output.

**Decision:** Phase 1 uses a **7-day rolling linear regression slope** on per-niche signal counts as the Growth dimension input. This works from day one, is deterministic, and is interpretable without statistical background.

Prophet is deferred to **Phase 1.5**, triggered when ≥30 days of real NicheSignal history is available.

**Consequences:** Growth scores in the first weeks reflect only recent velocity, not long-run trend projection. This is disclosed in the evaluation plan. Porting to Prophet later requires only changing the `forecasting/scoring.py` growth computation; the rest of the pipeline is unaffected.

---

## ADR-004 — Scoring design: three dimensions, percentile rank, weights 0.41/0.35/0.24

**Date:** 2026-04-23
**Status:** Accepted

**Context:** The original scoring formula used four dimensions (Growth 0.35, Demand 0.30, Novelty 0.20, Competition 0.15). The Competition dimension relies on app-store saturation data, which is mocked in Phase 1. Scoring 15% of the total on synthetic data risks self-deception about niche competitiveness.

Normalisation was described as "0–100" without specifying the method, which is where most scoring systems produce inconsistent rankings.

**Decision:**

**Dimensions (Phase 1):**
- Growth: 0.41 — 7-day rolling slope on signal counts
- Demand: 0.35 — mention count, GitHub star delta, install proxy
- Novelty: 0.24 — `1 − (age_of_newest_signal_days / 30)`, clamped [0, 1]

Competition is dropped from Phase 1 and reintroduced in Phase 1.5 when a real app-store provider is integrated. The 0.15 weight is redistributed proportionally across the three remaining dimensions.

**Normalisation:** Percentile rank over a rolling 30-day window per niche. A niche at the 90th percentile on Growth receives a Growth score of 90. Stable across day-to-day outliers; interpretable without statistics knowledge.

**Spike alert:** fires once daily immediately after the niche scoring job. Compares today's `score_total` against the last persisted row in `NicheScoreHistory`. Threshold: +15 points (configurable).

**Consequences:** Phase 1 competition signal is absent from scores. This is explicitly disclosed in the code and evaluation plan. Re-adding Competition requires changing the weights and updating `config.py` — no schema changes needed.

**Implementation notes (M3):** Niches with fewer than 2 days of score history in the 30-day window receive a neutral percentile rank of 50.0 for each dimension. This prevents new niches from being unfairly ranked at 0 before history accrues, and avoids artificially inflating them to 100. The fallback is applied inside `percentile_rank()` in `app/features/trend_features.py` and is covered by `test_percentile_rank_insufficient_history_returns_neutral_50`.

---

## ADR-005 — Agent graph design (LangGraph)

**Status:** Accepted (2026-04-27)

**Context.** M4 needed a transparent, testable orchestration layer that turns
the daily scoring output into an `OpportunityBrief`. The project committed to
LangGraph from day one (project doc §4) and a single asyncio loop (ADR-002).

**Decision.**

1. **Linear graph, no conditional edges.** `fetcher → retriever → forecaster
   → reporter → reviewer → END`. Conditional/branching edges are deferred to
   Phase 2.
2. **Forecaster reads, doesn't recompute.** Reads the latest
   `NicheScoreHistory` row for the day. Computes via `score_niche()` only as
   a cold-start fallback. This avoids duplicating M3 work inside the agent
   and keeps the graph cheap to invoke on demand.
3. **Headline programmatic, summary LLM.** The headline is `f"{name} —
   Score {round(score)}"`; only the prose summary is generated by qwen2.5.
   This sidesteps JSON-schema fragility (Risk Register §16).
4. **Reviewer is heuristic, never retries.** Checks summary length,
   placeholder markers, and evidence count. Sets `has_issues` and logs gaps.
   Retry/repair logic is deferred to Phase 2.
5. **LLM adapter injected at build time.** `build_graph(adapter)` binds the
   adapter via closure. Tests pass `MockLLMAdapter`; the scheduler picks
   `OllamaAdapter` or `MockLLMAdapter` based on `settings.llm_provider`. No
   global singleton.
6. **Persistence in the orchestrator.** `run_brief_for_niche()` invokes the
   compiled graph and writes `OpportunityBrief` (delete-then-insert per
   `(niche_id, day(UTC))`). Nodes stay pure with respect to the brief table.
7. **Per-niche timeout 90s.** Reporter wraps `adapter.generate_brief()` in
   `asyncio.wait_for(timeout=settings.brief_per_niche_timeout_s)`. Timeout
   produces an empty-summary brief that the reviewer marks `has_issues`.
   APScheduler `max_instances=1` prevents overlapping daily runs.

**Consequences.**

- The agent is fully testable with `MockLLMAdapter`; CI never needs Ollama.
- The reviewer can't repair briefs — the `has_issues` flag is observable,
  and Phase 2 can add a self-correction loop without changing the schema.
- Brief generation is bounded: 12 niches × 90s ≤ 18 min wall-clock.
- Adding a new dimension (e.g. competition in Phase 1.5) requires no graph
  changes — only `app/forecasting/scoring.py` and the prompt template.

---

## ADR-006: Daily digest delivery & spike-alert chaining

**Status:** Accepted (2026-04-27)

**Context.** M5 introduces two scheduled push flows: a daily digest at
08:00 UTC, and a spike alert that must fire only when today's score
truly differs from yesterday's persisted score. We considered (a) two
independent crons (scoring, then a separate spike-alert cron 15 min
later) and (b) chaining the alert inside `_scoring_job`.

**Decision.**
- Both digests and spike alerts target every chat in
  `telegram_allowed_chat_ids`.
- The spike alert is awaited inside `_scoring_job` immediately after
  `score_all_niches`, before that coroutine returns.
- The daily digest runs as its own cron (`digest_cron_hour/minute`).
- Per-chat send failures are logged and skipped; one bad chat must
  not abort the whole job.
- The bot reference is passed into `build_scheduler` so jobs can
  dispatch without a global.

**Consequences.**
- Spike alerts cannot race scoring — the same coroutine that wrote
  `NicheScoreHistory` reads it.
- If the bot is unconfigured, jobs no-op cleanly (log a WARNING).
- Future per-user subscriptions (Phase 2) replace the allowlist
  fan-out without touching the scheduler logic.

---

## ADR-007 — Bulk backfill on empty DB at startup

**Date:** 2026-04-28
**Status:** Accepted

**Context:** Percentile-rank normalisation (30-day window) produces undifferentiated scores (all ~50) when the DB contains fewer than ~7 days of NicheSignal history. On a fresh install, the scheduler alone would take a full day before the first scoring run — and ~30 days before scores became meaningful.

**Decision:** On startup, after niches are synced and connectors are instantiated, check `SELECT id FROM source_items LIMIT 1`. If the table is empty and `BACKFILL_ON_EMPTY=true`:

1. Run each connector sequentially with `connector.run(since=now−history_days)`. Sequential (not parallel) to avoid rate-limit pile-ups; each connector honours the existing `_request_with_retry` backoff.
2. Bin the ingested `SourceItem` rows by their original `created_at` date into per-day `NicheSignal` rows via `rebuild_historical_signals(history_days)`. This produces the same signal structure the daily `aggregate_daily_signals` job produces, but across the full historical window.
3. Score all niches day-by-day in chronological order (oldest first) via the existing `score_all_niches(as_of)` so each day's percentile rank can draw on prior days' history as it accumulates.
4. Generate `OpportunityBrief` rows for each niche via the existing agent graph.

**Trigger condition:** `BACKFILL_ON_EMPTY=true` (default) AND `source_items` table is empty. Subsequent restarts find a non-empty table and skip (`db_not_empty_skip_backfill` log line).

**History depth per source:**

| Source | Strategy | Practical depth |
|---|---|---|
| GitHub | `pushed:>{since}` + page=1..N | Full 30 days (cap: `BACKFILL_MAX_ITEMS_PER_SOURCE`) |
| HN | Algolia `numericFilters=created_at_i>{epoch}` + page=0..N | Full 30 days (Algolia 1000-item ceiling) |
| Reddit | `/r/{sub}/new.json?after=` cursor per sub | Up to ~1000 items per sub; busy subs (`r/startups`) may reach only ~7-10 days — logged as `oldest_item_age_days` |
| App Store mock | Static JSON, `since` ignored | All mock data loaded |

**Idempotency:** relies on the existing `(source_type, external_id)` unique constraint on `SourceItem` — re-running the backfill inserts zero duplicates. `rebuild_historical_signals` deletes then re-inserts NicheSignal rows for each touched (niche, day) pair.

**Consequences:**
- `/briefing` returns meaningful, percentile-normalised briefs immediately after first launch.
- The same `bulk_backfill()` function is exposed via `scripts/run_ingestion.py --backfill-days N` for dev/recovery without restarting the app.
- Reddit's 1000-post-per-sub ceiling means partial coverage for high-volume subs; this is documented and logged, not silently ignored.
- The backfill is synchronous in the lifespan (blocking startup) — acceptable because it runs only once on an empty DB.

---

## ADR-008 — Data retention: 90-day source items / 30-day non-aggregate signals / weekly Sunday 03:00 UTC

**Date:** 2026-04-28
**Status:** Accepted

**Context:** Without a retention policy the SQLite DB grows unbounded. `SourceItem` rows are the largest contributor (one row per ingested post/repo/app). `NicheSignal` rows produced by the daily aggregator are smaller but accumulate at roughly `(niches × sources × 2 metrics) / day`. Score history (`NicheScoreHistory`) and briefs (`OpportunityBrief`) must be retained forever — they are the primary input for percentile-rank normalisation and the trend-direction labelling shown to users.

**Decision:**

- **SourceItem** rows where `created_at < now − 90d` are deleted weekly. Items without a `created_at` (NULL) are not matched and remain.
- **NicheSignal** rows where `metric_timestamp < now − 30d` **and** `metric_name NOT IN` the daily-aggregate keep-list are deleted weekly. The keep-list (`mention_count`, `github_stars_total`, `hn_points_total`, `reddit_ups_total`, `appstore_install_proxy`) covers every metric_name the current aggregator writes, so all existing signals survive. Future non-aggregate signal types (e.g. per-event raw signals) would be pruned after 30 days.
- **NicheScoreHistory**, **OpportunityBrief**, **Niche** are never pruned.
- A new one-row `MaintenanceState` ORM model persists `last_pruned_at`. If a scoring job starts and `last_pruned_at` is NULL or older than 10 days, a `pruning_stale` warning is logged.
- The pruning job runs every Sunday at 03:00 UTC (configurable via `PRUNING_CRON_HOUR`). This is 1 hour after the brief generation job and avoids the peak ingestion window.

**Consequences:**
- DB growth is bounded. A system ingesting from all four sources for 10 niches generates ~400 SourceItems/day; after 90 days steady-state that is ~36 000 rows, well within SQLite limits.
- Daily aggregate signals accumulate indefinitely. This is intentional: percentile rank needs the full history of normalised Growth/Demand/Novelty raws, not just the last 30 days.
- The 10-day stale alert catches missed Sunday jobs (e.g. scheduler downtime) without flooding logs on every scoring run.

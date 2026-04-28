# Plan — Milestone 6: Hardening and Evaluation

## Context

M5.5 (bulk backfill on empty DB) shipped, so a fresh install now produces meaningful percentile-normalised briefs from day one. That closes the last functional gap in Phase 1's pipeline. Milestone 6 is **not feature work** — it is the hardening lap that takes Phase 1 to "Definition of Done" (§18 of `devtrend-project-document.md`):

- The DB grows unbounded today — there is no retention. A weekly pruning job is missing.
- The test suite is reasonably broad (≈2.4k lines across 16 files) but coverage gaps exist on three of the four KANBAN test items, particularly percentile normalisation, the `has_issues` reviewer behaviour, and a couple of bot commands.
- There is no replay harness. Today, the only way to exercise the scoring pipeline against historical data is to wait for real ingestion or hand-write fixtures per test. M6-06 is meant to give us a single CLI to seed synthetic 60-day history and replay the daily-scoring loop deterministically.
- `docs/evaluation-plan.md` and `scripts/run_forecasts.py` are placeholder/empty files. README is 59 lines — a one-pager with run commands but no architecture/test/limitations sections.

Outcome: at the end of M6, weekly pruning runs on schedule, all four KANBAN test categories are demonstrably solid, a replay harness exists for evaluation work, and the docs/README reflect Phase 1 reality.

## Design

### M6-01 — Weekly pruning job

**Approach:** add the pruning logic as a new module and register it as a single APScheduler job alongside the existing daily jobs in `app/ingestion/scheduler.py`.

- New module: **`app/maintenance/pruning.py`**
  - `async def prune_old_data(now: datetime) -> PruneReport`
  - Deletes `SourceItem` rows where `created_at < now − source_retention_days` (default 90).
  - Deletes `NicheSignal` rows where `metric_timestamp < now − signal_retention_days` (default 30) **and** `metadata_json` does NOT mark the row as a daily aggregate (project doc: "keep daily aggregates"). The aggregator already writes a discriminating `metric_name` (verify in `signal_aggregator.py`); use it as the keep-list rather than a JSON probe.
  - `OpportunityBrief`, `NicheScoreHistory`, `Niche` are NOT pruned (history must persist for percentile rank and trend-direction labelling).
  - Returns `PruneReport(source_items_deleted, signals_deleted, duration_ms, ran_at)`; emits one structured log line `pruning_complete`.
- **Stale-pruning warning:** persist `last_pruned_at` in a new one-row `MaintenanceState` ORM model (kept separate from `ConnectorRunRegistry` so concerns don't conflate; easy to extend with future maintenance fields). On scoring job startup, log a warning if `now − last_pruned_at > 10d` (project doc §16 "alert in logs if last pruning > 10 days stale").
- **Scheduler wiring** (`app/ingestion/scheduler.py`):
  ```python
  scheduler.add_job(
      _pruning_job,
      CronTrigger(day_of_week="sun", hour=settings.pruning_cron_hour, minute=0),
      id="weekly_pruning",
      max_instances=1, coalesce=True, misfire_grace_time=3600,
  )
  ```
  Default `pruning_cron_hour=3` (Sunday 03:00 UTC, matches §12 of the project doc).
- **Config additions** (`app/config.py` + `.env.example`):
  - `SOURCE_RETENTION_DAYS=90`
  - `SIGNAL_RETENTION_DAYS=30`
  - `PRUNING_CRON_HOUR=3`
- **Why a one-shot module rather than scattering deletes:** keeps the pruning policy in one auditable file and makes the unit test a single import.

### M6-02 — Tests: connectors

Coverage today (`tests/test_connectors.py`, 546 lines) already hits the four connectors plus the M5.5 weekly-window helper. Audit and close gaps:

- Confirm each connector has at least: (a) happy-path normalisation, (b) `since=`-aware fetch, (c) pagination/cursor termination, (d) duplicate-skip via `(source_type, external_id)`.
- Add: GitHub 403 / rate-limit handling via `_request_with_retry` (current tests likely cover 200; verify a 4xx path).
- Add: Reddit `oldest_item_age_days` log line on the 1000-item ceiling — this is documented behaviour in §16 of the project doc and should have a test.
- Add: AppStore mock connector loads all `data/mock/*.json` and is a no-op when `since` is set.

### M6-03 — Tests: scoring

Coverage today (`tests/test_scoring.py`, 115 lines, 6 tests) covers history-row persistence, composite weights, same-day idempotency, novelty edge cases, count return. Gaps to close:

- **Percentile rank over rolling 30-day window** — there is no direct unit test today. Add: seed 30 days of `NicheSignal` rows for two niches (one trending up, one flat); assert that the trending niche's percentile-normalised Growth ≥ 80 and the flat niche's ≤ 30. Driven through `score_all_niches(as_of)` so the full normalisation path is exercised.
- **Sparse history degrades gracefully** — only 3 days of data → percentile-rank still returns a defined number (no division-by-zero, no crash). The project doc flags "sparse rolling-slope window" as a Medium risk (§16).
- **Spike-alert delta** — assert the comparison logic against `NicheScoreHistory` returns the right boolean at the threshold edge (Δ = threshold ± 1). This is shared logic between scoring and the bot push hook; test it in isolation.

### M6-04 — Tests: agent graph

Coverage today is the strongest of the four (`test_agent_graph.py` + `_e2e.py` + `test_agent_nodes.py` ≈ 466 lines). Audit + add:

- **Reviewer `has_issues = True` path** — feed a deliberately incomplete `brief` dict (e.g. empty headline) through `reviewer_node` and assert the flag is set and a structured log line is emitted. Project doc §11/§17 calls this out specifically.
- **90s reporter timeout** — replace the `MockLLMAdapter` with one whose `generate_brief` sleeps 95s; assert `asyncio.wait_for` raises and the node returns an empty brief without crashing the graph. Use `monkeypatch` on the `wait_for` timeout to avoid actually waiting 90s in CI (e.g. monkeypatch the value to 0.1s with a 0.2s mock sleep).
- **Triggered-by propagation** — confirm `OpportunityState["triggered_by"]` is preserved end-to-end into the persisted `OpportunityBrief.metadata_json` (used in §14 logging traceability).

### M6-05 — Tests: bot handlers

Coverage today (`tests/test_bot_handlers.py`, 311 lines) covers the seven slash commands via `MagicMock`/`AsyncMock`. Audit + add:

- **Allowlist middleware** — explicit test that an unknown chat ID receives the polite rejection message and does NOT invoke any downstream handler. This is a security-relevant path (§7.1); should not rely on implicit coverage.
- **MarkdownV2 truncation** — feed `/niche` a niche with a 5000-char synthetic brief; assert output ≤ 4096 chars and ends with the `…see brief` footer. Uses the existing `formatter.py` helper.
- **`/sources` reflects `ConnectorRunRegistry`** — assert the command output includes the last-run timestamp from a seeded registry row.

### M6-06 — Mock historical replay harness

**Approach:** a new CLI script that seeds synthetic `NicheSignal` rows across a configurable date range and re-runs the daily-scoring loop day-by-day. This is purely an evaluation tool — does not touch production code paths beyond calling `score_all_niches(as_of)`.

- New script: **`scripts/run_replay.py`**
  - `--days N` (default 60) — synthetic history depth.
  - `--profile {flat,rising,spiky}` — three baked-in trend shapes per niche, useful for eyeballing percentile rank and slope behaviour.
  - `--niches s1,s2,...` (default: all niches in `data/niches.yaml`).
  - Flow: `init_db()` → wipe `NicheSignal` and `NicheScoreHistory` (with confirmation prompt unless `--yes`) → seed N×days rows per niche per source per profile → loop `for d in window: await score_all_niches(as_of=d)` → print a summary table (per-niche min/max/end score and slope direction).
- Reuses: `score_all_niches()` (`app/forecasting/scoring.py:170`), the existing daily aggregator path, `sync_niches_from_yaml()`.
- **Safety:** the wipe is gated behind `--yes` and the script aborts if `DATABASE_URL` does not contain `replay` or `:memory:` as a substring — protects the dev DB from accidental wipe. (User can override with `--force`, but the default is conservative.)
- **Why a script and not a fixture:** the project doc §15 explicitly requests "a replay harness using mock historical windows is built in Milestone 6" — it is meant for ad-hoc evaluation runs, not test fixtures. Test fixtures already get what they need from M6-03.

### M6-07 — `docs/evaluation-plan.md`

`docs/evaluation-plan.md` is empty today. Fill it as a manual-review checklist matching §15 of the project doc:

- One section per evaluation criterion (Evidence fidelity / Score interpretability / Signal freshness / Slope usefulness / Notification quality / Spike alert accuracy).
- Each section: a short prompt + concrete checks (e.g. for Evidence fidelity: "open the latest brief in `/niche <slug>`; for each evidence item, confirm the URL resolves and the excerpt appears in the linked source").
- Cadence guidance: weekly manual review for the first month, then monthly.
- Append a "How to use the replay harness" subsection pointing at `scripts/run_replay.py`.

### M6-08 — README complete

Current README is 59 lines covering data sources + manual ingestion. Expand to a real Phase 1 README:

1. **What it is** (one paragraph + the daily-brief example from §1).
2. **Quick start**: env vars, `uv run` commands for migration / bot / scheduler (placeholder text the user fills, since CLAUDE.md forbids running `uv` from this shell).
3. **Architecture** — embed the ASCII diagram from §5 of the project doc.
4. **Bot commands** — table from §7.1.
5. **Configuration** — link to `.env.example`.
6. **Running tests** — `uv run pytest` (user-runnable).
7. **Replay harness** — pointer to `scripts/run_replay.py`.
8. **Known limitations** — Reddit 1000-post ceiling, App Store mock, Prophet/competition deferred, single-tenant.
9. **Reddit User-Agent** instructions (already partially present; keep + tighten).
10. **Links** — project doc, decisions, roadmap, evaluation plan.

## Files to Modify

| File | Change |
|---|---|
| `app/maintenance/pruning.py` | **NEW** — `prune_old_data()` + `PruneReport` |
| `app/models.py` | Add `MaintenanceState` ORM model (one-row table; `last_pruned_at` column; extensible for future maintenance fields) |
| `app/ingestion/scheduler.py` | Register `weekly_pruning` job; warn when `last_pruned_at` > 10d stale |
| `app/config.py` | Add `source_retention_days`, `signal_retention_days`, `pruning_cron_hour` settings |
| `.env.example` | Document the three new env vars |
| `tests/test_connectors.py` | Gap-fill: GitHub 4xx path, Reddit 1000-item ceiling log, AppStore `since=` no-op |
| `tests/test_scoring.py` | Gap-fill: percentile-rank trending vs flat, sparse-history graceful, spike-alert threshold edge |
| `tests/test_agent_graph.py` (or `_nodes.py`) | Gap-fill: reviewer `has_issues=True` path, reporter 90s timeout, `triggered_by` propagation |
| `tests/test_bot_handlers.py` | Gap-fill: allowlist rejection, MarkdownV2 truncation, `/sources` registry rendering |
| `tests/test_pruning.py` | **NEW** — covers retention boundaries, daily-aggregate keep-list, idempotency |
| `scripts/run_replay.py` | **NEW** — seeded historical replay CLI |
| `scripts/run_forecasts.py` | **DELETE** — empty placeholder; Phase 1.5 will introduce a Prophet entry-point under a clearer name |
| `docs/evaluation-plan.md` | Fill from empty per §15 of the project doc |
| `README.md` | Expand to ~150 lines per the section list above |
| `docs/decisions.md` | Add **ADR-008** (pruning policy: 90d source / 30d signal / weekly Sunday 03:00 UTC, daily aggregates retained) |
| `KANBAN.md` | Mark M6 rows as the work progresses (not part of the plan; for the executor's bookkeeping) |

## Reused Existing Code

- `score_all_niches(as_of)` (`app/forecasting/scoring.py:170`) — the replay harness drives this directly per-day.
- `MockLLMAdapter` (`app/llm/mock_adapter.py`) — already used across agent-graph tests; reused for the M6-04 timeout test (with a sleep wrapper).
- `ConnectorRunRegistry` pattern — informs the shape of the new `MaintenanceState` model (one-row, structlog-emitting).
- `formatter.py` truncation helper — reused in the M6-05 truncation test rather than re-implementing.
- `sync_niches_from_yaml()` — replay harness boots niches the same way `app/main.py` does.

## Sequencing

The eight tasks have light dependencies. Recommended order (ship incrementally):

1. **M6-01** pruning job + ADR-008 + `tests/test_pruning.py` — landlocked, no other deps.
2. **M6-03** scoring gap-fill — informs M6-06 (replay harness asserts on the same code paths).
3. **M6-06** replay harness — depends on M6-03 understanding.
4. **M6-02 / M6-04 / M6-05** test gap-fills — independent; can land in parallel.
5. **M6-07** evaluation plan — references M6-06 harness.
6. **M6-08** README — last, so it can describe everything that landed.

Each can be a separate small commit; M6 doesn't need to be one PR.

## Verification

1. **Pruning** — seed `SourceItem` rows at 91d-old and 89d-old; run `prune_old_data(now)`; assert only the 91d-old row is gone. Same shape test for `NicheSignal` 31d/29d. Idempotency: run twice, second pass returns zero deletions. Ask the user to run `! uv run pytest tests/test_pruning.py` and paste output.
2. **Scheduler integration** — boot the app, inspect logs for `Scheduler built` line listing `weekly_pruning` among the registered jobs.
3. **Stale-pruning warning** — manually set `last_pruned_at` to 11 days ago in dev DB; restart; confirm the warning log line fires on the next scoring job.
4. **Test gap-fills** — full `uv run pytest -q` is green; the targeted lines from M6-02 through M6-05 are exercised by the new tests.
5. **Replay harness** — `python scripts/run_replay.py --days 60 --profile rising --niches ai-habit-trackers --yes` (against a `:memory:` or `replay`-suffixed DB) prints a per-day summary table with monotonically increasing scores for the rising profile and ≈flat scores for the flat profile.
6. **Evaluation plan** — open `docs/evaluation-plan.md`; checklist runs cleanly against a live local install.
7. **README** — a fresh reader can clone, follow the quick start, and have the bot answering `/start` within 10 minutes.
8. **Definition of Done (§18 of project doc)** — walk the checklist; every box should be tickable after M6 lands.

## Decisions (confirmed with user)

- **Pruning state**: new `MaintenanceState` ORM model (one-row).
- **`scripts/run_forecasts.py`**: deleted.
- **Test scope (M6-02 through M6-05)**: only the specific gaps listed in this plan — no broader audit, no coverage-tool introduction.

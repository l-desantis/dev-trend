# DevTrend — Kanban Board

> **Columns:** `Backlog` · `To Do` · `In Progress` · `Done`
> **Format per task:** ID · Title · Description · Status · Depends On
> Update `Status` as work progresses.

---

## Pre-Coding

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| PRE-01 | Delete old project doc | Remove `devradar-project-document.md` from repo | To Do | — |
| PRE-02 | Resolve open points | Confirm Reddit UA string, daily-digest timezone (UTC), review `data/niches.yaml` entries | To Do | — |

---

## Milestone 1 — Foundation

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| M1-01 | `requirements.txt` | Pin all Phase 1 dependencies: fastapi, uvicorn, python-telegram-bot, sqlalchemy, apscheduler, langgraph, httpx, pydantic, structlog | To Do | PRE-02 |
| M1-02 | `config.py` | Pydantic Settings class loading all env vars from `.env`; expose typed settings singleton | To Do | M1-01 |
| M1-03 | `db.py` + `models.py` | SQLAlchemy engine, session factory, and ORM models: SourceItem, Niche, NicheSignal, NicheScoreHistory, OpportunityBrief | To Do | M1-02 |
| M1-04 | Extend `.gitignore` | Verify `*.db`, `data/raw/`, `data/processed/` are covered | Done | — |
| M1-05 | FastAPI health endpoint | `GET /health` returns `{status: ok, version}`. Uvicorn starts on single asyncio loop | To Do | M1-02 |
| M1-06 | Telegram bot skeleton | Bot init with `AsyncIOScheduler` wired. `/start` and `/help` commands respond | To Do | M1-02 |
| M1-07 | Allowlist middleware | Middleware checks incoming `chat_id` against `TELEGRAM_ALLOWED_CHAT_IDS`; rejects unknown chats politely | To Do | M1-06 |
| M1-08 | `MockLLMAdapter` stub | Implements `LLMAdapter` interface; returns deterministic fixture brief for any input. Used in all tests | To Do | M1-03 |
| M1-09 | Structured logging | Configure `structlog` for JSON output on every agent run, job, and push event | To Do | M1-02 |
| M1-10 | ADR-001 + ADR-002 | Write name (DevTrend) and event-loop (single asyncio) ADRs in `docs/decisions.md` | Done | — |

---

## Milestone 2 — Ingestion Layer

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| M2-01 | Connector base class | Abstract `BaseConnector` with `fetch()`, `normalize()`, `save()`, `run()`. Enforces `SourceItem` output shape | To Do | M1-03 |
| M2-02 | GitHub connector | Fetch trending repos via GitHub REST API. Collect name, description, language, tags, star delta, dates | To Do | M2-01 |
| M2-03 | HN connector | Fetch top stories via Algolia HN Search API. Collect title, URL, points, comment count, date | To Do | M2-01 |
| M2-04 | Reddit connector | Fetch new posts from target subreddits via public JSON endpoint. Set `REDDIT_USER_AGENT` header | To Do | M2-01 |
| M2-05 | App Store mock connector | Load structured JSON datasets from `data/mock/`. Same interface as real connectors | To Do | M2-01 |
| M2-06 | Mock app-store seed data | Create realistic JSON files under `data/mock/` for at least 6 app categories | To Do | — |
| M2-07 | Niche attachment logic | At ingestion, match each `SourceItem` title + body against `data/niches.yaml` keywords; assign `niche_id` | To Do | M2-01 |
| M2-08 | `AsyncIOScheduler` wiring | Register ingestion jobs: GitHub every 6h, HN every 6h, Reddit every 12h, App Store daily | To Do | M2-02, M2-03, M2-04, M2-05 |
| M2-09 | `/sources` command | Bot command returning last-run timestamp and status per connector | To Do | M2-08 |

---

## Milestone 3 — Features and Scoring

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| M3-01 | Rolling-slope Growth | 7-day linear regression on per-niche `NicheSignal` mention counts. Output: slope value | Done | M2-07 |
| M3-02 | Demand signals | Aggregate mention count, GitHub star delta, and App Store install proxy per niche per day | Done | M2-07 |
| M3-03 | Novelty dimension | `1 − (age_of_newest_signal_days / 30)`, clamped [0, 1] | Done | M2-07 |
| M3-04 | Percentile normalisation | For each dimension: compute percentile rank over rolling 30-day window of per-niche values | Done | M3-01, M3-02, M3-03 |
| M3-05 | Composite scorer | Weighted sum: Growth 0.41 · Demand 0.35 · Novelty 0.24. Persist daily result to `NicheScoreHistory` | Done | M3-04 |
| M3-06 | ADR-003 | Write scoring-design ADR: three dimensions, percentile rank, weights, spike-alert logic | Done | M3-05 |

---

## Milestone 4 — Agent Graph

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| M4-01 | `OpportunityState` TypedDict | Define shared state passed through all LangGraph nodes | Done | M1-03 |
| M4-02 | LangGraph graph skeleton | Wire five nodes in linear sequence; register start/end; add structured error field | Done | M4-01 |
| M4-03 | `fetcher_node` | Load latest `SourceItem` rows for the target niche from DB | Done | M4-02 |
| M4-04 | `retriever_node` | Compute and retrieve `NicheSignal` aggregates for the niche | Done | M4-03 |
| M4-05 | `forecaster_node` | Call rolling-slope + scorer; populate `scorecard` in state | Done | M4-04, M3-05 |
| M4-06 | Ollama adapter | `OllamaAdapter` implementing `LLMAdapter`. Target model: qwen2.5. Prompt templates in `agents/prompts.py` | Done | M1-08 |
| M4-07 | `reporter_node` | Call `OllamaAdapter.generate_brief()` with 90s `asyncio.wait_for` timeout. On timeout: log, return empty brief | Done | M4-05, M4-06 |
| M4-08 | `reviewer_node` | Validate brief completeness. Set `has_issues = True` and log gaps. Never retries | Done | M4-07 |
| M4-09 | Brief persistence | Save `OpportunityBrief` to DB with denormalised `evidence_json` snapshot and `score_breakdown_json` | Done | M4-08 |
| M4-10 | Scheduler: brief job | Register daily brief-generation job with `max_instances=1` on `AsyncIOScheduler` | Done | M4-09 |

---

## Milestone 5 — Full Telegram Bot

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| M5-01 | `/briefing` command | Return top 3 ranked `OpportunityBrief` rows formatted in MarkdownV2 | Done | M4-09 |
| M5-02 | `/niches` command | List all tracked niches with current score and trend label | Done | M3-05 |
| M5-03 | `/niche <slug>` command | Full scorecard and evidence for a specific niche; trim to 4096 chars with "…see brief" footer | Done | M4-09 |
| M5-04 | `/trending` command | Top rising signals across all sources in last 24h, ranked by signal count delta | Done | M3-02 |
| M5-05 | MarkdownV2 formatter | Helpers for bolding, escaping, trend arrows, source badges, length truncation | Done | — |
| M5-06 | Daily digest push | Scheduler hook at 08:00 UTC: top 3 briefs formatted and pushed to allowed chat IDs | Done | M5-01, M5-05 |
| M5-07 | Spike alert push | Daily job immediately after niche scoring: compare to `NicheScoreHistory`; push alert if Δ ≥ threshold | Done | M3-05, M5-05 |

---

## Milestone 5.5 — Bulk Backfill on Empty DB

> Goal: a fresh install populates the DB with ~30 days of real source history on first launch and produces meaningful percentile-normalised briefs immediately, instead of waiting for scheduled ingestion to accumulate history.

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| M55-01 | `since` parameter on connector base | Add optional `since: datetime \| None = None` to `BaseConnector.fetch()` and `run()`; default behaviour unchanged | Done | M5-07 |
| M55-02 | GitHub paginated backfill | Honour `since` via `pushed:>{since}`; paginate `?per_page=100&page=N` until empty or `BACKFILL_MAX_ITEMS_PER_SOURCE` reached | Done | M55-01 |
| M55-03 | HN paginated backfill | Replace hardcoded 6h lookback with `since`; raise `hitsPerPage`; paginate via `&page=` up to Algolia 1000-item limit | Done | M55-01 |
| M55-04 | Reddit paginated backfill | Per-sub `?after=` cursor pagination until oldest item < `since` or ~1000-item ceiling; log `oldest_item_age_days` per sub | Done | M55-01 |
| M55-05 | App Store mock — accept `since` | Signature parity only; mock data loaded as-is | Done | M55-01 |
| M55-06 | `rebuild_historical_signals` helper | Bin `SourceItem.created_at` into per-day `(niche_id, source_type, metric_name, day)` NicheSignal upserts across the backfill window | Done | M55-02, M55-03, M55-04, M55-05 |
| M55-07 | `score_all_niches_for_date(d)` | Implemented via existing `score_all_niches(as_of)` — backfill orchestrator calls it per-day; no new function needed | Done | M55-06 |
| M55-08 | `app/ingestion/backfill.py` orchestrator | `bulk_backfill(connectors, history_days)` runs sequential fetch → signal rebuild → daily scoring → brief generation; returns structured `BackfillReport`; emits one structured log line | Done | M55-06, M55-07 |
| M55-09 | Startup lifespan hook | In `app/main.py` lifespan, after `sync_niches_from_yaml()` and before `scheduler.start()`: if `BACKFILL_ON_EMPTY=true` and `SELECT 1 FROM source_item LIMIT 1` is empty, await `bulk_backfill()` | Done | M55-08 |
| M55-10 | Config + `.env.example` | Add `BACKFILL_ON_EMPTY` (default true), `BACKFILL_HISTORY_DAYS` (default 30), `BACKFILL_MAX_ITEMS_PER_SOURCE` (default 1000); document in `.env.example` | Done | M55-08 |
| M55-11 | CLI parity | Extend `scripts/run_ingestion.py` with `--backfill-days N` flag that calls `bulk_backfill()` directly | Done | M55-08 |
| M55-12 | ADR-007 | Document bulk-backfill design: trigger condition, history depth, NicheSignal historical rebuild strategy, Reddit ceiling caveat | Done | M55-09 |
| M55-13 | Tests | Connector `since`/pagination tests + `rebuild_historical_signals` test (N×days rows) + idempotency test (re-run produces no duplicates) | Done | M55-11 |

---

## Milestone 6 — Hardening and Evaluation

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| M6-01 | Weekly pruning job | Delete `SourceItem` > 90 days, `NicheSignal` raw > 30 days; retain daily aggregates. Log if last pruning > 10 days stale | To Do | M2-08 |
| M6-02 | Tests: connectors | Unit tests for GitHub, HN, Reddit, App Store mock connectors using fixture responses | To Do | M2-05 |
| M6-03 | Tests: scoring | Unit tests for rolling slope, percentile normalisation, composite scorer | To Do | M3-05 |
| M6-04 | Tests: agent graph | End-to-end graph run using `MockLLMAdapter`; assert brief shape and `has_issues` behaviour | To Do | M4-09 |
| M6-05 | Tests: bot handlers | Test all slash commands using python-telegram-bot `Application` test helpers | To Do | M5-04 |
| M6-06 | Mock historical replay harness | Script to seed `NicheSignal` with synthetic 60-day history and replay scoring pipeline | To Do | M3-05 |
| M6-07 | `docs/evaluation-plan.md` | Manual review checklists for evidence fidelity, score interpretability, signal freshness, alert accuracy | To Do | M5-07 |
| M6-08 | README complete | Setup guide, bot commands, architecture diagram, known limitations, Reddit UA instructions | To Do | M5-07 |

---

## Phase 1.5 — Backlog

> Not started until Phase 1 Definition of Done is fully green.

| ID | Title | Description | Status | Depends On |
|---|---|---|---|---|
| P15-01 | Google Trends connector | Integrate official Google Trends API (alpha). Add search-interest velocity to Demand signals | Backlog | Phase 1 done |
| P15-02 | Stack Overflow connector | Stack Exchange API. Fetch tag question volumes and weekly growth rate | Backlog | Phase 1 done |
| P15-03 | Prophet forecasting | Add Prophet service when ≥30 days of real NicheSignal history exists. Replace rolling slope for Growth | Backlog | P15-01, P15-02 |
| P15-04 | Competition dimension | Reintroduce Competition scoring (app-store saturation) when a real data provider is integrated. Rebalance weights | Backlog | P15-03 |
| P15-05 | Reddit UA compliance review | Verify UA string is accepted; check for rate-limit patterns in logs | Backlog | Phase 1 done |

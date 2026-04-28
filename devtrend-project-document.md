# DevTrend — Project Document

> **Version:** 3.1
> **Last updated:** April 28, 2026
> **Change log:** v3.1 — Added bulk backfill on empty DB at startup so a fresh install can produce meaningful percentile-normalised briefs immediately instead of waiting for scheduled ingestion to accumulate ~30 days of history. v3.0 — Phase 1 scope trimmed to four ingestion sources (GitHub, HN, Reddit, App Store mock). Prophet deferred to Phase 1.5 — replaced by rolling 7-day slope. Competition scoring dimension dropped from Phase 1 (score weights rebalanced to 0.41 / 0.35 / 0.24). Spike-alert monitor simplified to daily cadence. LangGraph retained from day one. Single asyncio event loop committed.

---

## 1. Overview

DevTrend is a multi-source agentic intelligence system that monitors developer-facing market signals across app stores, GitHub, search trends, and developer communities, then synthesises them into structured **opportunity briefs** suggesting when and in which niche to launch a new app.

Phase 1 delivers a **Telegram bot** as the primary interface. The bot handles both on-demand commands and automatic daily push notifications. A background scheduler runs daily ingestion and analysis jobs without any user prompt. FastAPI is retained as a lightweight internal backbone for health checks only — there is no web dashboard in Phase 1.

### Example automatic daily notification

```
🚀 DevTrend Daily Brief — April 23, 2026

#1 — AI-Powered Habit Trackers (Wellness)
Score: 84/100 | Trend: ↑ Rising
GitHub momentum strong (Python + React Native).
HN discussion up 40% WoW. Search interest accelerating.

#2 — Local-first Personal Finance Tools
Score: 71/100 | Trend: ↑ Steady
Reddit r/startups mentions up. Low incumbent saturation.

#3 — Developer Onboarding Automation
Score: 66/100 | Trend: → Stable
SO tag volume growing. Several OSS repos with rising stars.
```

---

## 2. Goals

- Monitor developer and market signals from multiple heterogeneous sources on a daily automated schedule.
- Normalise those signals into a unified niche-level representation stored in SQLite.
- Compute 7-day rolling slope for growth momentum; use heuristic composite scoring for all dimensions.
- Use LangGraph to orchestrate a transparent agent graph: fetch → retrieve → forecast → report → review.
- Generate structured opportunity briefs with evidence, trend direction, and rationale.
- Deliver briefs and alerts through a Telegram bot with both automatic push and command-driven access.
- Maintain full traceability through structured logging and deterministic scoring.

---

## 3. Non-Goals for Phase 1

- Web dashboard or any browser-based UI.
- Azure AKS, Kubernetes, or Helm deployment.
- Prophet or deep learning forecasting (deferred to Phase 1.5).
- Google Trends and Stack Overflow connectors (deferred to Phase 1.5).
- Competition scoring dimension (deferred to Phase 1.5 when a real app-store provider is integrated).
- Official app-store API integrations (mocked in Phase 1).
- Enterprise authentication or multi-tenant access control.
- Vector DB or embedding layer (deferred to Phase 2).

---

## 4. Phase 1 Decisions

| Area | Decision |
|---|---|
| Primary interface | Telegram bot (python-telegram-bot v20+) |
| Notification model | Daily digest push + threshold spike alerts (daily cadence) |
| Bot interaction | Command-driven AND automatic scheduled push |
| Chat authorisation | Allowlist via `TELEGRAM_ALLOWED_CHAT_IDS` — unknown chats receive a polite rejection |
| LLM backend | Local Ollama (qwen2.5) with adapter pattern for future swap |
| Agent framework | LangGraph (linear graph; conditional edges deferred to Phase 2) |
| Internal API | FastAPI (health + internal trigger endpoints only) |
| Storage | SQLite via SQLAlchemy |
| Forecasting | Rolling 7-day slope (Prophet deferred to Phase 1.5) |
| Scoring | Three-dimension weighted heuristic: Growth 0.41 / Demand 0.35 / Novelty 0.24 |
| Deployment | Local / single server, Phase 1 only |
| Data access | Real: GitHub API, HN Algolia API, Reddit public JSON; Mocked: App Store |
| Niche taxonomy | Hand-curated YAML (`data/niches.yaml`) with 8–12 seeds + keyword lists |
| Event loop | Single asyncio loop — `AsyncIOScheduler` + async bot + async FastAPI. No threads. |
| Bulk backfill | One-shot 30-day historical fetch on startup when DB is empty; rebuilds per-day NicheSignal aggregates from each item's `created_at` so percentile normalisation works from day one. Gated by `BACKFILL_ON_EMPTY=true`. |
| Testing | `MockLLMAdapter` (deterministic fixture briefs) + python-telegram-bot `Application` test helpers |

---

## 5. High-Level Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     TELEGRAM BOT                         │
│          (python-telegram-bot, polling mode)             │
│   /start  /briefing  /niches  /niche <slug>  /trending   │
│                     /sources  /help                      │
│              Allowlist middleware on all handlers        │
└─────────────────────┬────────────────────────────────────┘
                      │  commands + push messages
                      ▼
┌──────────────────────────────────────────────────────────┐
│               LANGGRAPH AGENT GRAPH                      │
│                                                          │
│   fetcher_node → retriever_node → forecaster_node        │
│                                 → reporter_node          │
│                                 → reviewer_node          │
└───────┬──────────────────────────────────┬───────────────┘
        │                                  │
        ▼                                  ▼
┌──────────────────┐             ┌─────────────────────┐
│  INGESTION LAYER │             │  SCORING LAYER      │
│  GitHub          │             │  Rolling-slope calc │
│  Hacker News     │             │  Heuristic scorer   │
│  Reddit          │             │  Opportunity ranker │
│  AppStore Mock   │             └─────────────────────┘
└───────┬──────────┘
        ▼
┌──────────────────────────────────────────────────────────┐
│                   SQLITE DATABASE                        │
│  SourceItem | Niche | NicheSignal | NicheScoreHistory    │
│  OpportunityBrief                                        │
└──────────────────────────────────────────────────────────┘
        ▲
┌──────────────────────────────────────────────────────────┐
│              BACKGROUND SCHEDULER (APScheduler)          │
│  Daily ingestion → scoring → brief generation            │
│  Daily digest push → Telegram                            │
│  Daily spike monitor → alert → Telegram                  │
│  Weekly pruning job                                      │
└──────────────────────────────────────────────────────────┘
```

---

## 6. Repository Structure

```
devtrend/
├── app/
│   ├── main.py                     # FastAPI + bot entrypoint
│   ├── config.py                   # Settings from .env
│   ├── db.py                       # SQLAlchemy engine + session
│   ├── models.py                   # ORM table definitions
│   ├── schemas.py                  # Pydantic schemas
│   │
│   ├── ingestion/
│   │   ├── base.py                 # Abstract connector interface
│   │   ├── github_connector.py
│   │   ├── hn_connector.py
│   │   ├── reddit_connector.py
│   │   ├── appstore_mock_connector.py
│   │   └── scheduler.py            # APScheduler job definitions
│   │
│   ├── features/
│   │   ├── trend_features.py       # Rolling slope, delta, volatility
│   │   ├── sentiment_features.py   # Sentiment from text signals
│   │   └── niche_builder.py        # Keyword-based niche grouping + attachment
│   │
│   ├── forecasting/
│   │   └── scoring.py              # Weighted composite scorer (three dimensions)
│   │
│   ├── agents/
│   │   ├── state.py                # OpportunityState TypedDict
│   │   ├── graph.py                # LangGraph graph definition
│   │   ├── nodes.py                # Node functions
│   │   └── prompts.py              # Prompt templates
│   │
│   ├── tools/
│   │   ├── retriever.py
│   │   ├── forecaster.py
│   │   ├── reporter.py
│   │   └── source_inspector.py
│   │
│   ├── llm/
│   │   ├── base.py                 # LLMAdapter abstract class
│   │   ├── mock_adapter.py         # Deterministic fixture adapter for tests
│   │   ├── ollama_adapter.py       # Phase 1
│   │   ├── openai_adapter.py       # Phase 2 swap-in
│   │   └── anthropic_adapter.py    # Phase 2 swap-in
│   │
│   ├── bot/
│   │   ├── bot.py                  # Bot init + dispatcher
│   │   ├── middleware.py           # Allowlist check (TELEGRAM_ALLOWED_CHAT_IDS)
│   │   ├── handlers.py             # Slash command handlers
│   │   ├── notifications.py        # Push message builders
│   │   ├── formatter.py            # MarkdownV2 formatting helpers
│   │   └── scheduler_hooks.py      # Scheduler → Telegram bridge
│   │
│   └── api/
│       ├── routes_health.py
│       └── routes_internal.py      # Internal trigger endpoints
│
├── data/
│   ├── niches.yaml                 # Hand-curated niche taxonomy + keyword seeds
│   ├── raw/                        # (gitignored)
│   ├── processed/                  # (gitignored)
│   └── mock/                       # Mock app-store JSON datasets
│
├── docs/
│   ├── architecture.md
│   ├── decisions.md                # ADR log — written per decision as it happens
│   ├── evaluation-plan.md
│   └── roadmap.md                  # Phase 1 → Phase 1.5 → Phase 2 roadmap
│
├── scripts/
│   ├── seed_mock_data.py
│   ├── run_ingestion.py
│   └── run_forecasts.py
│
├── tests/
│   ├── test_connectors.py
│   ├── test_scoring.py
│   ├── test_agent_graph.py
│   └── test_bot_handlers.py
│
├── README.md
└── .env.example
```

---

## 7. Telegram Bot Design

### 7.1 Slash Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and feature overview |
| `/briefing` | On-demand top 3 opportunity briefs right now |
| `/niches` | List all tracked niches with current scores |
| `/niche <slug>` | Full scorecard and evidence for a specific niche |
| `/trending` | Top rising signals across all sources in last 24h |
| `/sources` | Last ingestion timestamp and status per source |
| `/help` | List all available commands |

All handlers pass through the allowlist middleware. Unknown chats receive: _"This bot is private. Access is restricted."_

### 7.2 Automatic Push Notifications

**Daily Digest** — sent every morning at a configurable time (default 08:00 UTC).

Content:
- Top 3 ranked niches with headline, score, trend direction emoji
- One-sentence evidence summary per niche
- Overall market signal summary sentence

**Spike Alert** — fires automatically once per day immediately after the niche scoring job, comparing today's composite score to the last persisted daily score.

Trigger condition:
```
score_today − last_persisted_daily_score >= SPIKE_ALERT_THRESHOLD  (default: 15)
```

Content:
- Niche name and new score
- Source signal that drove the spike
- Brief evidence context

### 7.3 Message Formatting Rules

- Use Telegram MarkdownV2 throughout
- Bold niche names and scores
- Trend direction: ↑ Rising · ↓ Declining · → Stable
- Numbered lists for ranked briefs
- Source badges for evidence transparency
- Keep daily digest under 4096 characters (Telegram message limit)

---

## 8. Data Sources

### 8.1 GitHub (Real)

API: GitHub REST API (`https://api.github.com`)

Collect per repo:
- Name, description, language, topic tags
- Stars today / this week proxy
- Commit activity, created and updated dates

Purpose: detect technology stack momentum and developer attention.

### 8.2 Hacker News (Real)

API: Algolia HN Search (`https://hn.algolia.com/api/v1/search`)

Collect per story:
- Title, URL, points, comment count, created date
- Keyword and niche tag matches

Purpose: detect early founder and builder discourse before mainstream saturation.

### 8.3 Reddit (Real)

Endpoint: public JSON (`https://www.reddit.com/r/{sub}/new.json`)

Target subreddits: r/startups · r/SideProject · r/Entrepreneur · r/reactnative · r/androiddev · r/iOSProgramming

Collect: post title, body snippet, score, comment count, timestamp.

Note: must set a descriptive `User-Agent` header (configured via `REDDIT_USER_AGENT` env var) to avoid request blocking. See README for format.

Purpose: grassroots demand signals and repeated unmet-need patterns.

### 8.4 App Store / Google Play (Mocked)

Phase 1 uses structured JSON mock datasets under `data/mock/`.
The connector interface is identical to real connectors so this layer can be replaced later with a commercial provider.

Mocked fields: category growth index, estimated install proxy, average rating, review sentiment, competitor density.

### 8.5 Deferred to Phase 1.5

- **Google Trends** — official Trends API (alpha). Will replace any unofficial approach.
- **Stack Overflow** — Stack Exchange API, tag question volumes and growth rate proxies.

---

## 9. Canonical Data Model

### SourceItem

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| source_type | String | github / hn / reddit / appstore |
| external_id | String | Original ID from source |
| title | String | Item title |
| body | Text | Body or description snippet |
| url | String | Source URL |
| created_at | DateTime | Original publish time |
| ingested_at | DateTime | Time stored in DB |
| niche_id | Integer | FK → Niche (assigned at ingestion via keyword match) |
| metadata_json | JSON | Source-specific extras |

### Niche

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| name | String | Human-readable name |
| slug | String | Command-safe identifier (e.g. ai-habit-trackers) |
| summary | Text | Short description |
| category | String | Top-level category (wellness, finance, devtools…) |
| keywords_json | JSON | Seed and derived keyword list (sourced from data/niches.yaml) |

### NicheSignal

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| niche_id | Integer | FK → Niche |
| source_type | String | Origin source |
| metric_name | String | e.g. mention_count, star_delta, search_index |
| metric_value | Float | Numeric value |
| metric_timestamp | DateTime | Time of measurement |
| metadata_json | JSON | Extra context |

### NicheScoreHistory

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| niche_id | Integer | FK → Niche |
| score_total | Float | 0–100 composite score for that day |
| score_breakdown_json | JSON | Per-dimension scores |
| scored_at | DateTime | Daily scoring timestamp |

### OpportunityBrief

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| niche_id | Integer | FK → Niche |
| headline | String | One-line summary |
| summary | Text | Full brief text (LLM-generated) |
| score_total | Float | 0–100 composite score |
| score_breakdown_json | JSON | Per-dimension scores |
| evidence_json | JSON | Denormalised snapshot: `[{source_type, external_id, title, url, created_at, excerpt}]` |
| forecast_label | String | Rising / Stable / Declining |
| has_issues | Boolean | Set by reviewer_node when completeness gaps are found |
| generated_at | DateTime | Generation timestamp |
| model_name | String | LLM model used |

---

## 10. Scoring Design

### Formula

```
total_score = (growth × 0.41) + (demand × 0.35) + (novelty × 0.24)
```

All dimensions normalised 0–100. Weights are configurable in `config.py`.

### Dimensions

| Dimension | Weight | Signals used |
|---|---|---|
| Growth | 0.41 | 7-day rolling linear regression slope on per-niche signal counts |
| Demand | 0.35 | Mention count, install proxy, GitHub star delta |
| Novelty | 0.24 | `1 − (age_of_newest_signal_days / 30)`, clamped to [0, 1] |

**Competition dimension** is deferred to Phase 1.5, pending integration of a real app-store data provider.

### 10.1 Normalisation

Each dimension value is normalised using **percentile rank over a rolling 30-day window** for that niche. A niche at the 90th percentile on Growth receives a Growth score of 90. This is stable across day-to-day outliers and interpretable to a non-technical audience.

### Threshold Alert Trigger

A spike alert fires once daily, immediately after the niche scoring job, when:

```
score_today − last_persisted_daily_score >= SPIKE_ALERT_THRESHOLD  (default: 15)
```

`last_persisted_daily_score` is read from `NicheScoreHistory`.

---

## 11. Agentic Reasoning Layer (LangGraph)

### Agent State

```python
class OpportunityState(TypedDict):
    niche: dict
    source_items: list
    signals: list
    forecast: dict
    scorecard: dict
    brief: dict
    errors: list
    triggered_by: str   # "scheduler" | "command" | "threshold_alert"
```

### Graph Flow

```
START
  └─→ fetcher_node
        └─→ retriever_node
              └─→ forecaster_node
                    └─→ reporter_node
                          └─→ reviewer_node
                                └─→ END
```

### Node Responsibilities

| Node | Responsibility |
|---|---|
| fetcher_node | Load latest SourceItems for the target niche from DB |
| retriever_node | Compute and retrieve NicheSignals |
| forecaster_node | Compute rolling-slope growth, run three-dimension scoring, populate scorecard |
| reporter_node | Call Ollama adapter → generate brief text; bounded by per-niche 90s timeout |
| reviewer_node | Validate brief completeness; set `has_issues = True` and log gaps if found. Does not retry. |

### LLM Adapter Interface

```python
class LLMAdapter:
    def generate_brief(self, context: dict) -> str: ...
    def summarize_evidence(self, items: list) -> str: ...
    def review_brief(self, brief: str) -> dict: ...

class MockAdapter(LLMAdapter): ...     # Deterministic fixture briefs for tests
class OllamaAdapter(LLMAdapter): ...   # Phase 1
class OpenAIAdapter(LLMAdapter): ...   # Phase 2
class AnthropicAdapter(LLMAdapter): ...# Phase 2
```

The rest of the codebase depends only on `LLMAdapter`, never on a vendor SDK directly.

---

## 12. Scheduling Strategy

APScheduler (`AsyncIOScheduler`) runs in the same process as the Telegram bot and FastAPI app on a single asyncio event loop.

| Job | Frequency | Action |
|---|---|---|
| GitHub ingestion | Every 6 hours | Fetch trending repos |
| HN ingestion | Every 6 hours | Fetch top stories |
| Reddit ingestion | Every 12 hours | Fetch new posts per subreddit |
| Mock App Store reload | Daily | Reload mock JSON datasets |
| Niche scoring | Daily | Recompute all niche scores; write to NicheScoreHistory |
| Brief generation | Daily | Run agent graph for all niches (max_instances=1; 90s per-niche timeout) |
| Spike monitor | Daily (immediately after Niche scoring) | Compare today's score to NicheScoreHistory; push alert if Δ ≥ threshold |
| Daily digest push | Daily 08:00 UTC | Send morning Telegram message |
| Weekly pruning | Weekly (Sunday 03:00 UTC) | Delete SourceItem > 90 days, NicheSignal raw > 30 days; keep daily aggregates |

### 12.1 Bulk Backfill on Empty DB

On app startup, after niche sync and connector instantiation but **before** `scheduler.start()`, the lifespan checks `SELECT 1 FROM source_item LIMIT 1`. If the table is empty and `BACKFILL_ON_EMPTY=true`, a one-shot bulk backfill runs synchronously before the scheduler takes over.

Flow:

```
db_empty? ── yes ──▶ bulk_backfill(history_days=30)
                       │
                       ├─ for each connector: fetch(since=now − 30d) with pagination
                       ├─ rebuild_historical_signals(): bin SourceItems by created_at
                       │  into per-day (niche, source) NicheSignal rows
                       ├─ score_all_niches_for_date(d) for each day in window
                       │  → NicheScoreHistory populated day-by-day
                       └─ run_brief_for_niche() once per niche
                                │
                                ▼
                       scheduler.start() (regular cadence resumes)
```

**Per-source historical depth:**

| Source | Capability | Practical depth at 30d |
|---|---|---|
| GitHub | `pushed:>{since}` + `?per_page=100&page=N` | Full 30 days (capped at `BACKFILL_MAX_ITEMS_PER_SOURCE`) |
| HN | Algolia `numericFilters=created_at_i>{epoch}` + paging | Full 30 days |
| Reddit | `/r/{sub}/new.json?after=` cursor | Up to ~1000 items per sub — busy subs (`r/startups`) may only reach ~7-10 days; logged as `oldest_item_age_days` per sub |
| App Store mock | Static JSON | All mock data loaded |

**Idempotency**: relies on the existing `(source_type, external_id)` uniqueness — re-running is a no-op. Subsequent restarts find a non-empty DB and skip the backfill.

**Manual trigger**: same logic is exposed via `python -m scripts.run_ingestion --backfill-days 30` for dev/recovery without restarting the app.

---

## 13. Configuration

`.env.example`:

```env
APP_NAME=DevTrend
ENV=dev

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ALLOWED_CHAT_IDS=    # comma-separated; unknown chats are rejected

# LLM
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5

# Database
DATABASE_URL=sqlite:///./devtrend.db

# Data Sources
GITHUB_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=DevTrend/1.0 (by /u/yourhandle)
ENABLE_MOCK_APPSTORE=true

# Scheduling
DAILY_DIGEST_TIME=08:00
SPIKE_ALERT_THRESHOLD=15

# Bulk backfill (runs once on startup if SourceItem table is empty)
BACKFILL_ON_EMPTY=true
BACKFILL_HISTORY_DAYS=30
BACKFILL_MAX_ITEMS_PER_SOURCE=1000

# Logging
LOG_LEVEL=INFO
```

---

## 14. Logging and Traceability

Every agent run, ingestion job, and notification push emits a structured JSON log record:

```json
{
  "timestamp": "2026-04-23T08:00:01Z",
  "component": "reporter_node",
  "niche_slug": "ai-habit-trackers",
  "triggered_by": "scheduler",
  "status": "success",
  "duration_ms": 1240,
  "brief_id": 42,
  "model": "qwen2.5"
}
```

Every generated OpportunityBrief stores the full `evidence_json` (denormalised snapshot) and `score_breakdown_json` so any ranking decision can be traced back to its source signals.

---

## 15. Evaluation Strategy

| Criterion | How to evaluate |
|---|---|
| Evidence fidelity | Does brief text reflect stored SourceItems? |
| Score interpretability | Can score breakdown explain ranking to a founder? |
| Signal freshness | Are underlying records within expected staleness window? |
| Slope usefulness | Does rolling-slope direction match intuitive trend? |
| Notification quality | Are daily digests concise, actionable, correctly formatted? |
| Spike alert accuracy | Do threshold alerts correspond to real signal jumps? |

Phase 1 evaluation is manual and checklist-based. A replay harness using mock historical windows is built in Milestone 6.

---

## 16. Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Telegram bot token exposed | High | Store in .env, never commit; .env is gitignored |
| Ollama / qwen2.5 output drift | Medium | Pin model version in .env; structured prompt templates + response schema validation |
| qwen2.5 response schema non-compliance | Medium | Validate JSON output in reporter_node; fall back to template if parsing fails |
| Noisy niche clustering | Medium | Curated keyword seeds in data/niches.yaml; rule-assisted grouping |
| Sparse rolling-slope window | Medium | Mock historical windows for warm-up in tests; label clearly |
| Reddit UA policy | Medium | Set descriptive REDDIT_USER_AGENT; document format in README |
| Reddit / GitHub rate limiting | Medium | Respectful polling intervals + caching + retry with backoff |
| APScheduler missed jobs | Low | Log every job run; expose last-run status via /sources |
| Brief generation timeout | Low | Per-niche asyncio.wait_for(90s); log and skip; APScheduler max_instances=1 |
| Telegram message > 4096 chars | Low | Truncation logic in formatter.py with "…see /niche" footer |
| SQLite size / pruning miss | Low | Weekly pruning job; alert in logs if last pruning > 10 days stale |
| Bulk-backfill rate-limit pile-up on first startup | Medium | Sequential per-connector fetch (not parallel); existing `_request_with_retry` honours Retry-After; cap items per source via `BACKFILL_MAX_ITEMS_PER_SOURCE` |
| Reddit 1000-post-per-sub ceiling on busy subs | Low | Documented limitation; backfill report logs `oldest_item_age_days` per sub so partial coverage is visible. Full 30-day depth not guaranteed for `r/startups`-class subs |

---

## 17. Implementation Roadmap

### Milestone 1 — Foundation
- [ ] Create `.env.example`, `config.py`
- [ ] Verify and extend `.gitignore` (`*.db`, `data/raw/`, `data/processed/`)
- [ ] Scaffold FastAPI app with health endpoint
- [ ] SQLAlchemy models and SQLite setup (including `NicheScoreHistory`)
- [ ] Structured logging configured
- [ ] Telegram bot connected — `/start` and `/help` working with allowlist middleware
- [ ] Scaffold `MockLLMAdapter` stub
- [ ] Write ADR-001 (name: DevTrend), ADR-002 (event loop: single asyncio)

### Milestone 2 — Ingestion Layer
- [ ] Abstract connector base class with `fetch()`, `normalize()`, `save()`, `run()`
- [ ] GitHub connector (real)
- [ ] Hacker News connector (real)
- [ ] Reddit connector (real, public JSON) — `REDDIT_USER_AGENT` set and documented
- [ ] Mock App Store connector + seed data files
- [ ] `data/niches.yaml` committed with 8–12 seeds + keyword lists
- [ ] Ingestion attaches `niche_id` to `SourceItem` via keyword match
- [ ] APScheduler (`AsyncIOScheduler`) wired for all ingestion jobs
- [ ] `/sources` command showing last-run status

### Milestone 3 — Features and Scoring
- [ ] Rolling 7-day slope for Growth dimension
- [ ] Mention-count and star-delta signals for Demand dimension
- [ ] Novelty = `1 − (age_of_newest_signal_days / 30)`, clamped [0,1]
- [ ] Percentile-rank normalisation (30-day window per niche)
- [ ] Composite score (0.41 / 0.35 / 0.24) persisted to `NicheScoreHistory` after each daily run
- [ ] Write ADR-003 (scoring design: percentile rank, three dimensions)

### Milestone 4 — Agent Graph
- [ ] LangGraph `OpportunityState` TypedDict
- [ ] fetcher_node, retriever_node, forecaster_node, reporter_node, reviewer_node
- [ ] reviewer_node: validate-and-log only; sets `has_issues` flag; never retries
- [ ] Ollama LLM adapter (qwen2.5)
- [ ] Per-niche 90s timeout; APScheduler `max_instances=1` on brief job
- [ ] OpportunityBrief persisted to DB with full denormalised evidence JSON

### Milestone 5 — Full Telegram Bot
- [ ] `/briefing` — on-demand top 3 briefs
- [ ] `/niches` — ranked niche list
- [ ] `/niche <slug>` — full scorecard
- [ ] `/trending` — top 24h signals
- [ ] Daily digest scheduler hook + MarkdownV2 formatter
- [ ] Daily spike alert: post-scoring comparison against `NicheScoreHistory`

### Milestone 5.5 — Bulk Backfill on Empty DB
- [ ] Extend `BaseConnector.fetch()` / `run()` to accept optional `since: datetime`
- [ ] GitHub connector: paginate `pushed:>{since}` + `?page=N`
- [ ] HN connector: replace hardcoded 6h lookback with `since`; paginate via Algolia `page`
- [ ] Reddit connector: per-sub `after`-cursor pagination until `since` reached or 1000-item ceiling; log `oldest_item_age_days`
- [ ] App Store mock connector: accept `since` param (no-op)
- [ ] `app/ingestion/backfill.py` — `bulk_backfill(history_days)` orchestrator with structured `BackfillReport`
- [ ] `rebuild_historical_signals(history_days)` — bin SourceItems by `created_at` into per-day NicheSignal rows
- [ ] `score_all_niches_for_date(d)` variant in `app/forecasting/scoring.py` so `NicheScoreHistory` is populated day-by-day
- [ ] Lifespan hook in `app/main.py`: run backfill if `BACKFILL_ON_EMPTY=true` AND DB is empty, before `scheduler.start()`
- [ ] Config + `.env.example`: `BACKFILL_ON_EMPTY`, `BACKFILL_HISTORY_DAYS`, `BACKFILL_MAX_ITEMS_PER_SOURCE`
- [ ] CLI parity: extend `scripts/run_ingestion.py` with `--backfill-days N`
- [ ] Write ADR-007 (bulk backfill on empty DB: trigger, depth, signal-rebuild strategy)

### Milestone 6 — Hardening and Evaluation
- [ ] Weekly pruning job wired and tested
- [ ] Full test suite: connectors, scoring, agent graph, bot handlers (using `MockLLMAdapter`)
- [ ] Mock historical replay harness
- [ ] `docs/decisions.md` complete with all ADRs
- [ ] `docs/evaluation-plan.md` with manual review checklists
- [ ] README complete: setup, commands, architecture, limitations

---

## 18. Definition of Done — Phase 1

Phase 1 is complete when ALL of the following are true:

- [ ] Bot runs locally and all slash commands return correct responses.
- [ ] Allowlist middleware rejects unknown chats.
- [ ] Daily digest pushes automatically every morning without manual trigger.
- [ ] Spike alerts fire correctly once daily when threshold is crossed.
- [ ] GitHub, HN, Reddit, and mock App Store all ingest successfully on schedule.
- [ ] Rolling-slope growth computed daily per niche; scores written to `NicheScoreHistory`.
- [ ] Opportunity briefs generated by the full LangGraph agent graph.
- [ ] `has_issues` flag set correctly on briefs with reviewer gaps.
- [ ] Weekly pruning job runs and keeps DB size bounded.
- [ ] All jobs and agent runs produce structured JSON logs.
- [ ] README documents setup, bot commands, architecture, and known limitations.

---

## 19. Recommended First Implementation Order

1. Create `.env`, `config.py`, `db.py`, `models.py`; extend `.gitignore`
2. Write ADR-001 (name) and ADR-002 (event loop) in `docs/decisions.md`
3. Scaffold FastAPI app — health endpoint working
4. Connect Telegram bot — `/start` and `/help` with allowlist middleware; scaffold `MockLLMAdapter`
5. Build connector base class + GitHub, HN, Reddit connectors
6. Add mock app-store JSON dataset and connector
7. Commit `data/niches.yaml` (8–12 niches); build niche attachment logic in ingestion
8. Wire `AsyncIOScheduler` for ingestion jobs
9. Build rolling-slope growth + demand + novelty scorers; percentile normalisation; write to `NicheScoreHistory`; write ADR-003
10. Build LangGraph agent graph + Ollama adapter (qwen2.5)
11. Wire agent output → MarkdownV2 formatter → Telegram push
12. Add daily digest and spike alert scheduler hooks
13. Add remaining bot commands
14. Add weekly pruning job + tests + fill `docs/`

---

## 20. Phase 1.5 — Deferred Items

These items are ready to implement once Phase 1 is shipped and stable:

- **Google Trends connector** — use the official Google Trends API (alpha). Replaces any unofficial pytrends approach.
- **Stack Overflow connector** — Stack Exchange API, tag question volumes and growth rate proxies.
- **Prophet forecasting service** — revisit when ≥30 days of real NicheSignal history exists. Replace rolling slope for the Growth dimension; rebalance weights accordingly.
- **Competition scoring dimension** — reintroduce when a real app-store data provider is integrated. Update formula to four-dimension weighted sum.
- **`docs/roadmap.md`** — full Phase 1.5 and Phase 2 roadmap with timelines.

---

## 21. Future Phase 2 Directions

- Replace SQLite with PostgreSQL + pgvector for embedding support.
- Add vector embedding layer for semantic niche retrieval.
- Swap Ollama adapter for OpenAI or Anthropic hosted model.
- Add web dashboard (FastAPI + Jinja2 or React).
- Add ARIMA / SLinear-inspired deep forecasting.
- Containerise with Docker Compose → AKS Helm charts.
- Multi-user Telegram support with per-user niche subscriptions.
- LangGraph conditional edges and parallel branches (reviewer loop, parallel source fetches).

---

## Open Points (resolve before Milestone 2)

1. **Reddit User-Agent string** — draft and document in README. Format: `DevTrend/1.0 (by /u/yourhandle)`.
2. **Daily-digest timezone** — default in config is `08:00`; confirm whether this is UTC or server-local. Recommend UTC.
3. **Niche taxonomy content** — `data/niches.yaml` must have 8–12 entries before Milestone 2 begins. Draft list not yet chosen.

---

*End of DevTrend Project Document v3.0*

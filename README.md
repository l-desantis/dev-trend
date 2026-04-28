# DevTrend

[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-pytest-green?style=flat-square)](tests/)

> Multi-source agentic intelligence system that monitors developer market signals and synthesises them into structured **opportunity briefs** — helping you decide when and where to launch your next app.

Delivered through a **Telegram bot** with daily push notifications and on-demand commands.

**Example output** (`/niche ai-habit-trackers`):
```
AI-Powered Habit Trackers — Score 84 ↑
Strong momentum this week: 3 new repos crossing 50 stars, HN thread at 200+
points, Reddit mentions up 40%. Growth slope: +0.6.
Evidence: habit-tracker repo (github), "Show HN: AI streak coach" (hn).
```

---

## Features

- **Multi-source ingestion** — GitHub (stars & repos), Hacker News (Algolia), Reddit, and App Store (mock)
- **Percentile-normalised scoring** — Growth, Demand, and Novelty dimensions combined into a 0–100 niche score
- **LLM-generated briefs** — LangGraph agent graph (fetcher → retriever → forecaster → reporter → reviewer) produces structured opportunity briefs via a local Ollama model or Claude/OpenAI
- **Telegram bot** — Seven slash commands with MarkdownV2 formatting, allowlist access control, and spike-alert push notifications
- **Automated scheduling** — APScheduler jobs for ingestion (6 h / 12 h), daily scoring, brief generation, weekly pruning, and digest push
- **Bulk backfill** — On first launch with an empty DB, up to 30 days of historical data is fetched automatically so scores are meaningful immediately
- **Replay harness** — Seed synthetic history and replay the scoring loop day-by-day for evaluation without waiting for real ingestion

---

## Quick Start

**Prerequisites:** Python 3.11+, [`uv`](https://docs.astral.sh/uv/), a Telegram bot token, and [Ollama](https://ollama.com) (or set `LLM_PROVIDER=mock` to skip the LLM).

### 1. Clone and install

```bash
git clone https://github.com/your-username/dev-trend.git
cd dev-trend
uv sync
```

### 2. Configure

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Comma-separated chat IDs that may use the bot |
| `GITHUB_TOKEN` | Optional — raises GitHub rate limit from 60 → 5 000 req/h |
| `REDDIT_USER_AGENT` | Your Reddit handle, e.g. `DevTrend/1.0 (by /u/yourhandle)` |
| `LLM_PROVIDER` | `ollama` (default) or `mock` (skips LLM, for development) |

### 3. Initialise the database

```bash
uv run python -c "import asyncio; from app.db import init_db; asyncio.run(init_db())"
```

### 4. Start the bot

```bash
uv run python -m app.main
```

On first launch with an empty database, a bulk backfill runs automatically (`BACKFILL_ON_EMPTY=true`). After ~2 minutes, send `/briefing` to your bot — you should see percentile-normalised scores straight away.

> [!TIP]
> Set `LLM_PROVIDER=mock` and `BACKFILL_ON_EMPTY=false` for a fast development loop that skips both the LLM and the initial backfill.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Telegram Bot (python-telegram-bot v20, async polling)       │
│  Handlers: /start /briefing /niches /niche /trending /sources│
└────────────────────────┬────────────────────────────────────┘
                         │ push (digest, spike alerts)
┌────────────────────────▼────────────────────────────────────┐
│  APScheduler (AsyncIOScheduler, same event loop)             │
│  • Ingestion jobs: GitHub 6h, HN 6h, Reddit 12h, AppStore 7h│
│  • Scoring job: daily 02:15 UTC                              │
│  • Brief generation: daily 03:00 UTC                         │
│  • Daily digest push: configurable (default 08:00 UTC)       │
│  • Weekly pruning: Sunday 03:00 UTC                          │
└──┬─────────────────────┬──────────────────────┬─────────────┘
   │                     │                      │
┌──▼──────────┐  ┌───────▼────────┐  ┌──────────▼──────────┐
│  Connectors │  │  Signal        │  │  Agent Graph        │
│  (httpx)    │  │  Aggregator    │  │  (LangGraph)        │
│  GitHub     │  │  → NicheSignal │  │  fetcher → retriever│
│  HN Algolia │  │  (daily aggs)  │  │  → forecaster       │
│  Reddit     │  └───────┬────────┘  │  → reporter (LLM)   │
│  AppStore   │          │           │  → reviewer          │
│  (mock)     │  ┌───────▼────────┐  └──────────┬──────────┘
└──┬──────────┘  │  Scorer        │             │
   │             │  percentile    │             │
   │             │  rank (30d)    │             │
   │             └───────┬────────┘             │
   │                     │                      │
   └──────────┬──────────┘                      │
              │                                 │
┌─────────────▼─────────────────────────────────▼────────────┐
│  SQLite (aiosqlite)                                          │
│  source_items · niche_signals · niche_score_history          │
│  opportunity_briefs · niches · maintenance_state             │
└─────────────────────────────────────────────────────────────┘
```

---

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and feature overview |
| `/briefing` | Top-3 opportunity briefs ranked by score |
| `/niches` | All tracked niches with current scores and trend arrows |
| `/niche <slug>` | Full scorecard, evidence, and brief for one niche |
| `/trending` | Top rising signals across all sources in the last 24 h |
| `/sources` | Last ingestion timestamp and item count per source |
| `/help` | Show all commands |

Access is restricted to `TELEGRAM_ALLOWED_CHAT_IDS`. Unknown chats receive a polite rejection and no further output.

---

## Configuration

All settings are read from `.env` (see [`.env.example`](.env.example) for the full list).

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./devtrend.db` | SQLite path or async DSN |
| `LLM_PROVIDER` | `ollama` | `ollama` or `mock` |
| `OLLAMA_MODEL` | `qwen2.5` | Model pulled in Ollama |
| `SPIKE_ALERT_THRESHOLD` | `15.0` | Score-delta that triggers a push alert |
| `SOURCE_RETENTION_DAYS` | `90` | Prune `source_items` older than this |
| `SIGNAL_RETENTION_DAYS` | `30` | Prune non-aggregate signals older than this |
| `BACKFILL_ON_EMPTY` | `true` | Fetch 30 days of history on first launch |
| `BACKFILL_HISTORY_DAYS` | `30` | History depth for the initial backfill |

---

## Running Tests

```bash
uv run pytest                          # full suite
uv run pytest tests/test_scoring.py -v # specific module
uv run pytest tests/test_pruning.py -v
uv run pytest -q                       # quiet summary
```

Tests use an in-memory SQLite DB — no external services or setup required.

---

## Replay Harness

Seed synthetic history and replay the daily scoring loop for evaluation:

```bash
# Use a separate replay DB (script refuses to run against a production DB)
DATABASE_URL=sqlite+aiosqlite:///./devtrend-replay.db \
  uv run python scripts/run_replay.py --days 60 --profile rising --yes
```

Available profiles: `flat` (constant counts), `rising` (linearly increasing), `spiky` (periodic spikes).

See [docs/evaluation-plan.md](docs/evaluation-plan.md) for the full manual review checklist.

---

## Data Sources

| Source | Schedule | Notes |
|---|---|---|
| GitHub | every 6 h | Stars ≥ 50, configurable lookback window; token optional but recommended |
| Hacker News | every 6 h | Algolia search API, no auth required |
| Reddit | every 12 h | Public JSON API; `REDDIT_USER_AGENT` required by ToS |
| App Store | daily 07:00 UTC | Mock only — reads `data/mock/*.json`; real provider deferred to Phase 1.5 |

> [!NOTE]
> Reddit's `/new.json` cursor API caps at ~1 000 posts per subreddit. High-volume subs may only cover 7–10 days of history. This is logged as `oldest_item_age_days` and is expected behaviour.

---

## Known Limitations

- **Reddit 1 000-post ceiling** — high-volume subreddits like `r/startups` may backfill only 7–10 days of history.
- **App Store is mocked** — real App Store ingestion is deferred to Phase 1.5; install-proxy values are synthetic.
- **No Prophet / competition score** — growth uses a 7-day rolling slope; the Competition dimension is absent from Phase 1 scoring. Both are planned for Phase 1.5 once ≥ 30 days of real history are available.
- **Single-tenant** — all configured chat IDs share the same niche set and briefs. Per-user subscriptions are a Phase 2 feature.
- **SQLite only** — migrating to PostgreSQL requires changing `DATABASE_URL` and installing `asyncpg`; no other code changes needed.

---

## Links

- [Project document](devtrend-project-document.md) — full spec, design decisions, and roadmap
- [Architecture decisions](docs/decisions.md) — ADR-001 through ADR-008
- [Evaluation plan](docs/evaluation-plan.md) — manual review checklist for Phase 1
- [Roadmap](docs/roadmap.md) — Phase 1 → Phase 1.5 → Phase 2

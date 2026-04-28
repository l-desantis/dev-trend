# DevTrend

Multi-source agentic intelligence system that monitors developer-facing market signals across GitHub, Hacker News, Reddit, and app stores, then synthesises them into structured **opportunity briefs** — suggesting when and in which niche to launch a new app.

Delivered through a **Telegram bot** with daily push notifications and on-demand commands.

> **Example brief** (`/niche ai-habit-trackers`):
> AI-Powered Habit Trackers — Score 84 ↑
> Strong momentum this week: 3 new repos crossing 50 stars, HN thread at 200+ points, Reddit mentions up 40 %. Growth slope: +0.6. Key evidence: habit-tracker repo (github), "Show HN: AI streak coach" (hn).

---

## Quick Start

**Prerequisites:** Python 3.11+, `uv`, a Telegram bot token.

1. Copy and fill the env file:
   ```bash
   cp .env.example .env
   # Set TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS, and optionally GITHUB_TOKEN
   ```

2. Install dependencies and run DB migrations:
   ```bash
   uv sync
   uv run python -c "import asyncio; from app.db import init_db; asyncio.run(init_db())"
   ```

3. Start the Telegram bot (includes scheduler and ingestion):
   ```bash
   uv run python -m app.main
   ```

4. On first launch with an empty DB, a bulk backfill runs automatically (`BACKFILL_ON_EMPTY=true`). After ~2 minutes, send `/briefing` to the bot — you should see percentile-normalised scores immediately.

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

All settings are in `.env` (see `.env.example` for the full list). Key vars:

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Required. From @BotFather. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | — | Comma-separated chat IDs with access. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./devtrend.db` | SQLite path or async connection string. |
| `GITHUB_TOKEN` | — | Optional. Raises API limit from 60 → 5 000 req/h. |
| `REDDIT_USER_AGENT` | `DevTrend/1.0 (by /u/yourhandle)` | Required by Reddit ToS. Use your handle. |
| `LLM_PROVIDER` | `ollama` | `ollama` or `mock`. Mock skips LLM for dev/test. |
| `SPIKE_ALERT_THRESHOLD` | `15.0` | Score-delta that triggers a spike alert push. |
| `SOURCE_RETENTION_DAYS` | `90` | Prune source_items older than this. |
| `SIGNAL_RETENTION_DAYS` | `30` | Prune non-aggregate signals older than this. |
| `BACKFILL_ON_EMPTY` | `true` | Run historical backfill on first launch. |

---

## Running Tests

```bash
uv run pytest                   # full suite
uv run pytest tests/test_scoring.py -v
uv run pytest tests/test_pruning.py -v
uv run pytest -q                # quiet summary
```

Tests use an in-memory SQLite DB (`sqlite+aiosqlite:///:memory:`) — no setup required.

---

## Replay Harness

Seed synthetic history and replay daily scoring for evaluation without waiting for real ingestion:

```bash
# Use a separate replay DB (the script refuses to run against a production DB)
DATABASE_URL=sqlite+aiosqlite:///./devtrend-replay.db \
  uv run python scripts/run_replay.py --days 60 --profile rising --yes
```

Available profiles: `flat` (constant counts), `rising` (linearly increasing), `spiky` (periodic spikes).

See [docs/evaluation-plan.md](docs/evaluation-plan.md) for the full evaluation checklist.

---

## Data Sources

| Source | Schedule | Auth |
|---|---|---|
| GitHub | every 6 h | optional token (60 req/h anonymous) |
| Hacker News | every 6 h | none |
| Reddit | every 12 h | User-Agent header only |
| App Store (mock) | daily 07:00 UTC | none — reads `data/mock/*.json` |

### Reddit User-Agent

Reddit's public JSON API requires a descriptive `User-Agent`. Set it in `.env`:

```
REDDIT_USER_AGENT=DevTrend/1.0 (by /u/yourhandle)
```

This is required by Reddit's ToS. Use your actual Reddit username. Without it, requests return 429 or are blocked.

### GitHub Token

Optional but recommended — raises the rate limit from 60 to 5 000 req/h:

```
GITHUB_TOKEN=ghp_...
```

Without a token, the connector logs a warning and continues at the anonymous limit.

---

## Known Limitations

- **Reddit 1 000-post ceiling:** Reddit's `/new.json` cursor API caps at ~1 000 posts per subreddit. High-volume subs (`r/startups`) may only backfill 7–10 days of history. This is logged as `oldest_item_age_days` and is expected behaviour.
- **App Store is mocked:** Real App Store data ingestion is deferred to Phase 1.5. The current connector reads static JSON from `data/mock/`. Install-proxy values are synthetic.
- **Prophet / competition score deferred:** Growth uses a 7-day rolling slope (not Prophet). The Competition dimension is absent from Phase 1 scoring. Both are planned for Phase 1.5 when ≥ 30 days of real history and a live app-store provider are available.
- **Single-tenant:** All configured chat IDs share the same niche set and briefs. Per-user subscriptions are a Phase 2 feature.
- **SQLite only:** The async stack (aiosqlite + SQLAlchemy async) targets SQLite for Phase 1. Migrating to PostgreSQL requires only changing `DATABASE_URL` and installing `asyncpg`.

---

## Links

- [Project document](devtrend-project-document.md) — full spec, decisions, and roadmap
- [Architecture decisions](docs/decisions.md) — ADR log (ADR-001 through ADR-008)
- [Evaluation plan](docs/evaluation-plan.md) — manual review checklist
- [Roadmap](docs/roadmap.md) — Phase 1 → Phase 1.5 → Phase 2

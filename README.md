# DevTrend

Multi-source agentic intelligence system that monitors developer-facing market signals across GitHub, Hacker News, Reddit, and app stores, then synthesises them into structured **opportunity briefs** — suggesting when and in which niche to launch a new app.

Delivered through a **Telegram bot** with daily push notifications and on-demand commands.

## Quick links

- [Project document](devtrend-project-document.md) — full spec, decisions, and roadmap
- [Architecture decisions](docs/decisions.md) — ADR log
- [Roadmap](docs/roadmap.md) — Phase 1 → Phase 1.5 → Phase 2

## Data Sources

DevTrend ingests from four sources:

| Source | Schedule | Auth |
|--------|----------|------|
| GitHub | every 6h | optional token (60 req/h anonymous) |
| Hacker News | every 6h | none |
| Reddit | every 12h | User-Agent header only |
| App Store (mock) | daily 07:00 | none — reads `data/mock/*.json` |

### Reddit

Reddit's public JSON API requires a descriptive `User-Agent`. Set it in `.env`:

```
REDDIT_USER_AGENT=DevTrend/1.0 (by /u/yourhandle)
```

This is required by Reddit's ToS. Use your actual Reddit username.

### GitHub

A token is optional but raises the rate limit from 60 to 5,000 req/h:

```
GITHUB_TOKEN=ghp_...
```

Without a token, the connector logs a warning and continues — 1 page per 6 hours fits comfortably within the anonymous limit.

### Running ingestion manually

```bash
# Verify mock data files
python -m scripts.seed_mock_data

# Smoke-test a single source (no network needed for appstore)
python -m scripts.run_ingestion --source appstore

# Run all sources
python -m scripts.run_ingestion --source all

# Check results
sqlite3 devtrend.db \
  "SELECT source_type, COUNT(*), COUNT(niche_id) AS attached FROM source_items GROUP BY source_type;"
```

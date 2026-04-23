# DevTrend — Skeleton Design Spec

**Date:** 2026-04-23
**Status:** Approved
**Scope:** Phase 1 skeleton — directory structure, empty Python files, `pyproject.toml`, `.env.example`. No business logic.

---

## Goal

Create the full repository skeleton for the DevTrend application as defined in the project document v3.0. All Python files are empty stubs (`pass` / minimal stub). No business logic is implemented.

---

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Skeleton depth | Empty files (Option A) | User preference — no signatures, no logic |
| `__init__.py` | Yes, in every package | Importability from day one |
| Package manager | `uv` + `pyproject.toml` | Modern, fast, drop-in over requirements.txt |
| Entrypoint | `devtrend = "app.main:main"` (Option C) | `uv run devtrend` works immediately |

---

## Directory Structure

```
devtrend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # def main(): pass
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── schemas.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── github_connector.py
│   │   ├── hn_connector.py
│   │   ├── reddit_connector.py
│   │   ├── appstore_mock_connector.py
│   │   └── scheduler.py
│   ├── features/
│   │   ├── __init__.py
│   │   ├── trend_features.py
│   │   ├── sentiment_features.py
│   │   └── niche_builder.py
│   ├── forecasting/
│   │   ├── __init__.py
│   │   └── scoring.py
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── state.py
│   │   ├── graph.py
│   │   ├── nodes.py
│   │   └── prompts.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── retriever.py
│   │   ├── forecaster.py
│   │   ├── reporter.py
│   │   └── source_inspector.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── mock_adapter.py
│   │   ├── ollama_adapter.py
│   │   ├── openai_adapter.py
│   │   └── anthropic_adapter.py
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── bot.py
│   │   ├── middleware.py
│   │   ├── handlers.py
│   │   ├── notifications.py
│   │   ├── formatter.py
│   │   └── scheduler_hooks.py
│   └── api/
│       ├── __init__.py
│       ├── routes_health.py
│       └── routes_internal.py
├── data/
│   ├── niches.yaml              # already exists
│   └── mock/
│       └── .gitkeep
├── docs/
│   ├── decisions.md             # already exists
│   ├── roadmap.md               # already exists
│   ├── architecture.md          # empty placeholder
│   └── evaluation-plan.md      # empty placeholder
├── scripts/
│   ├── seed_mock_data.py
│   ├── run_ingestion.py
│   └── run_forecasts.py
├── tests/
│   ├── __init__.py
│   ├── test_connectors.py
│   ├── test_scoring.py
│   ├── test_agent_graph.py
│   └── test_bot_handlers.py
├── pyproject.toml
├── .env.example
├── README.md                    # already exists
└── .gitignore                   # already exists
```

---

## `pyproject.toml`

```toml
[project]
name = "devtrend"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "python-telegram-bot>=20.0",
    "langgraph>=0.1",
    "langchain-core>=0.2",
    "apscheduler>=3.10",
    "sqlalchemy>=2.0",
    "aiosqlite>=0.20",
    "httpx>=0.27",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",
    "ollama>=0.2",
    "numpy>=1.26",
    "scipy>=1.13",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
    "mypy>=1.10",
]

[project.scripts]
devtrend = "app.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff.lint]
select = ["E", "F", "I"]
```

---

## `.env.example`

```env
APP_NAME=DevTrend
ENV=dev

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ALLOWED_CHAT_IDS=

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5

DATABASE_URL=sqlite:///./devtrend.db

GITHUB_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=DevTrend/1.0 (by /u/yourhandle)
ENABLE_MOCK_APPSTORE=true

DAILY_DIGEST_TIME=08:00
SPIKE_ALERT_THRESHOLD=15

LOG_LEVEL=INFO
```

---

## What is NOT in scope

- Any business logic, class bodies, function implementations
- Database migrations or alembic setup
- Docker / CI configuration
- Any file already present in the repo (will not be overwritten)

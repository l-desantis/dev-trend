# AGENTS.md

DevTrend — async Python service that ingests dev-community signals, scores
trends with an LLM pipeline and reports via FastAPI + a Telegram bot.

## Stack

Python 3.11+, uv, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, APScheduler,
Ollama/OpenAI adapters, pytest, ruff, mypy, Docker Compose (postgres → migrate → app).

## Layout

- `app/` — package. `pipeline/` orchestration, `ingestion/` connectors,
  `llm/` providers, `scoring/`, `features/`, `api/`, `bot/`, `maintenance/`,
  `models.py`, `schemas.py`, `config.py`, `db.py`, `main.py`
- `alembic/` — migrations · `tests/` — pytest · `scripts/` — one-off CLIs
- `docs/superpowers/plans/` — implementation plans

## Commands

```bash
uv sync                                # install
docker compose up -d postgres          # local DB
uv run alembic upgrade head            # migrate
uv run uvicorn app.main:app --reload   # run
uv run pytest                          # unit tests (LLM_PROVIDER=mock)
uv run pytest -m integration           # slow e2e
uv run ruff check app/ tests/
uv run mypy app/
```

## Conventions

- Async end to end; no sync DB calls in request or pipeline paths.
- Config via `app/config.py` (pydantic-settings) — read env there, not inline.
- Secrets: `.env` / SOPS. Never commit `.env`, `age.key` or decrypted secrets.
- Schema changes need an Alembic migration in the same change.
- Tests default to the `mock` LLM provider — keep them offline and deterministic.
- Before finishing: `uv run pytest` plus `ruff check` on touched paths.
- Commits: no `Co-authored-by` trailer for Copilot. Only add one when Claude
  wrote the change.

## Notes

Root-level `*_analysis.md`, `20??-*.txt` and `check_today.py` are scratch —
do not stage them.

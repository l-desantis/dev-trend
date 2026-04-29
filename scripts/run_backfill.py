"""Provider-independent backfill CLI.

Usage:
    uv run python -m scripts.run_backfill --history-days 30 --llm-provider ollama
    uv run python -m scripts.run_backfill --history-days 30 --llm-provider mock --db-url sqlite:///./test.db
"""
import argparse
import asyncio
import json
import os
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run v4 backfill pipeline")
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--llm-provider", default=None, choices=["ollama", "mock", "nim"])
    parser.add_argument("--embedding-provider", default=None, choices=["ollama", "mock", "nim"])
    parser.add_argument("--db-url", default=None)
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> None:
    # Apply overrides before importing Settings
    if args.llm_provider:
        os.environ["LLM_PROVIDER"] = args.llm_provider
    if args.embedding_provider:
        os.environ["EMBEDDING_PROVIDER"] = args.embedding_provider
    if args.db_url:
        os.environ["DATABASE_URL"] = args.db_url

    from app.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    from app.db import init_db, reset_engine
    reset_engine()
    await init_db()

    from app.llm.factory import make_embedding_adapter, make_llm_adapter
    llm = make_llm_adapter(settings)
    embedder = make_embedding_adapter(settings)

    from app.ingestion.base import ConnectorRunRegistry
    from app.ingestion.github_connector import GithubConnector
    from app.ingestion.hn_connector import HNConnector
    from app.ingestion.reddit_connector import RedditConnector
    import httpx

    client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s)
    registry = ConnectorRunRegistry()
    connectors = [
        GithubConnector(client, registry),
        HNConnector(client, registry),
        RedditConnector(client, registry),
    ]

    from app.ingestion.backfill import bulk_backfill
    report = await bulk_backfill(
        connectors, llm, embedder, settings, history_days=args.history_days
    )
    await client.aclose()

    print(json.dumps({"backfill_report": report.to_dict()}))
    return report


def main(argv: list[str] | None = None):
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    main()

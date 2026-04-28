#!/usr/bin/env python3
"""Manual ingestion runner.

Usage:
  python -m scripts.run_ingestion --source <name|all>
  python -m scripts.run_ingestion --backfill-days 30
"""
import argparse
import asyncio
from pathlib import Path


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["github", "hn", "reddit", "appstore", "all"], default="all")
    parser.add_argument("--backfill-days", type=int, default=0,
                        help="Run bulk backfill for N days of history (skips --source)")
    args = parser.parse_args()

    from app.config import get_settings
    from app.db import init_db
    from app.features.niche_builder import NicheMatcher, sync_niches_from_yaml
    from app.ingestion.appstore_mock_connector import AppStoreMockConnector
    from app.ingestion.base import ConnectorRunRegistry
    from app.ingestion.github_connector import GithubConnector
    from app.ingestion.hn_connector import HNConnector
    from app.ingestion.reddit_connector import RedditConnector
    import httpx

    settings = get_settings()
    await init_db()
    await sync_niches_from_yaml(Path("data/niches.yaml"))

    client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s)
    registry = ConnectorRunRegistry()
    matcher = await NicheMatcher.from_db()

    all_connectors = {
        "github": GithubConnector(client, matcher, registry),
        "hn": HNConnector(client, matcher, registry),
        "reddit": RedditConnector(client, matcher, registry),
        "appstore": AppStoreMockConnector(client, matcher, registry),
    }

    if args.backfill_days > 0:
        from app.ingestion.backfill import bulk_backfill
        from app.llm.mock_adapter import MockLLMAdapter
        from app.llm.ollama_adapter import OllamaAdapter

        adapter = (
            OllamaAdapter(base_url=settings.ollama_base_url, model=settings.ollama_model)
            if settings.llm_provider == "ollama"
            else MockLLMAdapter()
        )
        connectors = list(all_connectors.values())
        print(f"\nStarting bulk backfill for {args.backfill_days} days...")
        report = await bulk_backfill(connectors, adapter, history_days=args.backfill_days)
        print(f"\nBackfill complete:")
        print(f"  items_per_source: {report.items_per_source}")
        print(f"  signal_rows:      {report.signal_rows}")
        print(f"  scores_written:   {report.scores_written}")
        print(f"  briefs_generated: {report.briefs_generated}")
        print(f"  duration_s:       {report.duration_s:.1f}")
    else:
        targets = list(all_connectors.values()) if args.source == "all" else [all_connectors[args.source]]
        for connector in targets:
            print(f"\nRunning {connector.source_type}...")
            status = await connector.run()
            print(f"  status={status.last_status} items={status.items_ingested} duration={status.duration_s:.1f}s")
            if status.error:
                print(f"  error: {status.error}")

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

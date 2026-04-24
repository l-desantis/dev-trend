#!/usr/bin/env python3
"""Manual ingestion runner. Usage: python -m scripts.run_ingestion --source <name|all>"""
import argparse
import asyncio
from pathlib import Path


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["github", "hn", "reddit", "appstore", "all"], default="all")
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

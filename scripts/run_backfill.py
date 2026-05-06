"""Provider-independent backfill CLI.

Usage:
    uv run python -m scripts.run_backfill --history-days 30 --llm-provider ollama
    uv run python -m scripts.run_backfill --history-days 30 --llm-provider mock --db-url sqlite:///./test.db
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
import traceback


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run v4 backfill pipeline")
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--llm-provider", default=None, choices=["ollama", "mock", "nim"])
    parser.add_argument("--embedding-provider", default=None, choices=["ollama", "mock", "nim"])
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--max-extraction-items", type=int, default=None, metavar="N",
                        help="Cap LLM extraction to the first N source items (useful for testing)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show DEBUG-level logs")
    return parser.parse_args(argv)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Quieten noisy libraries unless verbose
    if not verbose:
        for noisy in ("httpx", "httpcore", "hpack"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def _section(title: str) -> None:
    print(f"\n{'─' * 60}", file=sys.stderr, flush=True)
    print(f"  {title}", file=sys.stderr, flush=True)
    print(f"{'─' * 60}", file=sys.stderr, flush=True)


async def _run(args: argparse.Namespace) -> None:
    t0 = time.monotonic()
    log = logging.getLogger("run_backfill")

    # ── 1. Apply env overrides ───────────────────────────────────────────────
    _section("1/6  Environment & settings")
    if args.llm_provider:
        os.environ["LLM_PROVIDER"] = args.llm_provider
        log.debug("override LLM_PROVIDER=%s", args.llm_provider)
    if args.embedding_provider:
        os.environ["EMBEDDING_PROVIDER"] = args.embedding_provider
        log.debug("override EMBEDDING_PROVIDER=%s", args.embedding_provider)
    if args.db_url:
        os.environ["DATABASE_URL"] = args.db_url
        log.debug("override DATABASE_URL=%s", args.db_url)

    from app.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()

    log.info(
        "settings loaded  llm=%s  embedding=%s  db=%s  specificity_gate=%s",
        settings.llm_provider,
        settings.embedding_provider,
        settings.database_url,
        settings.specificity_gate,
    )

    # ── 2. DB init ───────────────────────────────────────────────────────────
    _section("2/6  Database init")
    from app.db import init_db, reset_engine
    reset_engine()
    log.info("initialising database …")
    try:
        await init_db()
        log.info("database ready")
    except Exception:
        log.error("init_db failed\n%s", traceback.format_exc())
        sys.exit(1)

    # ── 3. Adapters ──────────────────────────────────────────────────────────
    _section("3/6  LLM / embedding adapters")
    from app.llm.factory import make_embedding_adapter, make_llm_adapter
    try:
        llm = make_llm_adapter(settings)
        log.info("LLM adapter: %s", type(llm).__name__)
    except Exception:
        log.error("make_llm_adapter failed\n%s", traceback.format_exc())
        sys.exit(1)

    try:
        embedder = make_embedding_adapter(settings)
        log.info("embedding adapter: %s", type(embedder).__name__)
    except Exception:
        log.error("make_embedding_adapter failed\n%s", traceback.format_exc())
        sys.exit(1)

    # ── 4. Connectors ────────────────────────────────────────────────────────
    _section("4/6  Connector setup")
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
    log.info(
        "connectors: %s  http_timeout=%ss  history_days=%s",
        [c.source_type for c in connectors],
        settings.ingestion_http_timeout_s,
        args.history_days,
    )

    # ── 5. Backfill ──────────────────────────────────────────────────────────
    _section("5/6  Bulk backfill (ingestion + pipeline)")
    from app.ingestion.backfill import bulk_backfill

    try:
        t_backfill = time.monotonic()
        report = await bulk_backfill(
            connectors, llm, embedder, settings,
            history_days=args.history_days,
            extraction_limit=args.max_extraction_items,
        )
        elapsed_backfill = time.monotonic() - t_backfill
        log.info("bulk_backfill finished in %.1fs", elapsed_backfill)
    except Exception:
        log.error("bulk_backfill raised an uncaught exception\n%s", traceback.format_exc())
        await client.aclose()
        sys.exit(1)
    finally:
        await client.aclose()

    # ── 6. Summary ───────────────────────────────────────────────────────────
    _section("6/6  Summary")
    result = report.to_dict()
    total_items = sum(report.items_per_source.values())
    log.info(
        "items ingested: %d  (per source: %s)",
        total_items,
        report.items_per_source,
    )
    log.info(
        "pipeline:  pain_points=%d  candidates=%d  labelled=%d",
        report.painpoints_created,
        report.candidates_created,
        report.labelled,
    )
    log.info("total wall-clock: %.1fs", time.monotonic() - t0)

    print(json.dumps({"backfill_report": result}))
    return report


def main(argv: list[str] | None = None):
    args = _parse_args(argv)
    _setup_logging(args.verbose)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    main()

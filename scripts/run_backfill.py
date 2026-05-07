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
from typing import Any


class _BackfillProgress:
    """Rich Live progress display driven by structlog events."""

    def __init__(self) -> None:
        from rich.console import Console
        from rich.live import Live
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )

        self.console = Console(stderr=True)
        self._p = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description:<22}"),
            BarColumn(bar_width=28),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TextColumn("[dim]{task.fields[extra]}"),
            console=self.console,
        )
        self.live = Live(self._p, console=self.console, refresh_per_second=4)

        self._t_conn = self._p.add_task("Connectors", total=3, extra="")
        self._t_extract = self._p.add_task("Extraction", total=None, extra="")
        self._t_embed = self._p.add_task("Embedding", total=None, extra="")
        self._t_identity = self._p.add_task("Identity", total=1, extra="")
        self._t_cluster = self._p.add_task("Clustering", total=1, extra="")
        self._t_label = self._p.add_task("Labelling", total=None, extra="")

        self._t_github: int | None = None
        self._t_hn: int | None = None
        self._source_items: dict[str, int] = {}

    def processor(self, logger: Any, method_name: str, event_dict: dict) -> dict:
        event = event_dict.get("event", "")

        if event == "backfill_window_done":
            src = event_dict.get("source_type", "")
            items = event_dict.get("items", 0)
            self._source_items[src] = self._source_items.get(src, 0) + items
            if src == "github":
                if self._t_github is None:
                    self._t_github = self._p.add_task("  └ github", total=None, extra="")
                self._p.advance(self._t_github)
                self._p.update(self._t_github, extra=f"items={self._source_items[src]}")
            elif src == "hn":
                if self._t_hn is None:
                    self._t_hn = self._p.add_task("  └ hn", total=None, extra="")
                self._p.advance(self._t_hn)
                self._p.update(self._t_hn, extra=f"items={self._source_items[src]}")

        elif event == "backfill_connector_done":
            src = event_dict.get("source_type", "")
            items = event_dict.get("items_total", 0)
            self._source_items[src] = items
            self._p.advance(self._t_conn)
            self._p.update(self._t_conn, extra=f"items={sum(self._source_items.values())}")
            if src == "github" and self._t_github is not None:
                done = int(self._p.tasks[self._t_github].completed)
                self._p.update(self._t_github, total=done, completed=done)
            elif src == "hn" and self._t_hn is not None:
                done = int(self._p.tasks[self._t_hn].completed)
                self._p.update(self._t_hn, total=done, completed=done)

        elif event == "extraction_start":
            self._p.update(self._t_extract, total=event_dict.get("total_rows", 0))

        elif event == "extraction_checkpoint":
            processed = event_dict.get("processed_so_far", 0)
            pp = event_dict.get("painpoints_so_far", 0)
            self._p.update(self._t_extract, completed=processed, extra=f"pp={pp}")

        elif event == "extraction_complete":
            task = self._p.tasks[self._t_extract]
            n = int(task.total or task.completed)
            self._p.update(self._t_extract, total=n, completed=n)

        elif event == "embedding_complete":
            n = event_dict.get("processed", 0)
            self._p.update(self._t_embed, total=n, completed=n)

        elif event == "identity_resolution_complete":
            attached = event_dict.get("attached", 0)
            self._p.update(self._t_identity, completed=1, extra=f"attached={attached}")

        elif event == "clustering_complete":
            cands = event_dict.get("candidates_created", 0)
            self._p.update(self._t_cluster, completed=1, extra=f"cands={cands}")

        elif event == "labelling_start":
            self._p.update(self._t_label, total=event_dict.get("unlabelled_found", 0))

        elif event == "labelling_progress":
            self._p.advance(self._t_label)

        elif event == "labelling_complete":
            task = self._p.tasks[self._t_label]
            n = int(task.total or task.completed)
            self._p.update(self._t_label, total=n, completed=n)

        return event_dict


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


def _setup_logging(verbose: bool) -> _BackfillProgress:
    import structlog
    import structlog.stdlib
    from rich.logging import RichHandler

    progress = _BackfillProgress()
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(
            console=progress.console,
            show_time=False,
            show_path=False,
            show_level=False,
            markup=False,
            highlight=False,
        )],
        force=True,
    )
    structlog.configure(
        processors=[
            progress.processor,
            structlog.stdlib.filter_by_level,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    if not verbose:
        for noisy in ("httpx", "httpcore", "hpack"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    return progress


def _section(title: str, console: Any = None) -> None:
    bar = "─" * 60
    if console is not None:
        console.print(f"\n{bar}\n  {title}\n{bar}")
    else:
        print(f"\n{bar}", file=sys.stderr, flush=True)
        print(f"  {title}", file=sys.stderr, flush=True)
        print(bar, file=sys.stderr, flush=True)


async def _run(args: argparse.Namespace, progress: _BackfillProgress) -> None:
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

    # ── 4. Connectors + 5. Backfill (live progress block) ───────────────────
    progress.live.start()
    try:
        _section("4/6  Connector setup", progress.console)
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

        _section("5/6  Bulk backfill (ingestion + pipeline)", progress.console)
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
    finally:
        progress.live.stop()

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
    progress = _setup_logging(args.verbose)
    return asyncio.run(_run(args, progress))


if __name__ == "__main__":
    main()

"""Manual job trigger CLI — fires any scheduled job once, outside of cron.

Usage:
    uv run python -m scripts.run_job --list
    uv run python -m scripts.run_job --job weekly_pruning
    uv run python -m scripts.run_job --job hn_ingestion --llm-provider mock

VPS (inside the running container):
    docker compose exec app python -m scripts.run_job --list
    docker compose exec app python -m scripts.run_job --job weekly_pruning
"""
import argparse
import asyncio
import logging
import os
import re
import sys
import time
import traceback

JOB_IDS = [
    "github_ingestion",
    "hn_ingestion",
    "reddit_ingestion",
    "playstore_ingestion",
    "playstore_app_discovery",
    "ios_rss_ingestion",
    "daily_pipeline",
    "daily_scoring",
    "daily_digest",
    "weekly_pruning",
    "weekly_recluster",
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manually trigger a scheduled job once (no scheduler.start())."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        dest="list_jobs",
        action="store_true",
        help="Print all registered job IDs with their triggers, then exit 0.",
    )
    group.add_argument(
        "--job",
        choices=JOB_IDS,
        metavar="JOB_ID",
        help=f"Job to run. One of: {', '.join(JOB_IDS)}",
    )
    parser.add_argument("--db-url", default=None, help="Override DATABASE_URL")
    parser.add_argument(
        "--llm-provider", default=None, choices=["ollama", "mock", "nim", "openai"]
    )
    parser.add_argument(
        "--embedding-provider", default=None, choices=["ollama", "mock", "nim", "openai"]
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Show DEBUG-level logs")
    return parser.parse_args(argv)


class _PrintBot:
    """Stub bot: prints what would be sent to Telegram instead of actually sending it.

    Passed to build_scheduler() so bot-dependent jobs (daily_digest, daily_scoring)
    actually execute their message-building logic and show their output locally.
    """

    _MDV2_ESCAPE = re.compile(r'\\([_*\[\]()~`>#+\-=|{}.!])')

    def _render(self, text: str) -> str:
        return self._MDV2_ESCAPE.sub(r'\1', text)

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **_):
        border = "─" * 64
        print(f"\n{border}", flush=True)
        print(f"  [Telegram → chat_id={chat_id}]", flush=True)
        print(border, flush=True)
        print(self._render(text), flush=True)
        print(border, flush=True)


def _setup_logging(verbose: bool) -> None:
    import structlog
    import structlog.stdlib

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stderr,
        force=True,
    )
    structlog.configure(
        processors=[
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


async def _run(args: argparse.Namespace) -> None:
    log = logging.getLogger("run_job")

    # ── 1. Apply env overrides ───────────────────────────────────────────────
    if args.llm_provider:
        os.environ["LLM_PROVIDER"] = args.llm_provider
    if args.embedding_provider:
        os.environ["EMBEDDING_PROVIDER"] = args.embedding_provider
    if args.db_url:
        os.environ["DATABASE_URL"] = args.db_url

    from app.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    log.info(
        "settings loaded  llm=%s  embedding=%s  db=%s",
        settings.llm_provider,
        settings.embedding_provider,
        settings.database_url,
    )

    # ── 2. DB init (skipped for --list, which needs no DB connection) ────────
    if not args.list_jobs:
        from app.db import check_db_reachable, reset_engine
        reset_engine()
        log.info("checking database reachability …")
        try:
            await check_db_reachable()
        except Exception:
            log.error("check_db_reachable failed\n%s", traceback.format_exc())
            sys.exit(1)
        log.info("database ready")

    # ── 3. Connectors + scheduler ─────────────────────────────────────────────
    import httpx
    from app.ingestion.base import ConnectorRunRegistry
    from app.ingestion.github_connector import GithubConnector
    from app.ingestion.hn_connector import HNConnector
    from app.ingestion.reddit_connector import RedditConnector
    from app.ingestion.scheduler import build_scheduler

    client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s)
    registry = ConnectorRunRegistry()
    connectors = [
        GithubConnector(client, registry),
        HNConnector(client, registry),
        RedditConnector(client, registry),
    ]
    scheduler = build_scheduler(connectors, registry, settings, bot=_PrintBot())

    try:
        # ── 4. List jobs ──────────────────────────────────────────────────────
        if args.list_jobs:
            for job in scheduler.get_jobs():
                print(f"{job.id:<28} {job.trigger}")
            return

        # ── 5. Trigger one job ────────────────────────────────────────────────
        job = scheduler.get_job(args.job)
        if job is None:
            log.error(
                "Job '%s' is not registered — it may be disabled by a feature flag "
                "(e.g. ENABLE_IOS_RSS=false).",
                args.job,
            )
            sys.exit(1)

        log.info("triggering job  job_id=%s", args.job)
        t0 = time.monotonic()
        await job.func()
        elapsed = time.monotonic() - t0
        log.info("job complete  job_id=%s  elapsed_s=%.2f", args.job, elapsed)

    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    _setup_logging(args.verbose)
    try:
        asyncio.run(_run(args))
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("run_job").error("Unhandled exception:\n%s", traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()

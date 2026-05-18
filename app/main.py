import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import httpx
import structlog
import structlog.stdlib
from fastapi import FastAPI

from app.api.routes_health import router as health_router
from app.config import get_settings
from app.db import check_db_reachable, get_session


def _configure_logging() -> None:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso")

    # Processors applied to records that did NOT originate from structlog
    # (uvicorn, apscheduler, telegram, etc.). They turn a stdlib LogRecord
    # into the same event_dict shape structlog produces, so the final
    # JSONRenderer can format both kinds identically.
    foreign_pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=foreign_pre_chain,
    )

    handler = logging.StreamHandler()  # stderr by default — Docker captures it.
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Idempotent: wipe any prior handlers (basicConfig, pytest, prior call)
    # so we never double-log.
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(log_level)

    # Silence noisy third-party loggers so app.* events stay readable.
    # httpx also logs outbound URLs which include the Telegram bot token.
    for name in ("uvicorn.access", "httpx", "httpcore", "telegram", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            timestamper,
            # Hand the event_dict to ProcessorFormatter, which finishes the
            # rendering via the JSONRenderer above. Single render path for
            # both stdlib- and structlog-originated records.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _configure_logging()
    log = structlog.get_logger("main")
    settings = get_settings()

    await check_db_reachable()
    log.info("Database reachable", component="main")

    from app.db_helpers.categories import sync_categories_from_yaml
    async with get_session() as session:
        await sync_categories_from_yaml(session, path=Path("data/categories.yaml"))
    log.info("Categories synced", component="main")

    from app.ingestion.base import ConnectorRunRegistry
    from app.ingestion.github_connector import GithubConnector
    from app.ingestion.hn_connector import HNConnector
    from app.ingestion.reddit_connector import RedditConnector

    http_client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s)
    registry = ConnectorRunRegistry()

    connectors = [
        GithubConnector(http_client, registry),
        HNConnector(http_client, registry),
        RedditConnector(http_client, registry),
    ]

    bot_app = None
    if settings.telegram_bot_token:
        from telegram import BotCommand
        from app.bot.bot import build_application
        bot_app = build_application()
        bot_app.bot_data["run_registry"] = registry
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.bot.set_my_commands([
            BotCommand("start",         "Welcome + commands"),
            BotCommand("help",          "Show command list"),
            BotCommand("opportunities", "Top opportunities right now"),
            BotCommand("opportunity",   "Full scorecard for an opportunity"),
            BotCommand("categories",    "Overview by category"),
            BotCommand("category",      "Filter by category slug"),
            BotCommand("emerging",      "Newly-discovered opportunities"),
            BotCommand("sources",       "Last ingestion status per source"),
        ])
        if bot_app.updater is not None:
            await bot_app.updater.start_polling()
        log.info("Telegram bot started", component="main")
    else:
        log.warning("TELEGRAM_BOT_TOKEN not set — bot disabled", component="main")

    # Bulk backfill: runs once if DB is empty and BACKFILL_ON_EMPTY=true
    if settings.backfill_on_empty:
        from sqlalchemy import select as _select
        from app.models import SourceItem as _SourceItem
        async with get_session() as _session:
            _row = await _session.execute(_select(_SourceItem.id).limit(1))
            db_empty = _row.first() is None
        if db_empty:
            log.info("db_empty_starting_backfill", component="main")
            from app.ingestion.backfill import bulk_backfill
            from app.llm.factory import make_embedding_adapter, make_llm_adapter
            _llm = make_llm_adapter(settings)
            _embedder = make_embedding_adapter(settings)
            _report = await bulk_backfill(
                connectors, _llm, _embedder, settings,
                history_days=settings.backfill_history_days,
            )
            log.info("bulk_backfill_complete", component="main", **_report.to_dict())
        else:
            log.info("db_not_empty_skip_backfill", component="main")

    from app.ingestion.scheduler import build_scheduler
    scheduler = build_scheduler(
        connectors, registry, settings,
        bot=(bot_app.bot if bot_app else None),
    )
    scheduler.start()
    log.info("Scheduler started", component="main")

    yield

    scheduler.shutdown(wait=False)
    if bot_app is not None:
        if bot_app.updater is not None:
            await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        log.info("Telegram bot stopped", component="main")
    await http_client.aclose()


app = FastAPI(title="DevTrend", version=get_settings().version, lifespan=lifespan)
app.include_router(health_router)


def main() -> None:
    import uvicorn

    # Configure logging BEFORE uvicorn boots so our root handler is in place
    # when uvicorn imports the app (and so `log_config=None` below doesn't
    # leave us briefly with no handlers at all).
    _configure_logging()

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        # Don't let uvicorn install its own dictConfig — it would clobber
        # the ProcessorFormatter handler we just set up on the root logger.
        log_config=None,
        # Access logs are noisy and we already pin uvicorn.access to WARNING;
        # turning the access logger off entirely is the cleanest signal.
        access_log=False,
    )

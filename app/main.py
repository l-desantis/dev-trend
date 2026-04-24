from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
import structlog.stdlib
from fastapi import FastAPI

from app.api.routes_health import router as health_router
from app.config import get_settings
from app.db import init_db


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _configure_logging()
    log = structlog.get_logger("main")

    settings = get_settings()
    await init_db()
    log.info("Database initialised", component="main")

    bot_app = None
    if settings.telegram_bot_token:
        from app.bot.bot import build_application

        bot_app = build_application()
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling()
        log.info("Telegram bot started", component="main")
    else:
        log.warning("TELEGRAM_BOT_TOKEN not set — bot disabled", component="main")

    yield

    if bot_app is not None:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        log.info("Telegram bot stopped", component="main")


app = FastAPI(title="DevTrend", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)

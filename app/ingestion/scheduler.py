import asyncio
from datetime import UTC, datetime

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from sqlalchemy import select

from app.agents.graph import run_brief_for_niche
from app.config import Settings
from app.db import get_session
from app.features.signal_aggregator import aggregate_daily_signals
from app.forecasting.scoring import score_all_niches
from app.ingestion.base import BaseConnector, ConnectorRunRegistry
from app.llm.base import LLMAdapter
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.ollama_adapter import OllamaAdapter
from app.models import Niche

log = structlog.get_logger(__name__)


def _select_adapter(settings: Settings) -> LLMAdapter:
    if settings.llm_provider == "ollama":
        return OllamaAdapter(base_url=settings.ollama_base_url, model=settings.ollama_model)
    return MockLLMAdapter()


def build_scheduler(
    connectors: list[BaseConnector],
    registry: ConnectorRunRegistry,
    settings: Settings,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    connector_map = {c.source_type: c for c in connectors}

    def _make_job(source_type: str):
        async def _job():
            connector = connector_map[source_type]
            try:
                await asyncio.wait_for(connector.run(), timeout=settings.ingestion_job_timeout_s)
            except asyncio.TimeoutError:
                registry.mark_error(source_type, "job timed out", settings.ingestion_job_timeout_s)
                log.error("Ingestion job timed out", source_type=source_type)
            except Exception as exc:
                log.error("Ingestion job crashed", source_type=source_type, error=str(exc))
        return _job

    scheduler.add_job(_make_job("github"), IntervalTrigger(hours=6), id="ingest_github", max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_make_job("hn"), IntervalTrigger(hours=6), id="ingest_hn", max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_make_job("reddit"), IntervalTrigger(hours=12), id="ingest_reddit", max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_make_job("appstore"), CronTrigger(hour=7, minute=0), id="ingest_appstore", max_instances=1, coalesce=True, misfire_grace_time=300)

    async def _scoring_job():
        now = datetime.now(UTC)
        try:
            rows = await aggregate_daily_signals(now)
            niches = await score_all_niches(now)
            log.info(
                "Daily scoring complete",
                component="scheduler",
                signal_rows=rows,
                niches_scored=niches,
            )
        except Exception as exc:
            log.error("Daily scoring failed", component="scheduler", error=str(exc))

    scheduler.add_job(
        _scoring_job,
        CronTrigger(hour=settings.scoring_cron_hour, minute=settings.scoring_cron_minute),
        id="daily_scoring",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    async def _brief_job():
        adapter = _select_adapter(settings)
        try:
            async with get_session() as session:
                niche_ids = (await session.execute(select(Niche.id))).scalars().all()
            generated = 0
            for nid in niche_ids:
                try:
                    if await run_brief_for_niche(nid, adapter, triggered_by="scheduler"):
                        generated += 1
                except Exception as exc:
                    log.error(
                        "Brief job: niche failed",
                        component="scheduler",
                        niche_id=nid,
                        error=str(exc),
                    )
            log.info(
                "Daily brief generation complete",
                component="scheduler",
                niches_total=len(niche_ids),
                briefs_generated=generated,
            )
        except Exception as exc:
            log.error("Daily brief generation failed", component="scheduler", error=str(exc))

    scheduler.add_job(
        _brief_job,
        CronTrigger(hour=settings.brief_cron_hour, minute=settings.brief_cron_minute),
        id="daily_brief_generation",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    log.info(
        "Scheduler built",
        component="scheduler",
        jobs=list(connector_map.keys()) + ["daily_scoring", "daily_brief_generation"],
    )
    return scheduler

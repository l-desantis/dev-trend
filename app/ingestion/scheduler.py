import asyncio
from datetime import UTC, datetime, timedelta

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
from app.maintenance.pruning import prune_old_data
from app.models import MaintenanceState, Niche

_STALE_PRUNING_THRESHOLD_DAYS = 10

log = structlog.get_logger(__name__)


def _select_adapter(settings: Settings) -> LLMAdapter:
    if settings.llm_provider == "ollama":
        return OllamaAdapter(base_url=settings.ollama_base_url, model=settings.ollama_model)
    return MockLLMAdapter()


def build_scheduler(
    connectors: list[BaseConnector],
    registry: ConnectorRunRegistry,
    settings: Settings,
    *,
    bot=None,
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
            async with get_session() as session:
                state = (await session.execute(select(MaintenanceState))).scalar_one_or_none()
            if state is None or state.last_pruned_at is None:
                log.warning("pruning_never_run", component="scheduler")
            else:
                last = state.last_pruned_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if (now - last) > timedelta(days=_STALE_PRUNING_THRESHOLD_DAYS):
                    log.warning(
                        "pruning_stale",
                        component="scheduler",
                        last_pruned_at=last.isoformat(),
                        days_since=round((now - last).days),
                    )
        except Exception as exc:
            log.warning("pruning_stale_check_failed", component="scheduler", error=str(exc))

        try:
            rows = await aggregate_daily_signals(now)
            niches = await score_all_niches(now)
            log.info(
                "Daily scoring complete",
                component="scheduler",
                signal_rows=rows,
                niches_scored=niches,
            )
            from app.bot.scheduler_hooks import push_spike_alerts
            await push_spike_alerts(bot, as_of=now)
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

    async def _digest_job() -> None:
        from app.bot.scheduler_hooks import push_daily_digest
        log.info("digest_job_start", component="scheduler")
        await push_daily_digest(bot)
        log.info("digest_job_done", component="scheduler")

    scheduler.add_job(
        _digest_job,
        CronTrigger(
            hour=settings.digest_cron_hour,
            minute=settings.digest_cron_minute,
        ),
        id="daily_digest",
        max_instances=1,
        replace_existing=True,
    )

    async def _pruning_job():
        now = datetime.now(UTC)
        try:
            await prune_old_data(
                now,
                source_retention_days=settings.source_retention_days,
                signal_retention_days=settings.signal_retention_days,
            )
        except Exception as exc:
            log.error("Weekly pruning failed", component="scheduler", error=str(exc))

    scheduler.add_job(
        _pruning_job,
        CronTrigger(day_of_week="sun", hour=settings.pruning_cron_hour, minute=0),
        id="weekly_pruning",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    log.info(
        "Scheduler built",
        component="scheduler",
        jobs=list(connector_map.keys()) + ["daily_scoring", "daily_brief_generation", "daily_digest", "weekly_pruning"],
    )
    return scheduler

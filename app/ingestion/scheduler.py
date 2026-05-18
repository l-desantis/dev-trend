import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from sqlalchemy import select

from app.config import Settings
from app.db import get_session
from app.ingestion.base import BaseConnector, ConnectorRunRegistry
from app.maintenance.pruning import prune_old_data
from app.models import MaintenanceState

_STALE_PRUNING_THRESHOLD_DAYS = 10

log = structlog.get_logger(__name__)


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

    scheduler.add_job(_make_job("github"), IntervalTrigger(hours=6), id="github_ingestion", max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_make_job("hn"), IntervalTrigger(hours=6), id="hn_ingestion", max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_make_job("reddit"), IntervalTrigger(hours=settings.reddit_cron_interval_hours), id="reddit_ingestion", max_instances=1, coalesce=True, misfire_grace_time=300)

    async def _pipeline_job() -> None:
        from app.llm.factory import make_embedding_adapter, make_llm_adapter
        from app.pipeline.orchestrator import run_pipeline
        from app.db import _get_session_factory
        llm = make_llm_adapter(settings)
        embedder = make_embedding_adapter(settings)
        session_factory = _get_session_factory()
        try:
            await run_pipeline(session_factory, llm, embedder, settings)
        except Exception as exc:
            log.error("Daily pipeline failed", component="scheduler", error=str(exc))

    scheduler.add_job(
        _pipeline_job,
        CronTrigger(hour=settings.pipeline_cron_hour, minute=settings.pipeline_cron_minute),
        id="daily_pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
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

    async def _scoring_job() -> None:
        from datetime import UTC, datetime
        import httpx
        from app.db import _get_session_factory
        from app.pipeline.validation import run_validation
        from app.scoring.candidate_scorer import score_all_candidates
        from app.pipeline.lifecycle import update_lifecycle_states_and_emit_transitions
        from app.bot.v4_notifications import emit_lifecycle_alerts

        session_factory = _get_session_factory()
        _gh_headers = {"Authorization": f"Bearer {settings.github_token}"} if settings.github_token else {}
        github_client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s, headers=_gh_headers)
        try:
            async with session_factory() as session:
                await run_validation(session, github_client)
            async with session_factory() as session:
                as_of = datetime.now(UTC)
                await score_all_candidates(session, as_of=as_of)
            async with session_factory() as session:
                transitions = await update_lifecycle_states_and_emit_transitions(
                    session, as_of=datetime.now(UTC)
                )
            if bot is not None:
                async with session_factory() as session:
                    await emit_lifecycle_alerts(
                        transitions, bot, session,
                        settings.telegram_allowed_chat_ids, settings
                    )
        except Exception as exc:
            log.error("Daily scoring job failed", component="scheduler", error=str(exc))
        finally:
            await github_client.aclose()

    scheduler.add_job(
        _scoring_job,
        CronTrigger(hour=settings.scoring_cron_hour, minute=settings.scoring_cron_minute),
        id="daily_scoring",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    async def _digest_job() -> None:
        from app.db import _get_session_factory
        from app.llm.factory import make_llm_adapter
        from app.bot.v4_notifications import run_digest_job

        if bot is None:
            return
        session_factory = _get_session_factory()
        llm = make_llm_adapter(settings)
        try:
            await run_digest_job(session_factory, bot, llm, settings)
        except Exception as exc:
            log.error("Daily digest job failed", component="scheduler", error=str(exc))

    scheduler.add_job(
        _digest_job,
        CronTrigger(hour=settings.digest_cron_hour, minute=settings.digest_cron_minute),
        id="daily_digest",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    async def _playstore_ingestion_job() -> None:
        # Play Store ingests at 02:00 UTC; daily pipeline at 03:30 picks up the new SourceItems.
        # Runs before the pipeline deliberately so same-day reviews enter the same day's digest.
        from app.ingestion.playstore_connector import PlayStoreReviewsConnector
        import httpx
        async with httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s) as client:
            connector = PlayStoreReviewsConnector(client=client, registry=registry)
            try:
                await asyncio.wait_for(connector.run(), timeout=settings.ingestion_job_timeout_s * 4)
            except asyncio.TimeoutError:
                registry.mark_error("playstore", "job timed out", settings.ingestion_job_timeout_s * 4)
                log.error("Play Store ingestion job timed out")
            except Exception as exc:
                log.error("Play Store ingestion job crashed", error=str(exc))

    scheduler.add_job(
        _playstore_ingestion_job,
        CronTrigger(hour=settings.playstore_cron_hour),
        id="playstore_ingestion",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    async def _playstore_app_discovery_job() -> None:
        from app.ingestion.playstore_app_discovery import refresh_app_list
        from app.db import _get_session_factory
        session_factory = _get_session_factory()
        try:
            async with session_factory() as session:
                count = await refresh_app_list(session)
                log.info("playstore_app_discovery_complete", upserted=count)
        except Exception as exc:
            log.error("Play Store app discovery failed", error=str(exc))

    scheduler.add_job(
        _playstore_app_discovery_job,
        CronTrigger(day_of_week="mon", hour=2, minute=30),
        id="playstore_app_discovery",
        max_instances=1,
        coalesce=True,
    )

    async def _weekly_recluster_job() -> None:
        from app.pipeline.recluster import run_weekly_recluster
        from app.llm.factory import make_embedding_adapter
        from app.db import _get_session_factory
        session_factory = _get_session_factory()
        try:
            async with session_factory() as session:
                report = await run_weekly_recluster(session)
                log.info(
                    "weekly_recluster_done",
                    merged=report.merged_count,
                    split=report.split_count,
                    relabelled=report.relabelled_count,
                )
        except Exception as exc:
            log.error("Weekly re-cluster failed", error=str(exc))

    scheduler.add_job(
        _weekly_recluster_job,
        CronTrigger(
            day_of_week=settings.weekly_recluster_cron_day,
            hour=settings.weekly_recluster_cron_hour,
        ),
        id="weekly_recluster",
        max_instances=1,
        coalesce=True,
    )

    if settings.enable_ios_rss:
        async def _ios_rss_ingestion_job() -> None:
            from app.ingestion.ios_rss_connector import IosRssReviewsConnector
            import httpx
            async with httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s) as client:
                connector = IosRssReviewsConnector(client=client, registry=registry)
                try:
                    await asyncio.wait_for(connector.run(), timeout=settings.ingestion_job_timeout_s * 2)
                except Exception as exc:
                    log.error("iOS RSS ingestion job crashed", error=str(exc))

        scheduler.add_job(
            _ios_rss_ingestion_job,
            CronTrigger(hour=settings.playstore_cron_hour, minute=30),
            id="ios_rss_ingestion",
            max_instances=1,
            coalesce=True,
        )

    log.info(
        "Scheduler built",
        component="scheduler",
        jobs=[
            "github_ingestion", "hn_ingestion", "reddit_ingestion",
            "playstore_ingestion", "playstore_app_discovery",
            "daily_pipeline", "daily_scoring", "daily_digest",
            "weekly_recluster", "weekly_pruning",
        ],
    )
    return scheduler

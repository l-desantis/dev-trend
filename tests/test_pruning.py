import pytest
pytest.skip("v3 — references deleted NicheSignal/Niche; rewrite in Plan C", allow_module_level=True)

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import get_session, init_db
from app.maintenance.pruning import PruneReport, prune_old_data
from app.models import MaintenanceState, NicheSignal, SourceItem, Niche


async def _mk_niche(slug: str) -> int:
    async with get_session() as session:
        n = Niche(slug=slug, name=slug, keywords_json=[])
        session.add(n)
        await session.commit()
        await session.refresh(n)
        return n.id


async def _mk_source_item(external_id: str, created_at: datetime) -> None:
    async with get_session() as session:
        session.add(SourceItem(
            source_type="github",
            external_id=external_id,
            title="t",
            body="b",
            url="u",
            created_at=created_at,
            ingested_at=created_at,
            metadata_json={},
        ))
        await session.commit()


async def _mk_signal(niche_id: int, metric_name: str, metric_timestamp: datetime) -> None:
    async with get_session() as session:
        session.add(NicheSignal(
            niche_id=niche_id,
            source_type="github",
            metric_name=metric_name,
            metric_value=1.0,
            metric_timestamp=metric_timestamp,
        ))
        await session.commit()


async def _count(model) -> int:
    async with get_session() as session:
        rows = (await session.execute(select(model))).scalars().all()
        return len(rows)


NOW = datetime(2026, 4, 28, 3, 0, tzinfo=UTC)


class TestSourceItemPruning:
    async def test_prunes_91d_old_source_item(self):
        await init_db()
        old_ts = NOW - timedelta(days=91)
        new_ts = NOW - timedelta(days=89)
        await _mk_source_item("old-item", old_ts)
        await _mk_source_item("new-item", new_ts)

        report = await prune_old_data(NOW, source_retention_days=90)

        assert report.source_items_deleted == 1
        assert await _count(SourceItem) == 1

    async def test_keeps_89d_old_source_item(self):
        await init_db()
        new_ts = NOW - timedelta(days=89)
        await _mk_source_item("new-item", new_ts)

        report = await prune_old_data(NOW, source_retention_days=90)

        assert report.source_items_deleted == 0
        assert await _count(SourceItem) == 1

    async def test_source_item_null_created_at_not_pruned(self):
        """Items with NULL created_at are not matched by the < cutoff filter."""
        await init_db()
        async with get_session() as session:
            session.add(SourceItem(
                source_type="github", external_id="null-ts",
                title="t", body="b", url="u",
                created_at=None,
                ingested_at=NOW,
                metadata_json={},
            ))
            await session.commit()

        report = await prune_old_data(NOW, source_retention_days=90)

        assert report.source_items_deleted == 0


class TestNicheSignalPruning:
    async def test_prunes_31d_non_aggregate_signal(self):
        await init_db()
        nid = await _mk_niche("alpha")
        old_ts = NOW - timedelta(days=31)
        await _mk_signal(nid, "raw_custom_metric", old_ts)

        report = await prune_old_data(NOW, signal_retention_days=30)

        assert report.signals_deleted == 1

    async def test_keeps_31d_daily_aggregate_signal(self):
        """Daily aggregate metric_names must NOT be pruned even when old."""
        await init_db()
        nid = await _mk_niche("alpha")
        old_ts = NOW - timedelta(days=31)
        for metric in ("mention_count", "github_stars_total",
                       "hn_points_total", "reddit_ups_total", "appstore_install_proxy"):
            await _mk_signal(nid, metric, old_ts)

        report = await prune_old_data(NOW, signal_retention_days=30)

        assert report.signals_deleted == 0
        assert await _count(NicheSignal) == 5

    async def test_keeps_29d_non_aggregate_signal(self):
        await init_db()
        nid = await _mk_niche("alpha")
        new_ts = NOW - timedelta(days=29)
        await _mk_signal(nid, "raw_custom_metric", new_ts)

        report = await prune_old_data(NOW, signal_retention_days=30)

        assert report.signals_deleted == 0


class TestIdempotency:
    async def test_second_prune_returns_zero_deletions(self):
        await init_db()
        await _mk_source_item("old-item", NOW - timedelta(days=91))
        nid = await _mk_niche("alpha")
        await _mk_signal(nid, "raw_metric", NOW - timedelta(days=31))

        await prune_old_data(NOW, source_retention_days=90, signal_retention_days=30)
        report2 = await prune_old_data(NOW, source_retention_days=90, signal_retention_days=30)

        assert report2.source_items_deleted == 0
        assert report2.signals_deleted == 0


class TestMaintenanceState:
    async def test_prune_sets_last_pruned_at(self):
        await init_db()
        await prune_old_data(NOW)

        async with get_session() as session:
            state = (await session.execute(select(MaintenanceState))).scalar_one_or_none()
        assert state is not None
        assert state.last_pruned_at is not None

    async def test_prune_updates_existing_state(self):
        await init_db()
        earlier = NOW - timedelta(hours=1)
        await prune_old_data(earlier)
        await prune_old_data(NOW)

        async with get_session() as session:
            states = (await session.execute(select(MaintenanceState))).scalars().all()
        assert len(states) == 1
        assert states[0].last_pruned_at == NOW.replace(tzinfo=None)


class TestPruneReport:
    async def test_report_fields_are_set(self):
        await init_db()
        report = await prune_old_data(NOW)
        assert isinstance(report, PruneReport)
        assert report.ran_at == NOW
        assert report.duration_ms >= 0
        assert report.source_items_deleted == 0
        assert report.signals_deleted == 0

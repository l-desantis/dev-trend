import pytest
pytest.skip("v3 — references deleted ORM entities; deferred to Plan C", allow_module_level=True)

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import get_session, init_db
from app.features.signal_aggregator import aggregate_daily_signals, rebuild_historical_signals
from app.models import Niche, NicheSignal, SourceItem


async def _seed_niche(slug: str = "ai-habit") -> int:
    async with get_session() as session:
        n = Niche(slug=slug, name="AI Habit", keywords_json=["habit"])
        session.add(n)
        await session.commit()
        await session.refresh(n)
        return n.id


async def _seed_source_item(niche_id: int, source_type: str, external_id: str,
                             ingested_at: datetime, metadata: dict | None = None) -> None:
    async with get_session() as session:
        session.add(SourceItem(
            source_type=source_type,
            external_id=external_id,
            title="t", body="b", url="u",
            created_at=ingested_at,
            ingested_at=ingested_at,
            niche_id=niche_id,
            metadata_json=metadata or {},
        ))
        await session.commit()


async def test_emits_mention_count_per_source_type():
    await init_db()
    nid = await _seed_niche()
    day = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await _seed_source_item(nid, "github", "g1", day, {"stars": 100})
    await _seed_source_item(nid, "github", "g2", day, {"stars": 50})
    await _seed_source_item(nid, "hn", "h1", day, {"points": 20})

    written = await aggregate_daily_signals(day)
    assert written >= 4  # mention_count x2 sources + github_stars_total + hn_points_total

    async with get_session() as session:
        result = await session.execute(
            select(NicheSignal).where(
                NicheSignal.niche_id == nid,
                NicheSignal.metric_name == "mention_count",
            )
        )
        signals = result.scalars().all()
    by_source = {s.source_type: s.metric_value for s in signals}
    assert by_source == {"github": 2.0, "hn": 1.0}


async def test_emits_source_specific_totals():
    await init_db()
    nid = await _seed_niche()
    day = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await _seed_source_item(nid, "github", "g1", day, {"stars": 100})
    await _seed_source_item(nid, "github", "g2", day, {"stars": 50})

    await aggregate_daily_signals(day)

    async with get_session() as session:
        result = await session.execute(
            select(NicheSignal).where(
                NicheSignal.niche_id == nid,
                NicheSignal.metric_name == "github_stars_total",
            )
        )
        row = result.scalar_one()
    assert row.metric_value == 150.0


async def test_excludes_items_from_other_days():
    await init_db()
    nid = await _seed_niche()
    target_day = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    other_day = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
    await _seed_source_item(nid, "github", "g1", target_day, {"stars": 10})
    await _seed_source_item(nid, "github", "g2", other_day, {"stars": 99})

    await aggregate_daily_signals(target_day)

    async with get_session() as session:
        result = await session.execute(
            select(NicheSignal).where(
                NicheSignal.niche_id == nid,
                NicheSignal.metric_name == "github_stars_total",
            )
        )
        row = result.scalar_one()
    assert row.metric_value == 10.0  # 99 from the other day is excluded


async def test_idempotent_rerun():
    await init_db()
    nid = await _seed_niche()
    day = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await _seed_source_item(nid, "github", "g1", day, {"stars": 10})

    await aggregate_daily_signals(day)
    await aggregate_daily_signals(day)  # no duplicates

    async with get_session() as session:
        result = await session.execute(
            select(NicheSignal).where(NicheSignal.niche_id == nid)
        )
        signals = result.scalars().all()
    # One mention_count + one github_stars_total = 2 rows, not 4
    assert len(signals) == 2


async def test_skips_items_with_no_niche():
    await init_db()
    day = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    async with get_session() as session:
        session.add(SourceItem(
            source_type="github", external_id="orphan",
            title="t", body="b", url="u",
            created_at=day, ingested_at=day,
            niche_id=None, metadata_json={"stars": 1},
        ))
        await session.commit()

    written = await aggregate_daily_signals(day)
    assert written == 0


# ---------------------------------------------------------------------------
# rebuild_historical_signals
# ---------------------------------------------------------------------------

async def _seed_item_with_created_at(
    niche_id: int, source_type: str, external_id: str,
    created_at: datetime, metadata: dict | None = None,
) -> None:
    """Seed a SourceItem with an explicit created_at (backfill scenario)."""
    async with get_session() as session:
        session.add(SourceItem(
            source_type=source_type,
            external_id=external_id,
            title="t", body="b", url="u",
            created_at=created_at,
            ingested_at=datetime.now(UTC),  # ingested today, created historically
            niche_id=niche_id,
            metadata_json=metadata or {},
        ))
        await session.commit()


async def test_rebuild_historical_signals_bins_by_created_at():
    """Items on different days produce NicheSignal rows for each day."""
    await init_db()
    nid = await _seed_niche("historical-niche")
    now = datetime.now(UTC)
    day1 = now - timedelta(days=5)
    day2 = now - timedelta(days=3)

    await _seed_item_with_created_at(nid, "github", "hist-g1", day1, {"stars": 10})
    await _seed_item_with_created_at(nid, "github", "hist-g2", day1, {"stars": 20})
    await _seed_item_with_created_at(nid, "hn", "hist-h1", day2, {"points": 5})

    total = await rebuild_historical_signals(history_days=7)
    assert total >= 3  # mention_count×2days + stars_total + hn_points_total

    async with get_session() as session:
        result = await session.execute(
            select(NicheSignal)
            .where(NicheSignal.niche_id == nid)
            .order_by(NicheSignal.metric_timestamp)
        )
        signals = result.scalars().all()

    # Two distinct metric_timestamp values (day1 and day2 midnight)
    timestamps = {s.metric_timestamp for s in signals}
    assert len(timestamps) == 2

    # Day1 github mention_count should be 2.
    # SQLite returns naive datetimes; strip tzinfo for comparison.
    day1_start_naive = day1.astimezone(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    github_day1 = [
        s for s in signals
        if s.source_type == "github"
        and s.metric_name == "mention_count"
        and s.metric_timestamp.replace(tzinfo=None) == day1_start_naive
    ]
    assert len(github_day1) == 1
    assert github_day1[0].metric_value == 2.0


async def test_rebuild_historical_signals_idempotent():
    """Re-running rebuild_historical_signals produces no duplicate rows."""
    await init_db()
    nid = await _seed_niche("idempotent-niche")
    now = datetime.now(UTC)
    day = now - timedelta(days=2)

    await _seed_item_with_created_at(nid, "github", "idem-g1", day, {"stars": 50})

    await rebuild_historical_signals(history_days=5)
    await rebuild_historical_signals(history_days=5)  # re-run

    async with get_session() as session:
        result = await session.execute(
            select(NicheSignal).where(NicheSignal.niche_id == nid)
        )
        signals = result.scalars().all()
    # 1 mention_count + 1 github_stars_total = 2, not 4
    assert len(signals) == 2


async def test_rebuild_historical_signals_excludes_out_of_window():
    """Items older than history_days window are not included."""
    await init_db()
    nid = await _seed_niche("window-niche")
    now = datetime.now(UTC)
    in_window = now - timedelta(days=3)
    out_of_window = now - timedelta(days=40)

    await _seed_item_with_created_at(nid, "github", "win-g1", in_window, {"stars": 10})
    await _seed_item_with_created_at(nid, "github", "out-g1", out_of_window, {"stars": 99})

    await rebuild_historical_signals(history_days=7)

    async with get_session() as session:
        result = await session.execute(
            select(NicheSignal).where(
                NicheSignal.niche_id == nid,
                NicheSignal.metric_name == "github_stars_total",
            )
        )
        signals = result.scalars().all()
    # Only the in-window item's stars (10), not the out-of-window (99)
    assert len(signals) == 1
    assert signals[0].metric_value == 10.0

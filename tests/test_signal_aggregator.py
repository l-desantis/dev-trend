from datetime import UTC, datetime

from sqlalchemy import select

from app.db import get_session, init_db
from app.features.signal_aggregator import aggregate_daily_signals
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

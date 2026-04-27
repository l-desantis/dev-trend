from datetime import UTC, datetime

from sqlalchemy import select

from app.db import get_session, init_db
from app.features.signal_aggregator import aggregate_daily_signals
from app.forecasting.scoring import score_all_niches, score_niche
from app.models import Niche, NicheScoreHistory, SourceItem


async def _mk_niche(slug: str) -> int:
    async with get_session() as session:
        n = Niche(slug=slug, name=slug, keywords_json=[slug])
        session.add(n)
        await session.commit()
        await session.refresh(n)
        return n.id


async def _mk_item(niche_id: int, source_type: str, external_id: str,
                   ingested_at: datetime, metadata: dict | None = None) -> None:
    async with get_session() as session:
        session.add(SourceItem(
            source_type=source_type, external_id=external_id,
            title="t", body="b", url="u",
            created_at=ingested_at, ingested_at=ingested_at,
            niche_id=niche_id, metadata_json=metadata or {},
        ))
        await session.commit()


async def test_score_niche_persists_history_row():
    await init_db()
    nid = await _mk_niche("alpha")
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await _mk_item(nid, "github", "g1", now, {"stars": 100})
    await aggregate_daily_signals(now)

    row = await score_niche(nid, now)

    assert row.niche_id == nid
    assert 0.0 <= row.score_total <= 100.0
    assert "growth" in row.score_breakdown_json
    assert "demand" in row.score_breakdown_json
    assert "novelty" in row.score_breakdown_json
    assert "raw" in row.score_breakdown_json["growth"]
    assert "normalized" in row.score_breakdown_json["growth"]


async def test_score_niche_composite_uses_correct_weights():
    await init_db()
    nid = await _mk_niche("alpha")
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await _mk_item(nid, "github", "g1", now, {"stars": 100})
    await aggregate_daily_signals(now)

    row = await score_niche(nid, now)
    b = row.score_breakdown_json
    expected = (
        b["growth"]["normalized"] * 0.41
        + b["demand"]["normalized"] * 0.35
        + b["novelty"]["normalized"] * 0.24
    )
    assert abs(row.score_total - expected) < 0.001


async def test_score_niche_idempotent_same_day():
    await init_db()
    nid = await _mk_niche("alpha")
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await _mk_item(nid, "github", "g1", now, {"stars": 100})
    await aggregate_daily_signals(now)

    await score_niche(nid, now)
    await score_niche(nid, now)  # re-run

    async with get_session() as session:
        result = await session.execute(
            select(NicheScoreHistory).where(NicheScoreHistory.niche_id == nid)
        )
        rows = result.scalars().all()
    assert len(rows) == 1


async def test_novelty_is_one_for_brand_new_item():
    await init_db()
    nid = await _mk_niche("alpha")
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await _mk_item(nid, "github", "g1", now, {"stars": 10})
    await aggregate_daily_signals(now)

    row = await score_niche(nid, now)
    assert row.score_breakdown_json["novelty"]["raw"] == 1.0


async def test_novelty_is_zero_for_niche_with_no_items():
    await init_db()
    nid = await _mk_niche("alpha")
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)

    row = await score_niche(nid, now)
    assert row.score_breakdown_json["novelty"]["raw"] == 0.0


async def test_score_all_niches_returns_count():
    await init_db()
    n1 = await _mk_niche("alpha")
    n2 = await _mk_niche("beta")
    now = datetime(2026, 4, 23, 12, 0, tzinfo=UTC)
    await _mk_item(n1, "github", "g1", now, {"stars": 10})
    await _mk_item(n2, "github", "g2", now, {"stars": 20})
    await aggregate_daily_signals(now)

    count = await score_all_niches(now)
    assert count == 2

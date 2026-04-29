import pytest
pytest.skip("v3 — references deleted ORM entities; deferred to Plan C", allow_module_level=True)

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from app.db import get_session, init_db
from app.features.signal_aggregator import aggregate_daily_signals
from app.forecasting.scoring import score_all_niches, score_niche
from app.models import Niche, NicheScoreHistory, NicheSignal, SourceItem


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


# ---------------------------------------------------------------------------
# M6-03 gap-fill: percentile rank, sparse history, spike-alert delta
# ---------------------------------------------------------------------------

async def _seed_score_history(niche_id: int, as_of: datetime, days: int, growth_raw: float) -> None:
    """Seed `days` NicheScoreHistory rows before as_of with fixed growth_raw."""
    today = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    async with get_session() as session:
        for offset in range(1, days + 1):
            session.add(NicheScoreHistory(
                niche_id=niche_id,
                score_total=50.0,
                score_breakdown_json={"growth": {"raw": growth_raw, "normalized": 50.0}},
                scored_at=today - timedelta(days=offset),
            ))
        await session.commit()


async def _seed_mention_counts(niche_id: int, as_of: datetime, counts: list[float]) -> None:
    """Seed mention_count NicheSignal rows for last len(counts) days ending at as_of."""
    today = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    n = len(counts)
    async with get_session() as session:
        for i, val in enumerate(counts):
            day = today - timedelta(days=(n - 1 - i))
            session.add(NicheSignal(
                niche_id=niche_id,
                source_type="hn",
                metric_name="mention_count",
                metric_value=val,
                metric_timestamp=day,
            ))
        await session.commit()


async def test_percentile_rank_trending_vs_flat():
    """Trending niche Growth normalized ≥ 80; flat niche Growth normalized ≤ 30."""
    await init_db()
    n_trend = await _mk_niche("trending")
    n_flat = await _mk_niche("flat")
    as_of = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

    # Seed 30 days of history with near-zero growth raws for trending niche.
    # Today's positive slope will exceed all history → high percentile.
    await _seed_score_history(n_trend, as_of, days=30, growth_raw=0.0)
    # Seed increasing mention counts → positive rolling slope today.
    await _seed_mention_counts(n_trend, as_of, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

    # Seed 30 days of history with high growth raws for flat niche.
    # Today's zero slope will be below all history → low percentile.
    await _seed_score_history(n_flat, as_of, days=30, growth_raw=1.0)
    # Seed constant mention counts → rolling slope ≈ 0.
    await _seed_mention_counts(n_flat, as_of, [5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])

    await score_all_niches(as_of)

    today = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    async with get_session() as session:
        row_trend = (await session.execute(
            select(NicheScoreHistory)
            .where(NicheScoreHistory.niche_id == n_trend, NicheScoreHistory.scored_at == today)
        )).scalar_one()
        row_flat = (await session.execute(
            select(NicheScoreHistory)
            .where(NicheScoreHistory.niche_id == n_flat, NicheScoreHistory.scored_at == today)
        )).scalar_one()

    assert row_trend.score_breakdown_json["growth"]["normalized"] >= 80
    assert row_flat.score_breakdown_json["growth"]["normalized"] <= 30


async def test_sparse_history_degrades_gracefully():
    """Only 3 days of signal data → no crash, score_total is in [0, 100]."""
    await init_db()
    nid = await _mk_niche("sparse")
    as_of = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

    # Seed only 3 days of mention_count signals (sparse growth window).
    await _seed_mention_counts(nid, as_of, [1.0, 2.0, 3.0])
    # No NicheScoreHistory → _raw_history returns [] → percentile_rank returns 50.0

    row = await score_niche(nid, as_of)

    assert row is not None
    assert 0.0 <= row.score_total <= 100.0
    for dim in ("growth", "demand", "novelty"):
        normalized = row.score_breakdown_json[dim]["normalized"]
        assert 0.0 <= normalized <= 100.0


async def test_spike_alert_fires_at_and_above_threshold(monkeypatch):
    """delta = threshold → alert fires; delta = threshold - 1 → alert suppressed."""
    from app.bot.scheduler_hooks import push_spike_alerts

    await init_db()
    nid = await _mk_niche("spike")
    threshold = 15.0
    today_start = datetime(2026, 4, 28, 0, 0, tzinfo=UTC)
    yesterday_start = today_start - timedelta(days=1)

    async with get_session() as session:
        session.add(NicheScoreHistory(
            niche_id=nid, score_total=70.0,
            score_breakdown_json={}, scored_at=yesterday_start,
        ))
        session.add(NicheScoreHistory(
            niche_id=nid, score_total=70.0 + threshold,  # delta == threshold
            score_breakdown_json={}, scored_at=today_start,
        ))
        await session.commit()

    mock_settings = MagicMock()
    mock_settings.telegram_allowed_chat_ids = [12345]
    mock_settings.spike_alert_threshold = threshold
    monkeypatch.setattr("app.bot.scheduler_hooks.get_settings", lambda: mock_settings)

    bot_fires = AsyncMock()
    await push_spike_alerts(bot_fires, as_of=today_start)
    bot_fires.send_message.assert_called_once()

    # Now test that delta < threshold suppresses the alert.
    async with get_session() as session:
        row = (await session.execute(
            select(NicheScoreHistory)
            .where(NicheScoreHistory.niche_id == nid,
                   NicheScoreHistory.scored_at == today_start)
        )).scalar_one()
        row.score_total = 70.0 + threshold - 1  # delta < threshold
        await session.commit()

    bot_silent = AsyncMock()
    await push_spike_alerts(bot_silent, as_of=today_start)
    bot_silent.send_message.assert_not_called()

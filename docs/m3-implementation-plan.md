# Milestone 3 — Features and Scoring — Implementation Plan

> **Date:** 2026-04-24
> **Milestone:** M3 (Features and Scoring)
> **Executor note:** This plan is intended to be executed outside the planning session. Each task is TDD-structured (failing test → implementation → passing test → commit). Follow steps in order.

---

## Context

M2 shipped the ingestion layer: four connectors (GitHub, HN, Reddit, AppStore mock) write `SourceItem` rows with `niche_id` attached via keyword match. `NicheSignal` and `NicheScoreHistory` tables exist but are empty — nothing today produces daily signals or composite scores.

M3 closes that gap. It builds the three-dimension scoring pipeline exactly as specified in ADR-004 and project doc §10: Growth (weight 0.41, 7-day rolling OLS slope), Demand (0.35, mentions + GH star delta + install proxy), Novelty (0.24, `1 − age/30`), each percentile-normalised over each niche's rolling 30-day history and combined into a 0–100 composite persisted daily to `NicheScoreHistory`. The daily job is wired into the existing `AsyncIOScheduler` so scores compute every morning before the 08:00 UTC digest.

**Design decisions (confirmed with user):**
1. Populate `NicheSignal` daily via a new `signal_aggregator` step (source for Growth/Demand; feeds `/trending` later).
2. Idempotency via delete-then-insert in scoring code — **no schema change** to `NicheScoreHistory`.
3. One daily cron job added to existing `app/ingestion/scheduler.py` (no new scheduler file).

**Already done:** ADR-004 in `docs/decisions.md:54-76` documents the scoring design (three dimensions, weights, percentile rank, spike-alert logic). M3-06 is effectively complete; Task 7 below is a read-only review + light amendment if implementation surfaces a deviation.

---

## File Structure

**New files:**
- `app/features/signal_aggregator.py` — builds daily `NicheSignal` rows from `SourceItem`.
- `app/features/trend_features.py` — pure math: `rolling_slope`, `percentile_rank`.
- `app/forecasting/scoring.py` — dimension computations + normalisation + composite + persistence.
- `scripts/run_scoring.py` — manual smoke runner.
- `tests/test_trend_features.py`
- `tests/test_signal_aggregator.py`
- `tests/test_scoring.py`

**Modified files:**
- `app/config.py` — add scoring weights + window settings.
- `app/ingestion/scheduler.py` — add `daily_scoring` cron job.
- `KANBAN.md` — flip M3-01 … M3-06 to Done.

**Untouched (but referenced):**
- `app/models.py:56-86` — `NicheSignal`, `NicheScoreHistory` schemas. No change.
- `app/db.py:27-30` — reuse `get_session()` async context manager.
- `app/ingestion/base.py:92-114` — existing `sqlite_insert … on_conflict_do_nothing` pattern for reference.

---

## Implementation Idioms (follow existing patterns)

- **Async DB access:** `async with get_session() as session: await session.execute(...); await session.commit()`.
- **Tests:** plain `async def test_*` (pytest-asyncio is in auto mode — `tests/test_connectors.py` has no `@pytest.mark.asyncio` decorators and works). Call `await init_db()` inside each test to create tables against the in-memory SQLite configured by `tests/conftest.py:8-14`.
- **Structured logging:** `log = structlog.get_logger(__name__)`; emit `log.info("event", component="…", …)`.

---

## Task 1 — Scoring config settings

**Files:**
- Modify: `app/config.py` (insert a "Scoring" block after the "Scheduling" block at line 79)

- [x] **Step 1: Add settings**

In `app/config.py`, after the existing `spike_alert_threshold: float = 15.0` line, add:

```python
    # Scoring
    growth_weight: float = 0.41
    demand_weight: float = 0.35
    novelty_weight: float = 0.24
    scoring_growth_window_days: int = 7
    scoring_novelty_max_age_days: int = 30
    scoring_normalization_window_days: int = 30
    scoring_cron_hour: int = 2
    scoring_cron_minute: int = 15
```

- [x] **Step 2: Quick sanity check**

Run: `python -c "from app.config import get_settings; s = get_settings(); print(s.growth_weight + s.demand_weight + s.novelty_weight)"`
Expected: `1.0`

- [x] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat(scoring): add scoring weights and window settings"
```

---

## Task 2 — Pure math helpers (TDD)

**Files:**
- Create: `app/features/trend_features.py`
- Test: `tests/test_trend_features.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_trend_features.py`:

```python
from app.features.trend_features import percentile_rank, rolling_slope


def test_rolling_slope_constant_series_is_zero():
    assert rolling_slope([5, 5, 5, 5, 5, 5, 5]) == 0.0


def test_rolling_slope_rising_series_is_positive():
    assert rolling_slope([1, 2, 3, 4, 5, 6, 7]) == 1.0


def test_rolling_slope_declining_series_is_negative():
    assert rolling_slope([7, 6, 5, 4, 3, 2, 1]) == -1.0


def test_rolling_slope_returns_zero_for_fewer_than_two_points():
    assert rolling_slope([]) == 0.0
    assert rolling_slope([42]) == 0.0


def test_percentile_rank_target_above_all_is_100():
    assert percentile_rank([1, 2, 3, 4, 5], 10.0) == 100.0


def test_percentile_rank_target_below_all_is_zero():
    assert percentile_rank([1, 2, 3, 4, 5], 0.0) == 0.0


def test_percentile_rank_midpoint_is_50():
    # Value 3 has two below, two above, one equal → (2 + 0.5*1)/5 = 0.5 → 50
    assert percentile_rank([1, 2, 3, 4, 5], 3.0) == 50.0


def test_percentile_rank_insufficient_history_returns_neutral_50():
    assert percentile_rank([], 42.0) == 50.0
    assert percentile_rank([7.0], 42.0) == 50.0
```

- [x] **Step 2: Run — confirm failure**

Run: `pytest tests/test_trend_features.py -v`
Expected: `ModuleNotFoundError: No module named 'app.features.trend_features'`

- [x] **Step 3: Implement**

Create `app/features/trend_features.py`:

```python
"""Pure numerical helpers for trend scoring. No I/O, no DB."""


def rolling_slope(values: list[float]) -> float:
    """Ordinary least squares slope over evenly spaced x = 0, 1, 2, ...

    Returns 0.0 when fewer than two points are available.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = 0.0
    den = 0.0
    for i, y in enumerate(values):
        dx = i - mean_x
        num += dx * (y - mean_y)
        den += dx * dx
    if den == 0:
        return 0.0
    return num / den


def percentile_rank(history: list[float], target: float) -> float:
    """Return target's percentile rank (0..100) against history.

    Uses the standard definition: (#below + 0.5 * #equal) / n * 100.
    Returns 50.0 as a neutral fallback when history has fewer than 2 values;
    early-life niches get a middle-of-pack score until enough history accrues.
    """
    if len(history) < 2:
        return 50.0
    below = sum(1 for v in history if v < target)
    equal = sum(1 for v in history if v == target)
    return ((below + 0.5 * equal) / len(history)) * 100.0
```

- [x] **Step 4: Run — confirm pass**

Run: `pytest tests/test_trend_features.py -v`
Expected: all 8 tests pass.

- [x] **Step 5: Commit**

```bash
git add app/features/trend_features.py tests/test_trend_features.py
git commit -m "feat(scoring): add rolling_slope and percentile_rank helpers"
```

---

## Task 3 — Signal aggregator (TDD)

Builds one `NicheSignal` row per `(niche_id, source_type, metric_name)` for the target UTC date, emitting:
- `mention_count` — count of SourceItems (per source type)
- `github_stars_total` — sum of `metadata_json["stars"]` (github only)
- `hn_points_total` — sum of `metadata_json["points"]` (hn only)
- `reddit_ups_total` — sum of `metadata_json["ups"]` (reddit only)
- `appstore_install_proxy` — sum of `metadata_json["install_proxy"]` (appstore only)

Day boundary is `ingested_at` in `[midnight_utc(date), midnight_utc(date) + 1d)`. Idempotent: deletes any `NicheSignal` rows for the touched niches on that day before inserting.

**Files:**
- Create: `app/features/signal_aggregator.py`
- Test: `tests/test_signal_aggregator.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_signal_aggregator.py`:

```python
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
```

- [x] **Step 2: Run — confirm failure**

Run: `pytest tests/test_signal_aggregator.py -v`
Expected: `ModuleNotFoundError: No module named 'app.features.signal_aggregator'`

- [x] **Step 3: Implement**

Create `app/features/signal_aggregator.py`:

```python
"""Daily aggregation: SourceItem rows → NicheSignal rows per niche×source×metric."""
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, func, select

from app.db import get_session
from app.models import NicheSignal, SourceItem

log = structlog.get_logger(__name__)

# Per-source metric name and the metadata key to sum.
_SOURCE_METRICS: dict[str, tuple[str, str]] = {
    "github": ("github_stars_total", "stars"),
    "hn": ("hn_points_total", "points"),
    "reddit": ("reddit_ups_total", "ups"),
    "appstore": ("appstore_install_proxy", "install_proxy"),
}


def _day_bounds(as_of: datetime) -> tuple[datetime, datetime]:
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    day_start = as_of.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


async def aggregate_daily_signals(as_of: datetime) -> int:
    """Write one NicheSignal row per (niche_id, source_type, metric_name) for as_of's UTC day.

    Returns the number of rows written. Idempotent: existing signals with the same
    metric_timestamp day for touched niches are removed before insert.
    """
    day_start, day_end = _day_bounds(as_of)

    async with get_session() as session:
        # mention_count per (niche_id, source_type)
        mention_stmt = (
            select(
                SourceItem.niche_id,
                SourceItem.source_type,
                func.count(SourceItem.id).label("n"),
            )
            .where(
                SourceItem.niche_id.is_not(None),
                SourceItem.ingested_at >= day_start,
                SourceItem.ingested_at < day_end,
            )
            .group_by(SourceItem.niche_id, SourceItem.source_type)
        )
        mention_rows = (await session.execute(mention_stmt)).all()

        # Per-source specific totals: fetch items and sum metadata in Python
        # (SQLite JSON_EXTRACT works but varies by version; keep it portable).
        items_stmt = (
            select(SourceItem)
            .where(
                SourceItem.niche_id.is_not(None),
                SourceItem.ingested_at >= day_start,
                SourceItem.ingested_at < day_end,
            )
        )
        items = (await session.execute(items_stmt)).scalars().all()

        source_totals: dict[tuple[int, str, str], float] = {}
        for item in items:
            metric = _SOURCE_METRICS.get(item.source_type)
            if metric is None:
                continue
            metric_name, meta_key = metric
            value = 0.0
            if item.metadata_json is not None:
                raw = item.metadata_json.get(meta_key)
                if isinstance(raw, (int, float)):
                    value = float(raw)
            key = (item.niche_id, item.source_type, metric_name)
            source_totals[key] = source_totals.get(key, 0.0) + value

        # Idempotency: delete any existing NicheSignal rows for this day
        # that we're about to (re)write.
        touched_niche_ids = {nid for nid, _, _ in mention_rows} | {
            nid for (nid, _, _) in source_totals.keys()
        }
        if touched_niche_ids:
            await session.execute(
                delete(NicheSignal).where(
                    NicheSignal.niche_id.in_(touched_niche_ids),
                    NicheSignal.metric_timestamp >= day_start,
                    NicheSignal.metric_timestamp < day_end,
                )
            )

        to_add: list[NicheSignal] = []
        for niche_id, source_type, n in mention_rows:
            to_add.append(NicheSignal(
                niche_id=niche_id,
                source_type=source_type,
                metric_name="mention_count",
                metric_value=float(n),
                metric_timestamp=day_start,
            ))
        for (niche_id, source_type, metric_name), total in source_totals.items():
            to_add.append(NicheSignal(
                niche_id=niche_id,
                source_type=source_type,
                metric_name=metric_name,
                metric_value=total,
                metric_timestamp=day_start,
            ))

        session.add_all(to_add)
        await session.commit()

    log.info(
        "Signal aggregation complete",
        component="signal_aggregator",
        day=day_start.isoformat(),
        rows=len(to_add),
    )
    return len(to_add)
```

- [x] **Step 4: Run — confirm pass**

Run: `pytest tests/test_signal_aggregator.py -v`
Expected: all 5 tests pass.

- [x] **Step 5: Commit**

```bash
git add app/features/signal_aggregator.py tests/test_signal_aggregator.py
git commit -m "feat(scoring): daily SourceItem → NicheSignal aggregator"
```

---

## Task 4 — Scoring orchestrator (TDD)

Computes the three dimension raws, percentile-normalises each against the niche's own 30-day history (from `NicheScoreHistory.score_breakdown_json[dim]["raw"]`), and persists the composite.

**Files:**
- Create: `app/forecasting/scoring.py`
- Test: `tests/test_scoring.py`

### 4.1 Shape of `score_breakdown_json`

```json
{
  "growth":  {"raw": 0.45,  "normalized": 78.2},
  "demand":  {"raw": 120.0, "normalized": 65.0},
  "novelty": {"raw": 0.8,   "normalized": 85.0}
}
```

### 4.2 Raw dimension formulas

- **Growth raw** = `rolling_slope([daily_mention_sum for day in last 7 days])`.
  `daily_mention_sum` = sum of `NicheSignal.metric_value` for the niche with `metric_name='mention_count'` on that UTC day (across all source types). Missing days → 0.
- **Demand raw** = `mentions_today + star_delta_7d + install_proxy_today` where:
  - `mentions_today` = sum of `mention_count` signals for the niche on `as_of`'s day.
  - `star_delta_7d` = `github_stars_total` today − `github_stars_total` 7 days ago (clamped ≥ 0; 0 if either is missing).
  - `install_proxy_today` = `appstore_install_proxy` signal today (0 if missing).
- **Novelty raw** = `max(0, 1 − age_days / settings.scoring_novelty_max_age_days)` where `age_days` is `(as_of − latest SourceItem.created_at or ingested_at for niche).days`. If the niche has no source items, novelty = 0.

### 4.3 Steps

- [x] **Step 1: Write failing tests**

Create `tests/test_scoring.py`:

```python
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
```

- [x] **Step 2: Run — confirm failure**

Run: `pytest tests/test_scoring.py -v`
Expected: `ModuleNotFoundError: No module named 'app.forecasting.scoring'`.

- [x] **Step 3: Implement**

Create `app/forecasting/scoring.py`:

```python
"""Daily composite scorer.

Reads NicheSignal (daily aggregates) and NicheScoreHistory (rolling history),
computes Growth / Demand / Novelty raws, percentile-normalises each against
the niche's own 30-day history, and persists a NicheScoreHistory row.
"""
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db import get_session
from app.features.trend_features import percentile_rank, rolling_slope
from app.models import Niche, NicheScoreHistory, NicheSignal, SourceItem

log = structlog.get_logger(__name__)

_DIMENSIONS = ("growth", "demand", "novelty")


def _day_start(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def _mention_sum_for_day(session, niche_id: int, day_start: datetime) -> float:
    stmt = (
        select(func.coalesce(func.sum(NicheSignal.metric_value), 0.0))
        .where(
            NicheSignal.niche_id == niche_id,
            NicheSignal.metric_name == "mention_count",
            NicheSignal.metric_timestamp == day_start,
        )
    )
    return float((await session.execute(stmt)).scalar_one())


async def _source_metric_for_day(
    session, niche_id: int, metric_name: str, day_start: datetime
) -> float:
    stmt = (
        select(func.coalesce(func.sum(NicheSignal.metric_value), 0.0))
        .where(
            NicheSignal.niche_id == niche_id,
            NicheSignal.metric_name == metric_name,
            NicheSignal.metric_timestamp == day_start,
        )
    )
    return float((await session.execute(stmt)).scalar_one())


async def _compute_growth_raw(session, niche_id: int, as_of: datetime, window_days: int) -> float:
    today = _day_start(as_of)
    daily_sums: list[float] = []
    for offset in range(window_days - 1, -1, -1):  # oldest → newest
        day = today - timedelta(days=offset)
        daily_sums.append(await _mention_sum_for_day(session, niche_id, day))
    return rolling_slope(daily_sums)


async def _compute_demand_raw(session, niche_id: int, as_of: datetime) -> float:
    today = _day_start(as_of)
    seven_ago = today - timedelta(days=7)
    mentions_today = await _mention_sum_for_day(session, niche_id, today)
    stars_today = await _source_metric_for_day(session, niche_id, "github_stars_total", today)
    stars_past = await _source_metric_for_day(session, niche_id, "github_stars_total", seven_ago)
    star_delta = max(0.0, stars_today - stars_past)
    install_proxy = await _source_metric_for_day(session, niche_id, "appstore_install_proxy", today)
    return mentions_today + star_delta + install_proxy


async def _compute_novelty_raw(session, niche_id: int, as_of: datetime, max_age_days: int) -> float:
    # Prefer created_at; fall back to ingested_at when the source provided none.
    stmt = (
        select(func.max(func.coalesce(SourceItem.created_at, SourceItem.ingested_at)))
        .where(SourceItem.niche_id == niche_id)
    )
    latest = (await session.execute(stmt)).scalar_one()
    if latest is None:
        return 0.0
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    as_of_utc = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    age_days = (as_of_utc - latest).total_seconds() / 86400.0
    return max(0.0, 1.0 - age_days / max_age_days)


async def _raw_history(
    session, niche_id: int, dimension: str, as_of: datetime, window_days: int
) -> list[float]:
    window_start = _day_start(as_of) - timedelta(days=window_days)
    stmt = (
        select(NicheScoreHistory.score_breakdown_json)
        .where(
            NicheScoreHistory.niche_id == niche_id,
            NicheScoreHistory.scored_at >= window_start,
            NicheScoreHistory.scored_at < _day_start(as_of),
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    values: list[float] = []
    for bd in rows:
        if not bd:
            continue
        dim = bd.get(dimension)
        if dim is None:
            continue
        raw = dim.get("raw")
        if isinstance(raw, (int, float)):
            values.append(float(raw))
    return values


async def score_niche(niche_id: int, as_of: datetime) -> NicheScoreHistory:
    """Compute today's score for a niche and persist to NicheScoreHistory.

    Delete-then-insert on (niche_id, scored_at = midnight UTC of as_of).
    """
    settings = get_settings()
    today = _day_start(as_of)

    async with get_session() as session:
        raws = {
            "growth": await _compute_growth_raw(
                session, niche_id, as_of, settings.scoring_growth_window_days
            ),
            "demand": await _compute_demand_raw(session, niche_id, as_of),
            "novelty": await _compute_novelty_raw(
                session, niche_id, as_of, settings.scoring_novelty_max_age_days
            ),
        }

        breakdown: dict[str, dict[str, float]] = {}
        for dim in _DIMENSIONS:
            history = await _raw_history(
                session, niche_id, dim, as_of, settings.scoring_normalization_window_days
            )
            normalized = percentile_rank(history, raws[dim])
            breakdown[dim] = {"raw": raws[dim], "normalized": normalized}

        total = (
            breakdown["growth"]["normalized"] * settings.growth_weight
            + breakdown["demand"]["normalized"] * settings.demand_weight
            + breakdown["novelty"]["normalized"] * settings.novelty_weight
        )

        await session.execute(
            delete(NicheScoreHistory).where(
                NicheScoreHistory.niche_id == niche_id,
                NicheScoreHistory.scored_at == today,
            )
        )
        row = NicheScoreHistory(
            niche_id=niche_id,
            score_total=total,
            score_breakdown_json=breakdown,
            scored_at=today,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)

    log.info(
        "Niche scored",
        component="scoring",
        niche_id=niche_id,
        score_total=round(total, 2),
        scored_at=today.isoformat(),
    )
    return row


async def score_all_niches(as_of: datetime) -> int:
    """Score every niche in the DB for as_of's UTC day. Returns niches scored."""
    async with get_session() as session:
        niche_ids = (await session.execute(select(Niche.id))).scalars().all()

    for nid in niche_ids:
        try:
            await score_niche(nid, as_of)
        except Exception as exc:
            log.error(
                "Niche scoring failed",
                component="scoring",
                niche_id=nid,
                error=str(exc),
            )
    return len(niche_ids)
```

- [x] **Step 4: Run — confirm pass**

Run: `pytest tests/test_scoring.py -v`
Expected: all 6 tests pass.

- [x] **Step 5: Commit**

```bash
git add app/forecasting/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): three-dimension composite scorer with percentile normalization"
```

---

## Task 5 — Wire daily scoring into the scheduler

**Files:**
- Modify: `app/ingestion/scheduler.py`

- [ ] **Step 1: Edit scheduler**

In `app/ingestion/scheduler.py`:

1. Add imports at the top (after existing imports):

```python
from datetime import UTC, datetime

from app.features.signal_aggregator import aggregate_daily_signals
from app.forecasting.scoring import score_all_niches
```

2. Inside `build_scheduler(...)`, after the existing `ingest_appstore` line (line 37), add:

```python
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
```

3. Update the final `log.info(...)` call to include the scoring job:

```python
    log.info(
        "Scheduler built",
        component="scheduler",
        jobs=list(connector_map.keys()) + ["daily_scoring"],
    )
```

- [ ] **Step 2: Verify imports**

Run: `python -c "from app.ingestion.scheduler import build_scheduler; print('ok')"`
Expected: `ok` (no import errors).

- [ ] **Step 3: Commit**

```bash
git add app/ingestion/scheduler.py
git commit -m "feat(scoring): schedule daily signal aggregation + niche scoring"
```

---

## Task 6 — End-to-end smoke script

**Files:**
- Create: `scripts/run_scoring.py`

- [ ] **Step 1: Create script**

Create `scripts/run_scoring.py`:

```python
"""Manual smoke-test: aggregate daily signals and score all niches against live DB.

Usage: python scripts/run_scoring.py
"""
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.db import init_db
from app.features.niche_builder import sync_niches_from_yaml
from app.features.signal_aggregator import aggregate_daily_signals
from app.forecasting.scoring import score_all_niches


async def main() -> None:
    await init_db()
    await sync_niches_from_yaml(Path("data/niches.yaml"))
    now = datetime.now(UTC)
    signal_rows = await aggregate_daily_signals(now)
    niches_scored = await score_all_niches(now)
    print(f"signals_written={signal_rows} niches_scored={niches_scored}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify**

Run (against existing dev DB after at least one ingestion has happened; if the DB is empty, first run `python scripts/seed_mock_data.py` then `python scripts/run_ingestion.py`):

```bash
python scripts/run_scoring.py
```

Expected: prints `signals_written=<N> niches_scored=<M>` with both ≥ 0 (≥ 1 if any source items exist). No traceback.

- [ ] **Step 3: Commit**

```bash
git add scripts/run_scoring.py
git commit -m "chore(scoring): add manual run_scoring smoke script"
```

---

## Task 7 — Final sweep and KANBAN update

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: all existing tests still pass + the three new test files pass. If anything breaks, fix before moving on.

- [ ] **Step 2: Review ADR-004**

Read `docs/decisions.md:54-76`. Confirm it matches the implementation:
- Weights 0.41 / 0.35 / 0.24 ✓
- Percentile rank over 30-day rolling window per niche ✓
- Spike alert logic described ✓ (implemented in M5, not M3)

If the implementation surfaced a non-obvious choice worth documenting (e.g. the neutral-50 fallback for niches with < 2 days history), append a one-paragraph note to ADR-004 under a new **Implementation notes** subheading. Otherwise leave it as-is.

- [ ] **Step 3: Flip KANBAN status**

In `KANBAN.md`, set `Status` to `Done` for: M3-01, M3-02, M3-03, M3-04, M3-05, M3-06.

- [ ] **Step 4: Commit**

```bash
git add KANBAN.md docs/decisions.md
git commit -m "docs(m3): mark M3 tasks done; review ADR-004"
```

---

## Verification (end-to-end)

1. **Unit tests** — `pytest tests/test_trend_features.py tests/test_signal_aggregator.py tests/test_scoring.py -v` → all green.
2. **Full suite regression** — `pytest -v` → no M1/M2 tests broken.
3. **Smoke run against mock data:**
   ```bash
   python scripts/seed_mock_data.py
   python scripts/run_ingestion.py
   python scripts/run_scoring.py
   ```
   Expected: prints `signals_written=<N> niches_scored=<M>` with M equal to the number of niches in `data/niches.yaml` (8–12).
4. **DB inspection:**
   ```bash
   sqlite3 devtrend.db "SELECT niche_id, score_total, scored_at FROM niche_score_history ORDER BY score_total DESC LIMIT 5;"
   sqlite3 devtrend.db "SELECT niche_id, metric_name, metric_value FROM niche_signals LIMIT 10;"
   ```
   Expected: `niche_score_history` has one row per niche for today; `niche_signals` has mention_count + source-specific rows.
5. **Scheduler boot (optional, longer):**
   ```bash
   uvicorn app.main:app
   ```
   Verify boot log includes `"Scheduler built" ... jobs=[..., "daily_scoring"]`. Job fires at `02:15 UTC` (configurable via `SCORING_CRON_HOUR` / `SCORING_CRON_MINUTE`).

---

## Out of scope (intentionally deferred)

- **Spike alert push** — listed as M5-07; requires the Telegram push infra from M5.
- **`/trending` command content** — M5-04; will read from `NicheSignal` populated by this milestone.
- **Brief generation** — M4.
- **Weekly pruning of `NicheSignal`** — M6-01.
- **Schema `UniqueConstraint(niche_id, scored_at)`** — deliberately not added; idempotency handled in code to avoid the SQLite-no-alembic schema migration dance.

---

## KANBAN coverage

| Kanban ID | Covered by |
|---|---|
| M3-01 Rolling-slope Growth | Task 2 (`rolling_slope`) + Task 4 (`_compute_growth_raw`) |
| M3-02 Demand signals | Task 3 (signal aggregator) + Task 4 (`_compute_demand_raw`) |
| M3-03 Novelty dimension | Task 4 (`_compute_novelty_raw`) |
| M3-04 Percentile normalisation | Task 2 (`percentile_rank`) + Task 4 (`_raw_history`) |
| M3-05 Composite scorer + persistence | Task 4 (`score_niche`, `score_all_niches`) + Task 5 (scheduler wiring) |
| M3-06 ADR-003 | Already written as ADR-004 in `docs/decisions.md:54-76`; Task 7 Step 2 reviews it |

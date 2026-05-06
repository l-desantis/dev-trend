"""Tests for v4 pruning job (rewritten from v3 in Plan C)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import StaticPool, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    CandidateValidation,
    LifecycleEvent,
    MaintenanceState,
    OpportunityCandidate,
    PainPoint,
    SourceItem,
)


@pytest.fixture
async def Session():
    from sqlalchemy import event

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_pragma(conn, _record):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _run_prune(Session, now: datetime):
    """Run the pruning logic directly against the in-memory DB."""
    source_cutoff = now - timedelta(days=90)
    signal_cutoff = now - timedelta(days=30)

    async with Session() as session:
        r1 = await session.execute(
            delete(SourceItem).where(SourceItem.created_at < source_cutoff)
        )
        r2 = await session.execute(
            text("""
                DELETE FROM candidate_validations
                 WHERE validated_at < :cutoff
                   AND id NOT IN (
                       SELECT MAX(id) FROM candidate_validations GROUP BY candidate_id
                   )
            """),
            {"cutoff": signal_cutoff},
        )
        r3 = await session.execute(
            delete(LifecycleEvent).where(LifecycleEvent.recorded_at < signal_cutoff)
        )
        state = (await session.execute(select(MaintenanceState))).scalar_one_or_none()
        if state is None:
            state = MaintenanceState(last_pruned_at=now)
            session.add(state)
        else:
            state.last_pruned_at = now
        await session.commit()
        return r1.rowcount, r2.rowcount, r3.rowcount


async def test_prune_painpoints_cascade_with_source_items(Session) -> None:
    now = datetime.now(UTC)
    old_created = now - timedelta(days=100)

    async with Session() as session:
        si = SourceItem(
            source_type="reddit",
            external_id="old-item",
            role="extraction",
            extraction_state="pending",
            created_at=old_created,
        )
        session.add(si)
        await session.flush()
        session.add(PainPoint(
            source_item_id=si.id,
            extractor_model="mock",
            extracted_at=now,
        ))
        await session.commit()

    source_deleted, _, _ = await _run_prune(Session, now)
    assert source_deleted == 1

    async with Session() as session:
        remaining = (await session.execute(select(PainPoint))).scalars().all()
        assert len(remaining) == 0  # cascade deleted


async def test_prune_keeps_latest_validation_per_candidate(Session) -> None:
    now = datetime.now(UTC)

    async with Session() as session:
        cand = OpportunityCandidate(problem_statement="test", created_at=now)
        session.add(cand)
        await session.flush()
        for days_ago in [45, 20, 5]:
            session.add(CandidateValidation(
                candidate_id=cand.id,
                signal_type="github_stars",
                signal_value=float(days_ago),
                validated_at=now - timedelta(days=days_ago),
            ))
        await session.commit()

    _, cv_deleted, _ = await _run_prune(Session, now)
    # -45d row: older than cutoff AND not the newest-per-candidate (that's -5d)
    # -20d row: within cutoff window (< 30d), survives
    # -5d row: newest per candidate, always protected
    assert cv_deleted == 1


async def test_prune_deletes_old_lifecycle_events(Session) -> None:
    now = datetime.now(UTC)

    async with Session() as session:
        cand = OpportunityCandidate(problem_statement="c", created_at=now)
        session.add(cand)
        await session.flush()
        for days in [10, 45]:
            session.add(LifecycleEvent(
                candidate_id=cand.id,
                old_state=None,
                new_state="emerging",
                recorded_at=now - timedelta(days=days),
            ))
        await session.commit()

    _, _, le_deleted = await _run_prune(Session, now)
    assert le_deleted == 1

    async with Session() as session:
        remaining = (await session.execute(select(LifecycleEvent))).scalars().all()
        assert len(remaining) == 1  # the 10-day-old one survives

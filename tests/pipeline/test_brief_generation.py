"""Tests for Stage 9 — brief_generation.py"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.llm.mock_adapter import MockLLMAdapter
from app.models import Base, CandidateBrief, OpportunityCandidate, PainPoint, SourceItem
from app.pipeline.brief_generation import generate_briefs_for


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session: AsyncSession, n_pain_points: int = 3) -> OpportunityCandidate:
    c = OpportunityCandidate(
        problem_statement="wish there was a better todo app",
        audience="developers",
        why_now="AI tools proliferating",
        specificity=3,
    )
    session.add(c)
    await session.flush()

    si = SourceItem(source_type="reddit", external_id=f"r_{c.id}", url="https://reddit.com/r/x")
    session.add(si)
    await session.flush()

    for i in range(n_pain_points):
        pp = PainPoint(
            source_item_id=si.id,
            candidate_id=c.id,
            extractor_model="mock",
            problem_text=f"I wish X was easier (#{i})",
            extracted_at=datetime.now(UTC) - timedelta(days=i),
        )
        session.add(pp)
    await session.commit()
    return c


async def test_generate_brief_persists(session: AsyncSession) -> None:
    c = await _seed(session)
    llm = MockLLMAdapter()

    briefs = await generate_briefs_for(session, llm, [c])
    assert len(briefs) == 1
    assert briefs[0].summary
    assert briefs[0].evidence_json
    assert len(briefs[0].evidence_json) == 3


async def test_generate_brief_timeout_skips_candidate(session: AsyncSession) -> None:
    c = await _seed(session)

    async def _slow(_ctx):
        await asyncio.sleep(100)
        return "never"

    llm = MockLLMAdapter()
    llm.generate_brief = _slow  # type: ignore[method-assign]

    briefs = await generate_briefs_for(session, llm, [c], timeout_s=0.01)
    assert briefs == []

    from sqlalchemy import select, func
    count = (await session.execute(select(func.count(CandidateBrief.id)))).scalar_one()
    assert count == 0


async def test_generate_brief_idempotent_same_day(session: AsyncSession) -> None:
    c = await _seed(session)
    llm = MockLLMAdapter()

    await generate_briefs_for(session, llm, [c])
    await generate_briefs_for(session, llm, [c])

    from sqlalchemy import select, func
    count = (await session.execute(select(func.count(CandidateBrief.id)))).scalar_one()
    assert count == 1

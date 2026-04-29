"""Tests for Stage 1 — extract."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db_helpers.categories import sync_categories_from_yaml
from app.llm.mock_adapter import MockLLMAdapter
from app.models import PainPoint, SourceItem
from app.pipeline.extract import run_extraction


def _make_item(session, external_id: str, title: str, body: str = "", role: str = "extraction") -> SourceItem:
    item = SourceItem(source_type="reddit", external_id=external_id, title=title, body=body, role=role)
    session.add(item)
    return item


async def test_extract_creates_painpoint_for_high_signal(session: AsyncSession) -> None:
    _make_item(session, "t3_1", "I wish there was a habit tracker for ADHD")
    await session.commit()

    llm = MockLLMAdapter()
    report = await run_extraction(session, llm)

    assert report.painpoints_created == 1
    pps = (await session.execute(select(PainPoint))).scalars().all()
    assert len(pps) == 1
    assert pps[0].extractor_model == "mock-llm-v1"


async def test_extract_marks_no_signal_for_low_signal(session: AsyncSession) -> None:
    _make_item(session, "t3_2", "Tech company releases new product")
    await session.commit()

    report = await run_extraction(session, MockLLMAdapter())

    assert report.no_signal == 1
    assert report.painpoints_created == 0
    item = (await session.execute(select(SourceItem))).scalar_one()
    assert item.extraction_state == "no_signal"
    pps = (await session.execute(select(PainPoint))).scalars().all()
    assert len(pps) == 0


async def test_extract_skips_already_extracted_same_model(session: AsyncSession) -> None:
    item = _make_item(session, "t3_3", "I wish there was a better finance app")
    await session.flush()
    existing = PainPoint(
        source_item_id=item.id,
        extractor_model="mock-llm-v1",
        problem_text="already extracted",
        audience="users",
    )
    session.add(existing)
    await session.commit()

    report = await run_extraction(session, MockLLMAdapter())

    # The item already has a PainPoint with same model — should be skipped
    assert report.processed == 0
    pps = (await session.execute(select(PainPoint))).scalars().all()
    assert len(pps) == 1  # original, no duplicate


async def test_extract_re_extracts_on_force(session: AsyncSession) -> None:
    item = _make_item(session, "t3_4", "Why is there no good ADHD tracker?")
    await session.flush()
    existing = PainPoint(
        source_item_id=item.id,
        extractor_model="mock-llm-v1",
        problem_text="prior",
        audience="users",
    )
    session.add(existing)
    await session.commit()

    report = await run_extraction(session, MockLLMAdapter(), force=True)

    assert report.processed == 1
    assert report.painpoints_created == 1
    pps = (await session.execute(select(PainPoint))).scalars().all()
    assert len(pps) == 2


async def test_extract_skips_validation_role(session: AsyncSession) -> None:
    _make_item(session, "gh_1", "owner/repo — cool tool", role="validation")
    await session.commit()

    report = await run_extraction(session, MockLLMAdapter())

    assert report.processed == 0
    pps = (await session.execute(select(PainPoint))).scalars().all()
    assert len(pps) == 0


async def test_extract_marks_failed_on_exception(session: AsyncSession, monkeypatch) -> None:
    _make_item(session, "t3_5", "I wish there was an app that does X")
    await session.commit()

    from unittest.mock import AsyncMock
    llm = MockLLMAdapter()
    monkeypatch.setattr(llm, "extract_pain_point", AsyncMock(side_effect=RuntimeError("LLM down")))

    report = await run_extraction(session, llm)

    assert report.failed == 1
    item = (await session.execute(select(SourceItem))).scalar_one()
    assert item.extraction_state == "failed"

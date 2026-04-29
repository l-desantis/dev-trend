"""Tests for v4 ORM model definitions."""
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    CandidateBrief,
    CandidateFeedback,
    CandidateScoreHistory,
    CandidateValidation,
    Category,
    MaintenanceState,
    OpportunityCandidate,
    PainPoint,
    SourceItem,
)


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_models_can_create_each_v4_entity(session: AsyncSession) -> None:
    cat = Category(slug="wellness", name="Wellness", description="Health apps")
    session.add(cat)
    await session.flush()

    item = SourceItem(source_type="reddit", external_id="t3_abc", title="Test", role="extraction")
    session.add(item)
    await session.flush()

    candidate = OpportunityCandidate(category_id=cat.id, problem_statement="Need X", specificity=3)
    session.add(candidate)
    await session.flush()

    pp = PainPoint(
        source_item_id=item.id,
        candidate_id=candidate.id,
        extractor_model="mock",
        problem_text="I wish there was...",
        audience="developers",
    )
    session.add(pp)
    await session.flush()

    validation = CandidateValidation(
        candidate_id=candidate.id, signal_type="star_growth", signal_value=0.8
    )
    session.add(validation)

    score = CandidateScoreHistory(
        candidate_id=candidate.id, score_total=72.0, scored_at=__import__("datetime").datetime.now()
    )
    session.add(score)

    brief = CandidateBrief(candidate_id=candidate.id, headline="Opportunity", summary="Details")
    session.add(brief)
    await session.flush()

    feedback = CandidateFeedback(
        candidate_id=candidate.id, user_id="user1", brief_id=brief.id, rating=5
    )
    session.add(feedback)
    await session.commit()

    assert cat.id is not None
    assert item.id is not None
    assert candidate.id is not None
    assert pp.id is not None
    assert validation.id is not None
    assert score.id is not None
    assert brief.id is not None
    assert feedback.id is not None


async def test_source_item_default_role_is_extraction(session: AsyncSession) -> None:
    item = SourceItem(source_type="reddit", external_id="t3_def")
    session.add(item)
    await session.commit()
    await session.refresh(item)
    assert item.role == "extraction"
    assert item.extraction_state == "pending"


async def test_candidate_unique_feedback_constraint(session: AsyncSession) -> None:
    candidate = OpportunityCandidate(problem_statement="test", specificity=1)
    session.add(candidate)
    await session.flush()

    brief = CandidateBrief(candidate_id=candidate.id, headline="h")
    session.add(brief)
    await session.flush()

    fb1 = CandidateFeedback(candidate_id=candidate.id, user_id="u1", brief_id=brief.id, rating=5)
    session.add(fb1)
    await session.commit()

    fb2 = CandidateFeedback(candidate_id=candidate.id, user_id="u1", brief_id=brief.id, rating=3)
    session.add(fb2)
    with pytest.raises(IntegrityError):
        await session.commit()

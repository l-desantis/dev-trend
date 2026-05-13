"""Tests for v4 ORM model definitions."""
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CandidateBrief,
    CandidateFeedback,
    CandidateScoreHistory,
    CandidateValidation,
    Category,
    LifecycleEvent,
    MaintenanceState,
    OpportunityCandidate,
    PainPoint,
    SourceItem,
)


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
        candidate_id=candidate.id, user_id=12345, brief_id=brief.id,
        label="up", chat_id=99,
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

    fb1 = CandidateFeedback(
        candidate_id=candidate.id, user_id=111, brief_id=brief.id, label="up", chat_id=1,
    )
    session.add(fb1)
    await session.commit()

    fb2 = CandidateFeedback(
        candidate_id=candidate.id, user_id=111, brief_id=brief.id, label="down", chat_id=1,
    )
    session.add(fb2)
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_lifecycle_event_schema(session: AsyncSession) -> None:
    candidate = OpportunityCandidate(problem_statement="test lc", specificity=3)
    session.add(candidate)
    await session.flush()

    evt = LifecycleEvent(
        candidate_id=candidate.id,
        old_state="emerging",
        new_state="hot",
        score_total=78.5,
        was_alerted=False,
    )
    session.add(evt)
    await session.commit()

    assert evt.id is not None
    assert evt.new_state == "hot"
    assert evt.was_alerted is False


async def test_candidate_brief_evidence_json(session: AsyncSession) -> None:
    candidate = OpportunityCandidate(problem_statement="evidence test", specificity=3)
    session.add(candidate)
    await session.flush()

    brief = CandidateBrief(
        candidate_id=candidate.id,
        headline="Test",
        summary="Summary",
        evidence_json=[{"problem_text": "I hate X", "source_type": "reddit"}],
    )
    session.add(brief)
    await session.commit()
    await session.refresh(brief)

    assert isinstance(brief.evidence_json, list)
    assert brief.evidence_json[0]["source_type"] == "reddit"

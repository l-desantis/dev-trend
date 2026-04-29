"""Tests for Stage 5 — labelling."""
from unittest.mock import AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.llm.mock_adapter import MockLLMAdapter
from app.llm.schemas import ClusterLabel
from app.models import Category, OpportunityCandidate, PainPoint, SourceItem
from app.pipeline.labelling import run_labelling


async def _seed_wellness(session: AsyncSession) -> Category:
    cat = Category(slug="wellness", name="Wellness")
    session.add(cat)
    await session.flush()
    return cat


async def _seed_candidate_with_pps(
    session: AsyncSession, n_pps: int = 3, labeller_model: str | None = None
) -> OpportunityCandidate:
    candidate = OpportunityCandidate(
        problem_statement="",
        specificity=0,
        labeller_model=labeller_model,
    )
    session.add(candidate)
    await session.flush()

    item = SourceItem(source_type="reddit", external_id=f"t3_{candidate.id}", role="extraction")
    session.add(item)
    await session.flush()

    for i in range(n_pps):
        pp = PainPoint(
            source_item_id=item.id,
            candidate_id=candidate.id,
            extractor_model="mock",
            problem_text=f"Problem {i}",
            audience="users",
        )
        session.add(pp)

    await session.commit()
    return candidate


async def test_labelling_populates_unlabelled_candidate(session: AsyncSession) -> None:
    candidate = await _seed_candidate_with_pps(session)
    report = await run_labelling(session, MockLLMAdapter())

    assert report.labelled == 1
    c = (await session.execute(select(OpportunityCandidate))).scalar_one()
    assert c.labeller_model == "mock-llm-v1"
    assert c.problem_statement != ""
    assert c.specificity >= 1


async def test_labelling_skips_already_labelled(session: AsyncSession) -> None:
    candidate = await _seed_candidate_with_pps(session, labeller_model="mock-llm-v1")
    original_statement = "original statement"
    candidate.problem_statement = original_statement
    await session.commit()

    report = await run_labelling(session, MockLLMAdapter())

    assert report.labelled == 0
    c = (await session.execute(select(OpportunityCandidate))).scalar_one()
    assert c.problem_statement == original_statement


async def test_labelling_assigns_category_when_known_slug(session: AsyncSession) -> None:
    cat = await _seed_wellness(session)
    candidate = await _seed_candidate_with_pps(session)

    llm = MockLLMAdapter()
    fixed_label = ClusterLabel(
        problem_statement="Habit tracking gap",
        audience="ADHD adults",
        why_now="AI makes it cheap",
        specificity=4,
        suggested_category_slug="wellness",
    )
    llm.label_cluster = AsyncMock(return_value=fixed_label)  # type: ignore

    await run_labelling(session, llm)

    c = (await session.execute(select(OpportunityCandidate))).scalar_one()
    assert c.category_id == cat.id


async def test_labelling_null_category_when_unknown_slug(session: AsyncSession) -> None:
    candidate = await _seed_candidate_with_pps(session)

    llm = MockLLMAdapter()
    fixed_label = ClusterLabel(
        problem_statement="Unknown domain",
        audience="someone",
        why_now="because",
        specificity=2,
        suggested_category_slug="spaceflight",
    )
    llm.label_cluster = AsyncMock(return_value=fixed_label)  # type: ignore

    await run_labelling(session, llm)

    c = (await session.execute(select(OpportunityCandidate))).scalar_one()
    assert c.category_id is None


async def test_labelling_propagates_category_to_source_items(session: AsyncSession) -> None:
    cat = await _seed_wellness(session)
    candidate = await _seed_candidate_with_pps(session)

    llm = MockLLMAdapter()
    fixed_label = ClusterLabel(
        problem_statement="Health gap",
        audience="users",
        why_now="now",
        specificity=3,
        suggested_category_slug="wellness",
    )
    llm.label_cluster = AsyncMock(return_value=fixed_label)  # type: ignore

    await run_labelling(session, llm)

    item = (await session.execute(select(SourceItem))).scalar_one()
    assert item.category_id == cat.id


async def test_labelling_continues_on_single_cluster_error(session: AsyncSession) -> None:
    cand_a = await _seed_candidate_with_pps(session, n_pps=2)
    cand_b = await _seed_candidate_with_pps(session, n_pps=2)

    call_count = [0]

    async def mock_label_cluster(evidence_texts, category_slugs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("LLM down for A")
        return ClusterLabel(
            problem_statement="B succeeds",
            audience="b users",
            why_now="now",
            specificity=3,
            suggested_category_slug=None,
        )

    llm = MockLLMAdapter()
    llm.label_cluster = mock_label_cluster  # type: ignore

    report = await run_labelling(session, llm)

    assert report.labelled == 1
    assert report.failed == 1

    labelled = (
        await session.execute(
            select(OpportunityCandidate).where(OpportunityCandidate.labeller_model.is_not(None))
        )
    ).scalars().all()
    assert len(labelled) == 1
    assert labelled[0].problem_statement == "B succeeds"

from datetime import UTC, datetime

from sqlalchemy import select

from app.agents.graph import build_graph, run_brief_for_niche
from app.db import get_session, init_db
from app.features.signal_aggregator import aggregate_daily_signals
from app.llm.mock_adapter import MockLLMAdapter
from app.models import Niche, OpportunityBrief, SourceItem


async def _seed(slug: str = "alpha") -> tuple[int, datetime]:
    """Returns (niche_id, now). Returns now so each test uses the same `as_of`
    that was used to create the seed data — fetcher/retriever node cutoffs are
    `utc_now()`-based, so we anchor to real now."""
    async with get_session() as session:
        n = Niche(slug=slug, name=slug.title(), category="devtools",
                  summary="seed", keywords_json=[slug])
        session.add(n)
        await session.commit()
        await session.refresh(n)
        nid = n.id

    now = datetime.now(UTC)
    async with get_session() as session:
        for i in range(3):
            session.add(SourceItem(
                source_type="github", external_id=f"g{i}",
                title=f"repo-{i}", body="useful body text",
                url=f"https://example.com/{i}",
                created_at=now, ingested_at=now,
                niche_id=nid, metadata_json={"stars": 50 + i},
            ))
        await session.commit()
    await aggregate_daily_signals(now)
    return nid, now


async def test_run_brief_for_niche_persists_brief():
    await init_db()
    nid, now = await _seed()

    brief_id = await run_brief_for_niche(
        nid, MockLLMAdapter(), as_of=now, triggered_by="scheduler"
    )

    assert brief_id is not None
    async with get_session() as session:
        rows = (await session.execute(
            select(OpportunityBrief).where(OpportunityBrief.niche_id == nid)
        )).scalars().all()
    assert len(rows) == 1
    brief = rows[0]
    assert brief.score_total is not None
    assert brief.headline
    assert brief.summary
    assert brief.evidence_json
    assert brief.forecast_label in ("Rising", "Stable", "Declining")
    assert brief.model_name == "MockLLMAdapter"
    assert isinstance(brief.score_breakdown_json, dict)


async def test_run_brief_for_niche_idempotent_same_day():
    await init_db()
    nid, now = await _seed()

    await run_brief_for_niche(nid, MockLLMAdapter(), as_of=now)
    await run_brief_for_niche(nid, MockLLMAdapter(), as_of=now)

    async with get_session() as session:
        rows = (await session.execute(
            select(OpportunityBrief).where(OpportunityBrief.niche_id == nid)
        )).scalars().all()
    assert len(rows) == 1


async def test_build_graph_can_be_invoked_directly():
    await init_db()
    nid, _ = await _seed()
    graph = build_graph(MockLLMAdapter())
    final_state = await graph.ainvoke({
        "niche": {"id": nid},
        "errors": [],
        "triggered_by": "command",
    })
    assert "brief" in final_state
    assert final_state["brief"]["headline"]


async def test_run_brief_marks_has_issues_when_summary_empty():
    """An adapter that returns empty summary → reviewer flags it; brief still persists."""
    await init_db()
    nid, now = await _seed()

    class EmptyAdapter(MockLLMAdapter):
        async def generate_brief(self, context):  # type: ignore[override]
            return ""

    await run_brief_for_niche(nid, EmptyAdapter(), as_of=now)

    async with get_session() as session:
        row = (await session.execute(
            select(OpportunityBrief).where(OpportunityBrief.niche_id == nid)
        )).scalar_one()
    assert row.has_issues is True

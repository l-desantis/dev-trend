"""LangGraph wiring for the opportunity-brief agent.

The graph is a linear pipeline (project doc §11):
    fetcher → retriever → forecaster → reporter → reviewer
The orchestrator `run_brief_for_niche` invokes the compiled graph and
persists the final state as an `OpportunityBrief` row.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete

from app.agents.nodes import (
    fetcher_node,
    forecaster_node,
    reporter_node,
    retriever_node,
    reviewer_node,
)
from app.agents.state import OpportunityState
from app.db import get_session
from app.llm.base import LLMAdapter
from app.models import OpportunityBrief

log = structlog.get_logger(__name__)


def build_graph(adapter: LLMAdapter):
    """Compile the linear opportunity-brief graph bound to `adapter`.

    The reporter is the only node that touches the LLM, so the adapter is
    captured in a closure here rather than passed through state.
    """
    sg: StateGraph = StateGraph(OpportunityState)

    async def _reporter(state: OpportunityState) -> OpportunityState:
        return await reporter_node(state, adapter=adapter)

    sg.add_node("fetcher", fetcher_node)
    sg.add_node("retriever", retriever_node)
    sg.add_node("forecaster", forecaster_node)
    sg.add_node("reporter", _reporter)
    sg.add_node("reviewer", reviewer_node)

    sg.add_edge(START, "fetcher")
    sg.add_edge("fetcher", "retriever")
    sg.add_edge("retriever", "forecaster")
    sg.add_edge("forecaster", "reporter")
    sg.add_edge("reporter", "reviewer")
    sg.add_edge("reviewer", END)

    return sg.compile()


def _start_of_day(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def _persist_brief(state: dict[str, Any], when: datetime) -> int:
    niche = state.get("niche") or {}
    brief = state.get("brief") or {}
    scorecard = state.get("scorecard") or {}
    day_start = _start_of_day(when)
    day_end = day_start + timedelta(days=1)

    async with get_session() as session:
        await session.execute(
            delete(OpportunityBrief).where(
                OpportunityBrief.niche_id == niche.get("id"),
                OpportunityBrief.generated_at >= day_start,
                OpportunityBrief.generated_at < day_end,
            )
        )
        row = OpportunityBrief(
            niche_id=niche["id"],
            headline=brief.get("headline"),
            summary=brief.get("summary"),
            score_total=scorecard.get("score_total"),
            score_breakdown_json=scorecard.get("breakdown"),
            evidence_json=brief.get("evidence"),
            forecast_label=brief.get("forecast_label"),
            has_issues=bool(brief.get("has_issues")),
            generated_at=when.astimezone(UTC) if when.tzinfo else when.replace(tzinfo=UTC),
            model_name=brief.get("model_name"),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def run_brief_for_niche(
    niche_id: int,
    adapter: LLMAdapter,
    *,
    as_of: datetime | None = None,
    triggered_by: str = "scheduler",
) -> int | None:
    """Run the full graph for one niche and persist an OpportunityBrief.

    Returns the new OpportunityBrief.id, or None if persistence was skipped
    because the graph couldn't produce a brief (e.g. niche missing).
    """
    when = as_of or datetime.now(UTC)
    graph = build_graph(adapter)
    initial: OpportunityState = {
        "niche": {"id": niche_id},
        "errors": [],
        "triggered_by": triggered_by,
    }
    final = await graph.ainvoke(initial)

    if not final.get("brief") or not (final.get("niche") or {}).get("id"):
        log.warning(
            "Skipping persistence — incomplete state",
            component="agent_orchestrator",
            niche_id=niche_id,
            errors=final.get("errors", []),
        )
        return None

    brief_id = await _persist_brief(final, when)
    log.info(
        "Brief persisted",
        component="agent_orchestrator",
        niche_id=niche_id,
        brief_id=brief_id,
        has_issues=final["brief"].get("has_issues"),
        triggered_by=triggered_by,
    )
    return brief_id

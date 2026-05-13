"""Tests for Stage 9 — brief_generation.py"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMAdapter
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.prompts import render_brief_prompt
from app.models import CandidateBrief, OpportunityCandidate, PainPoint, SourceItem
from app.pipeline.brief_generation import generate_briefs_for


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


class _PromptRenderingAdapter(LLMAdapter):
    """LLM adapter that calls render_brief_prompt and returns the rendered text."""

    @property
    def model_name(self) -> str:
        return "prompt-render-test"

    async def extract_pain_point(self, source_item_text: str) -> dict[str, Any]:
        return {"has_unmet_need": False, "problem_text": "", "audience": "", "urgency_cue": "", "current_workaround": ""}

    async def label_cluster(self, evidence_lines: str, categories: str) -> dict[str, Any]:
        return {"problem_statement": "", "audience": "", "why_now": "", "specificity": 1, "suggested_category_slug": None}

    async def generate_brief(self, context: dict[str, Any]) -> str:
        return render_brief_prompt(context)

    async def summarize_evidence(self, items: list[Any]) -> str:
        return ""

    async def review_brief(self, brief: str) -> dict[str, object]:
        return {}


async def test_render_brief_prompt_no_key_error(session: AsyncSession) -> None:
    c = await _seed(session)
    llm = _PromptRenderingAdapter()

    briefs = await generate_briefs_for(session, llm, [c])

    assert len(briefs) == 1
    rendered = briefs[0].summary
    assert c.problem_statement in rendered
    assert any(
        ev["source_type"] in rendered
        for ev in briefs[0].evidence_json
        if ev.get("source_type")
    )

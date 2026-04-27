"""LangGraph node functions for the opportunity-brief agent.

Each node accepts and returns the full `OpportunityState` dict so LangGraph
can merge keys back into the channel automatically.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from app.agents.state import OpportunityState
from app.config import get_settings
from app.db import get_session
from app.forecasting.scoring import score_niche
from app.llm.base import LLMAdapter
from app.models import Niche, NicheScoreHistory, NicheSignal, SourceItem

log = structlog.get_logger(__name__)

_SOURCE_ITEM_LOOKBACK_DAYS = 30
_SOURCE_ITEM_LIMIT = 50


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _record_error(state: OpportunityState, component: str, message: str) -> None:
    state.setdefault("errors", []).append({
        "component": component,
        "message": message,
        "at": _utcnow().isoformat(),
    })


def _serialise_item(item: SourceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "source_type": item.source_type,
        "external_id": item.external_id,
        "title": item.title,
        "body": item.body,
        "url": item.url,
        "created_at": (item.created_at or item.ingested_at).isoformat(),
        "ingested_at": item.ingested_at.isoformat(),
        "metadata": item.metadata_json or {},
    }


async def fetcher_node(state: OpportunityState) -> OpportunityState:
    niche_id = state.get("niche", {}).get("id")
    state.setdefault("errors", [])

    if not niche_id:
        _record_error(state, "fetcher_node", "missing niche.id")
        state["source_items"] = []
        return state

    async with get_session() as session:
        niche = (await session.execute(
            select(Niche).where(Niche.id == niche_id)
        )).scalar_one_or_none()

        if niche is None:
            _record_error(state, "fetcher_node", f"niche {niche_id} not found")
            state["niche"] = {"id": niche_id}
            state["source_items"] = []
            return state

        cutoff = _utcnow() - timedelta(days=_SOURCE_ITEM_LOOKBACK_DAYS)
        items = (await session.execute(
            select(SourceItem)
            .where(
                SourceItem.niche_id == niche_id,
                SourceItem.ingested_at >= cutoff,
            )
            .order_by(SourceItem.ingested_at.desc())
            .limit(_SOURCE_ITEM_LIMIT)
        )).scalars().all()

    state["niche"] = {
        "id": niche.id,
        "slug": niche.slug,
        "name": niche.name,
        "category": niche.category,
        "summary": niche.summary,
    }
    state["source_items"] = [_serialise_item(i) for i in items]
    log.info("Fetcher complete", component="fetcher_node",
             niche_id=niche_id, items=len(items))
    return state


_SIGNAL_LOOKBACK_DAYS = 7


async def retriever_node(state: OpportunityState) -> OpportunityState:
    niche_id = state.get("niche", {}).get("id")
    state.setdefault("errors", [])
    state["signals"] = []

    if not niche_id:
        _record_error(state, "retriever_node", "missing niche.id")
        return state

    cutoff = _utcnow() - timedelta(days=_SIGNAL_LOOKBACK_DAYS)
    async with get_session() as session:
        rows = (await session.execute(
            select(NicheSignal)
            .where(
                NicheSignal.niche_id == niche_id,
                NicheSignal.metric_timestamp >= cutoff,
            )
            .order_by(NicheSignal.metric_timestamp.desc())
        )).scalars().all()

    state["signals"] = [
        {
            "source_type": r.source_type,
            "metric_name": r.metric_name,
            "metric_value": float(r.metric_value),
            "metric_timestamp": r.metric_timestamp.isoformat(),
        }
        for r in rows
    ]
    log.info("Retriever complete", component="retriever_node",
             niche_id=niche_id, signals=len(rows))
    return state


def _forecast_label(growth_raw: float) -> str:
    if growth_raw > 0.05:
        return "Rising"
    if growth_raw < -0.05:
        return "Declining"
    return "Stable"


def _start_of_day(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def forecaster_node(
    state: OpportunityState, *, as_of: datetime | None = None
) -> OpportunityState:
    niche_id = state.get("niche", {}).get("id")
    state.setdefault("errors", [])
    when = as_of or _utcnow()
    today = _start_of_day(when)

    if not niche_id:
        _record_error(state, "forecaster_node", "missing niche.id")
        return state

    async with get_session() as session:
        row = (await session.execute(
            select(NicheScoreHistory)
            .where(
                NicheScoreHistory.niche_id == niche_id,
                NicheScoreHistory.scored_at == today,
            )
        )).scalar_one_or_none()

    if row is None:
        try:
            row = await score_niche(niche_id, when)
        except Exception as exc:
            _record_error(state, "forecaster_node", f"score_niche failed: {exc}")
            return state

    breakdown = row.score_breakdown_json or {}
    growth_raw = float(breakdown.get("growth", {}).get("raw", 0.0))

    state["scorecard"] = {
        "score_total": float(row.score_total),
        "breakdown": breakdown,
        "scored_at": row.scored_at.isoformat(),
    }
    state["forecast"] = {
        "label": _forecast_label(growth_raw),
        "slope": growth_raw,
    }
    log.info("Forecaster complete", component="forecaster_node",
             niche_id=niche_id, score_total=round(state["scorecard"]["score_total"], 2))
    return state


def _build_evidence(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out = []
    for item in items[:limit]:
        body = item.get("body") or ""
        excerpt = body[:240].replace("\n", " ").strip()
        out.append({
            "source_type": item.get("source_type"),
            "external_id": item.get("external_id"),
            "title": item.get("title"),
            "url": item.get("url"),
            "created_at": item.get("created_at"),
            "excerpt": excerpt,
        })
    return out


async def reporter_node(
    state: OpportunityState,
    *,
    adapter: LLMAdapter,
    timeout: float | None = None,
) -> OpportunityState:
    state.setdefault("errors", [])
    settings = get_settings()
    timeout_s = timeout if timeout is not None else settings.brief_per_niche_timeout_s
    max_evidence = settings.brief_max_evidence_items

    niche = state.get("niche", {}) or {}
    scorecard = state.get("scorecard", {}) or {}
    forecast = state.get("forecast", {}) or {}
    source_items = state.get("source_items", []) or []
    evidence = _build_evidence(source_items, max_evidence)

    score_total = float(scorecard.get("score_total", 0.0))
    headline = f"{niche.get('name', 'Unknown')} — Score {round(score_total)}"
    forecast_label = forecast.get("label", "Stable")
    model_name = type(adapter).__name__

    context = {
        "niche": niche,
        "scorecard": scorecard,
        "forecast": forecast,
        "evidence": evidence,
    }

    summary = ""
    try:
        summary = await asyncio.wait_for(
            adapter.generate_brief(context),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        _record_error(state, "reporter_node",
                      f"generate_brief timed out after {timeout_s}s")
        log.error("Reporter timeout", component="reporter_node",
                  niche_id=niche.get("id"), timeout_s=timeout_s)
    except Exception as exc:
        _record_error(state, "reporter_node", f"generate_brief failed: {exc}")
        log.error("Reporter failed", component="reporter_node",
                  niche_id=niche.get("id"), error=str(exc))

    state["brief"] = {
        "headline": headline,
        "summary": summary or "",
        "evidence": evidence,
        "forecast_label": forecast_label,
        "has_issues": False,  # reviewer_node sets this
        "model_name": model_name,
    }
    log.info("Reporter complete", component="reporter_node",
             niche_id=niche.get("id"),
             summary_chars=len(state["brief"]["summary"]))
    return state


_PLACEHOLDER_MARKERS = ("TODO", "[INSERT", "<placeholder>", "TBD")


async def reviewer_node(state: OpportunityState) -> OpportunityState:
    state.setdefault("errors", [])
    settings = get_settings()
    brief = state.get("brief") or {}
    gaps: list[str] = []

    summary = (brief.get("summary") or "").strip()
    if len(summary) < settings.brief_min_summary_chars:
        gaps.append(f"summary shorter than {settings.brief_min_summary_chars} chars")

    upper = summary.upper()
    for marker in _PLACEHOLDER_MARKERS:
        if marker.upper() in upper:
            gaps.append(f"summary contains placeholder '{marker}'")
            break

    if not brief.get("evidence"):
        gaps.append("no evidence items")

    brief["has_issues"] = bool(gaps)
    brief["gaps"] = gaps
    state["brief"] = brief

    if gaps:
        log.warning(
            "Brief has issues",
            component="reviewer_node",
            niche_id=state.get("niche", {}).get("id"),
            gaps=gaps,
        )
    else:
        log.info(
            "Brief reviewed clean",
            component="reviewer_node",
            niche_id=state.get("niche", {}).get("id"),
        )
    return state

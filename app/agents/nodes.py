"""LangGraph node functions for the opportunity-brief agent.

Each node returns only the state keys it updates; LangGraph merges them back
into the channel. The `errors` channel uses an `operator.add` reducer so
errors from every node accumulate rather than overwrite each other.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from app.agents.state import OpportunityState
from app.utils.datetime_utils import utc_now, utc_start_of_day
from app.config import get_settings
from app.db import get_session
from app.forecasting.scoring import score_niche
from app.llm.base import LLMAdapter
from app.models import Niche, NicheScoreHistory, NicheSignal, SourceItem

log = structlog.get_logger(__name__)

_SOURCE_ITEM_LOOKBACK_DAYS = 30
_SOURCE_ITEM_LIMIT = 50
_SIGNAL_LOOKBACK_DAYS = 7


def _error(component: str, message: str) -> dict[str, Any]:
    return {"component": component, "message": message, "at": utc_now().isoformat()}


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


async def fetcher_node(state: OpportunityState) -> dict[str, Any]:
    niche_id = (state.get("niche") or {}).get("id")

    if not niche_id:
        return {"source_items": [], "errors": [_error("fetcher_node", "missing niche.id")]}

    async with get_session() as session:
        niche = (await session.execute(
            select(Niche).where(Niche.id == niche_id)
        )).scalar_one_or_none()

        if niche is None:
            return {
                "niche": {"id": niche_id},
                "source_items": [],
                "errors": [_error("fetcher_node", f"niche {niche_id} not found")],
            }

        cutoff = utc_now() - timedelta(days=_SOURCE_ITEM_LOOKBACK_DAYS)
        items = (await session.execute(
            select(SourceItem)
            .where(
                SourceItem.niche_id == niche_id,
                SourceItem.ingested_at >= cutoff,
            )
            .order_by(SourceItem.ingested_at.desc())
            .limit(_SOURCE_ITEM_LIMIT)
        )).scalars().all()

    log.info("Fetcher complete", component="fetcher_node",
             niche_id=niche_id, items=len(items))
    return {
        "niche": {
            "id": niche.id,
            "slug": niche.slug,
            "name": niche.name,
            "category": niche.category,
            "summary": niche.summary,
        },
        "source_items": [_serialise_item(i) for i in items],
    }


async def retriever_node(state: OpportunityState) -> dict[str, Any]:
    niche_id = (state.get("niche") or {}).get("id")

    if not niche_id:
        return {"signals": [], "errors": [_error("retriever_node", "missing niche.id")]}

    cutoff = utc_now() - timedelta(days=_SIGNAL_LOOKBACK_DAYS)
    async with get_session() as session:
        rows = (await session.execute(
            select(NicheSignal)
            .where(
                NicheSignal.niche_id == niche_id,
                NicheSignal.metric_timestamp >= cutoff,
            )
            .order_by(NicheSignal.metric_timestamp.desc())
        )).scalars().all()

    log.info("Retriever complete", component="retriever_node",
             niche_id=niche_id, signals=len(rows))
    return {
        "signals": [
            {
                "source_type": r.source_type,
                "metric_name": r.metric_name,
                "metric_value": float(r.metric_value),
                "metric_timestamp": r.metric_timestamp.isoformat(),
            }
            for r in rows
        ],
    }


def _forecast_label(growth_raw: float) -> str:
    if growth_raw > 0.05:
        return "Rising"
    if growth_raw < -0.05:
        return "Declining"
    return "Stable"


async def forecaster_node(
    state: OpportunityState, *, as_of: datetime | None = None
) -> dict[str, Any]:
    niche_id = (state.get("niche") or {}).get("id")
    when = as_of or utc_now()
    today = utc_start_of_day(when)

    if not niche_id:
        return {"errors": [_error("forecaster_node", "missing niche.id")]}

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
            return {"errors": [_error("forecaster_node", f"score_niche failed: {exc}")]}

    breakdown = row.score_breakdown_json or {}
    growth_raw = float(breakdown.get("growth", {}).get("raw", 0.0))
    score_total = float(row.score_total)

    log.info("Forecaster complete", component="forecaster_node",
             niche_id=niche_id, score_total=round(score_total, 2))
    return {
        "scorecard": {
            "score_total": score_total,
            "breakdown": breakdown,
            "scored_at": row.scored_at.isoformat(),
        },
        "forecast": {
            "label": _forecast_label(growth_raw),
            "slope": growth_raw,
        },
    }


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
) -> dict[str, Any]:
    settings = get_settings()
    timeout_s = timeout if timeout is not None else settings.brief_per_niche_timeout_s
    max_evidence = settings.brief_max_evidence_items

    niche = state.get("niche") or {}
    scorecard = state.get("scorecard") or {}
    forecast = state.get("forecast") or {}
    source_items = state.get("source_items") or []
    evidence = _build_evidence(source_items, max_evidence)

    score_total = float(scorecard.get("score_total", 0.0))
    headline = f"{niche.get('name', 'Unknown')} — Score {round(score_total)}"
    forecast_label = forecast.get("label", "Stable")
    model_name = type(adapter).__name__

    context = {"niche": niche, "scorecard": scorecard, "forecast": forecast, "evidence": evidence}

    summary = ""
    errors: list[dict[str, Any]] = []
    try:
        summary = await asyncio.wait_for(adapter.generate_brief(context), timeout=timeout_s)
    except asyncio.TimeoutError:
        errors.append(_error("reporter_node", f"generate_brief timed out after {timeout_s}s"))
        log.error("Reporter timeout", component="reporter_node",
                  niche_id=niche.get("id"), timeout_s=timeout_s)
    except Exception as exc:
        errors.append(_error("reporter_node", f"generate_brief failed: {exc}"))
        log.error("Reporter failed", component="reporter_node",
                  niche_id=niche.get("id"), error=str(exc))

    log.info("Reporter complete", component="reporter_node",
             niche_id=niche.get("id"), summary_chars=len(summary))

    result: dict[str, Any] = {
        "brief": {
            "headline": headline,
            "summary": summary or "",
            "evidence": evidence,
            "forecast_label": forecast_label,
            "has_issues": False,  # reviewer_node sets this
            "model_name": model_name,
        },
    }
    if errors:
        result["errors"] = errors
    return result


_PLACEHOLDER_MARKERS = ("TODO", "[INSERT", "<placeholder>", "TBD")


async def reviewer_node(state: OpportunityState) -> dict[str, Any]:
    settings = get_settings()
    brief = dict(state.get("brief") or {})
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

    if gaps:
        log.warning("Brief has issues", component="reviewer_node",
                    niche_id=(state.get("niche") or {}).get("id"), gaps=gaps)
    else:
        log.info("Brief reviewed clean", component="reviewer_node",
                 niche_id=(state.get("niche") or {}).get("id"))

    return {"brief": brief}

"""Prompt templates for the opportunity-brief agent."""
from typing import Any

BRIEF_SYSTEM_PROMPT = (
    "You are DevTrend, an analyst writing concise market-opportunity briefs "
    "for indie developers. Briefs must be grounded in the evidence supplied; "
    "do not invent sources, numbers, or trends."
)

_BRIEF_TEMPLATE = """Write a 3-5 sentence opportunity brief for the niche below.

Niche: {name} ({category})
Slug: {slug}
Summary: {summary}

Composite score: {score:.0f}/100
- Growth (weight 0.41): raw={growth_raw}, normalized={growth_norm:.0f}
- Demand (weight 0.35): raw={demand_raw}, normalized={demand_norm:.0f}
- Novelty (weight 0.24): raw={novelty_raw}, normalized={novelty_norm:.0f}

Trend direction: {forecast_label} (7-day slope = {slope})

Evidence (top {evidence_count}):
{evidence_block}

Rules:
- 3-5 sentences total. No bullet lists, no markdown headings.
- Reference at least one specific evidence item by source type.
- State the trend direction explicitly.
- Do not invent metrics not shown above.
"""


def _format_evidence(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(no evidence available)"
    lines = []
    for i, item in enumerate(items, 1):
        src = item.get("source_type", "?")
        title = item.get("title", "(untitled)")
        excerpt = item.get("excerpt") or item.get("body") or ""
        excerpt = excerpt[:200].replace("\n", " ").strip()
        lines.append(f"{i}. [{src}] {title} — {excerpt}")
    return "\n".join(lines)


def render_brief_prompt(context: dict[str, Any]) -> str:
    """Render the user-prompt body for `LLMAdapter.generate_brief()`.

    `context` must contain `niche`, `scorecard`, `forecast`, `evidence`.
    """
    niche = context["niche"]
    scorecard = context["scorecard"]
    forecast = context["forecast"]
    evidence = context.get("evidence", [])
    breakdown = scorecard.get("breakdown", {})
    growth = breakdown.get("growth", {"raw": 0, "normalized": 0})
    demand = breakdown.get("demand", {"raw": 0, "normalized": 0})
    novelty = breakdown.get("novelty", {"raw": 0, "normalized": 0})
    return _BRIEF_TEMPLATE.format(
        name=niche.get("name", "?"),
        category=niche.get("category", "?"),
        slug=niche.get("slug", "?"),
        summary=niche.get("summary", "") or "(none)",
        score=scorecard.get("score_total", 0.0),
        growth_raw=round(float(growth.get("raw", 0)), 3),
        growth_norm=float(growth.get("normalized", 0)),
        demand_raw=round(float(demand.get("raw", 0)), 3),
        demand_norm=float(demand.get("normalized", 0)),
        novelty_raw=round(float(novelty.get("raw", 0)), 3),
        novelty_norm=float(novelty.get("normalized", 0)),
        forecast_label=forecast.get("label", "Stable"),
        slope=round(float(forecast.get("slope", 0.0)), 3),
        evidence_count=len(evidence),
        evidence_block=_format_evidence(evidence),
    )

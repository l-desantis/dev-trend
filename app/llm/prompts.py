"""Shared prompt templates for all LLM adapters (Ollama, NIM, etc.)."""
from typing import Any

# ---------------------------------------------------------------------------
# v4 extraction prompt
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM_PROMPT = (
    "You analyse a single piece of developer / market chatter and decide whether it "
    "contains an unmet-need signal that could justify a new app."
)

EXTRACT_USER_PROMPT = """\
Input text:
---
{text}
---

Return STRICT JSON with these keys:
- has_unmet_need: boolean
- problem_text: string (1 sentence, only if has_unmet_need=true; else "")
- audience: string (1 phrase, only if has_unmet_need=true; else "")
- urgency_cue: string (e.g. "repeated complaint", "specific deadline", "explicit ask"; "" if none)
- current_workaround: string ("" if not mentioned)

Examples of HIGH-signal text: complaints, "I wish there was an app that...",
"why is there no good X", repeated requests in a thread.
Examples of LOW-signal text: news headlines, tech announcements, marketing posts,
generic discussion. For these, set has_unmet_need=false and leave the strings
empty.

Reply with ONLY the JSON object, no prose.\
"""

# Kept for backward-compat with OllamaAdapter (single-message format without system role)
EXTRACT_PROMPT = """\
You analyse a single piece of developer / market chatter and decide whether it
contains an unmet-need signal that could justify a new app.

Input text:
---
{text}
---

Return STRICT JSON with these keys:
- has_unmet_need: boolean
- problem_text: string (1 sentence, only if has_unmet_need=true; else "")
- audience: string (1 phrase, only if has_unmet_need=true; else "")
- urgency_cue: string (e.g. "repeated complaint", "specific deadline", "explicit ask"; "" if none)
- current_workaround: string ("" if not mentioned)

Examples of HIGH-signal text: complaints, "I wish there was an app that...",
"why is there no good X", repeated requests in a thread.
Examples of LOW-signal text: news headlines, tech announcements, marketing posts,
generic discussion. For these, set has_unmet_need=false and leave the strings
empty.

Reply with ONLY the JSON object, no prose.\
"""

# ---------------------------------------------------------------------------
# v4 cluster labelling prompt
# ---------------------------------------------------------------------------

LABEL_CLUSTER_PROMPT = """\
You are labelling a cluster of pain points extracted from developer & user
chatter. Produce a concrete app-opportunity hypothesis.

Cluster evidence (one item per line):
{evidence_lines}

Available categories: {categories}

Return STRICT JSON with these keys:
- problem_statement: 1 sentence describing the opportunity (mid-precision —
  specific enough to be actionable, broad enough to allow exploration).
- audience: 1 phrase describing who has the problem.
- why_now: 1 sentence on what makes this timely (e.g. tech enabler, emerging
  workflow, repeated recent mention).
- specificity: integer 1–5. 5 = a concrete app idea with clear scope; 1 = vague,
  could mean many different products. Be honest — vague clusters get filtered.
- suggested_category_slug: one of the available categories, or null.

Reply with ONLY the JSON object, no prose.\
"""

# ---------------------------------------------------------------------------
# v3/v4 brief generation prompts (used by generate_brief)
# ---------------------------------------------------------------------------

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
    """Render the user-prompt body for `LLMAdapter.generate_brief()`."""
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

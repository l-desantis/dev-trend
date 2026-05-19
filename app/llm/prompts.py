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
# v4 brief generation prompts (used by generate_brief)
# ---------------------------------------------------------------------------

BRIEF_SYSTEM_PROMPT = (
    "You are DevTrend, an analyst writing concise market-opportunity briefs "
    "for indie developers. Briefs must be grounded in the evidence supplied; "
    "do not invent sources, numbers, or trends."
)

_BRIEF_TEMPLATE = """Write a 3-5 sentence opportunity brief for the following developer pain-point opportunity.

Problem: {problem_statement}
Audience: {audience}
Why now: {why_now}

Evidence (top {evidence_count}):
{evidence_block}

Rules:
- 3-5 sentences total. No bullet lists, no markdown headings.
- Reference at least one specific evidence item by source type.
- Do not invent metrics or sources not shown above.
"""


def _format_evidence(items: list[dict[str, Any]]) -> str:
    if not items:
        return "(no evidence available)"
    lines = []
    for i, item in enumerate(items, 1):
        src = item.get("source_type", "?")
        excerpt = (item.get("excerpt") or item.get("problem_text") or "")[:200].replace("\n", " ").strip()
        lines.append(f"{i}. [{src}] {excerpt}")
    return "\n".join(lines)


def render_brief_prompt(context: dict[str, Any]) -> str:
    """Render the user-prompt body for `LLMAdapter.generate_brief()`."""
    evidence = context.get("evidence", [])
    return _BRIEF_TEMPLATE.format(
        problem_statement=context.get("problem_statement", ""),
        audience=context.get("audience", ""),
        why_now=context.get("why_now", ""),
        evidence_count=len(evidence),
        evidence_block=_format_evidence(evidence),
    )


# ---------------------------------------------------------------------------
# Validation keyword extraction prompt
# ---------------------------------------------------------------------------

KEYWORD_EXTRACT_SYSTEM_PROMPT = (
    "You extract specific GitHub search keywords from product opportunity descriptions. "
    "Return only domain-specific nouns: product categories, technologies, problem domains. "
    "Never return verbs, adjectives, or generic terms like 'app', 'tool', 'users', 'developers'."
)

KEYWORD_EXTRACT_USER_PROMPT = """\
Problem: {problem}
Audience: {audience}

Return 3-5 specific keywords suitable for GitHub repository search.
Good examples: adhd, fintech, leetcode, react-native, ecommerce, multilingual, procurement, wearable
Bad examples: struggle, create, accessible, multiple, users, developers, platform, manage

Return STRICT JSON: {{"keywords": ["word1", "word2", "word3"]}}
Reply with ONLY the JSON object, no prose.\
"""

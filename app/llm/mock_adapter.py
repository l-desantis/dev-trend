import re
from typing import Any

from app.llm.base import LLMAdapter
from app.llm.schemas import ClusterLabel, PainPointDraft

_FIXTURE_BRIEF = (
    "**Mock Opportunity Brief**\n\n"
    "This niche shows strong growth signals with rising developer interest. "
    "Multiple sources confirm consistent mention growth over the past 7 days. "
    "Score: 75/100. Trend: ↑ Rising.\n\n"
    "Evidence: GitHub repos gaining stars, HN discussion up, Reddit mentions increasing."
)

_HIGH_SIGNAL_KEYWORDS = ["wish", "why is there no", "should be a way to"]


class MockLLMAdapter(LLMAdapter):
    @property
    def model_name(self) -> str:
        return "mock-llm-v1"

    async def generate_brief(self, context: dict[str, Any]) -> str:
        return _FIXTURE_BRIEF

    async def summarize_evidence(self, items: list[Any]) -> str:
        return "Mock evidence summary."

    async def review_brief(self, brief: str) -> dict[str, object]:
        return {"has_issues": False, "gaps": []}

    async def extract_pain_point(
        self,
        source_item_text: str,
        *,
        model_hint: str | None = None,
    ) -> PainPointDraft:
        text_lower = source_item_text.lower()
        if any(kw in text_lower for kw in _HIGH_SIGNAL_KEYWORDS):
            return PainPointDraft(
                has_unmet_need=True,
                problem_text=f"User wants: {source_item_text[:80]}",
                audience="users mentioned in the text",
                urgency_cue="repeated complaint",
                current_workaround=None,
            )
        return PainPointDraft(has_unmet_need=False)

    async def label_cluster(
        self,
        evidence_texts: list[str],
        category_slugs: list[str],
    ) -> ClusterLabel:
        first_words = " · ".join(t.split(".")[0][:30] for t in evidence_texts[:3])
        return ClusterLabel(
            problem_statement=f"Cluster: {first_words}",
            audience="mocked audience",
            why_now="mocked why-now",
            specificity=min(5, max(1, len(evidence_texts) // 2)),
            suggested_category_slug=(category_slugs[0] if category_slugs else None),
        )

    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        # 4+ chars keeps cohort tokens like "adhd" — the whole point of the LLM path
        # over the stopword path is to surface domain-specific short nouns.
        combined = f"{audience or ''} {problem}"
        words = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", combined)]
        seen: set[str] = set()
        result: list[str] = []
        for w in words:
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result[:4]

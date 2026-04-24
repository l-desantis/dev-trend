from app.llm.base import LLMAdapter

_FIXTURE_BRIEF = (
    "**Mock Opportunity Brief**\n\n"
    "This niche shows strong growth signals with rising developer interest. "
    "Multiple sources confirm consistent mention growth over the past 7 days. "
    "Score: 75/100. Trend: ↑ Rising.\n\n"
    "Evidence: GitHub repos gaining stars, HN discussion up, Reddit mentions increasing."
)


class MockLLMAdapter(LLMAdapter):
    async def generate_brief(self, context: dict) -> str:
        return _FIXTURE_BRIEF

    async def summarize_evidence(self, items: list) -> str:
        return "Mock evidence summary."

    async def review_brief(self, brief: str) -> dict:
        return {"has_issues": False, "gaps": []}

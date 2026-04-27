import pytest

from app.llm.mock_adapter import MockLLMAdapter


@pytest.fixture
def adapter() -> MockLLMAdapter:
    return MockLLMAdapter()


async def test_generate_brief_is_deterministic(adapter: MockLLMAdapter) -> None:
    first = await adapter.generate_brief({})
    second = await adapter.generate_brief({"niche": "anything"})
    assert first == second
    assert len(first) > 0


async def test_summarize_evidence(adapter: MockLLMAdapter) -> None:
    result = await adapter.summarize_evidence([])
    assert isinstance(result, str)
    assert len(result) > 0


async def test_review_brief_no_issues(adapter: MockLLMAdapter) -> None:
    result = await adapter.review_brief("some brief text")
    assert result["has_issues"] is False
    assert result["gaps"] == []


from app.agents.state import OpportunityState


def test_opportunity_state_accepts_all_documented_keys():
    state: OpportunityState = {
        "niche": {"id": 1, "slug": "x", "name": "X"},
        "source_items": [],
        "signals": [],
        "forecast": {"label": "Stable", "slope": 0.0},
        "scorecard": {"score_total": 50.0, "breakdown": {}},
        "brief": {"headline": "h", "summary": "s", "evidence": [],
                  "forecast_label": "Stable", "has_issues": False,
                  "model_name": "qwen2.5"},
        "errors": [],
        "triggered_by": "scheduler",
    }
    assert state["triggered_by"] == "scheduler"
    assert state["niche"]["slug"] == "x"


def test_opportunity_state_allows_partial_population():
    state: OpportunityState = {"niche": {"id": 1}, "errors": []}
    assert "source_items" not in state

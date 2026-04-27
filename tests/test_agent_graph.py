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


from app.agents.prompts import render_brief_prompt


def test_render_brief_prompt_includes_niche_and_score():
    context = {
        "niche": {"name": "AI Habit Trackers", "slug": "ai-habit",
                  "category": "wellness", "summary": "Habit-tracking apps"},
        "scorecard": {"score_total": 84.2, "breakdown": {
            "growth": {"raw": 0.5, "normalized": 78.0},
            "demand": {"raw": 12.0, "normalized": 65.0},
            "novelty": {"raw": 0.9, "normalized": 90.0},
        }},
        "forecast": {"label": "Rising", "slope": 0.5},
        "evidence": [
            {"source_type": "github", "title": "habit-tracker repo",
             "url": "https://example.com", "excerpt": "Stars rising"},
        ],
    }
    prompt = render_brief_prompt(context)
    assert "AI Habit Trackers" in prompt
    assert "84" in prompt
    assert "Rising" in prompt
    assert "habit-tracker repo" in prompt
    assert "github" in prompt


def test_render_brief_prompt_handles_no_evidence():
    context = {
        "niche": {"name": "X", "slug": "x", "category": "c", "summary": ""},
        "scorecard": {"score_total": 0.0, "breakdown": {
            "growth": {"raw": 0, "normalized": 0},
            "demand": {"raw": 0, "normalized": 0},
            "novelty": {"raw": 0, "normalized": 0},
        }},
        "forecast": {"label": "Stable", "slope": 0.0},
        "evidence": [],
    }
    prompt = render_brief_prompt(context)
    assert "X" in prompt
    assert "no evidence" in prompt.lower() or "none" in prompt.lower()

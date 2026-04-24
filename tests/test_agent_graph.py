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

"""Tests for OpenAIAdapter."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.llm.openai_adapter import OpenAIAdapter
from app.llm.schemas import ClusterLabel, PainPointDraft


@pytest.fixture
def adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="test-key", model="gpt-4.1-nano")


def _parse_completion(parsed_obj) -> MagicMock:
    msg = MagicMock()
    msg.parsed = parsed_obj
    choice = MagicMock()
    choice.message = msg
    completion = MagicMock()
    completion.choices = [choice]
    return completion


def _create_completion(content: str) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    completion = MagicMock()
    completion.choices = [choice]
    return completion


@pytest.mark.asyncio
async def test_extract_pain_point_happy_path(adapter: OpenAIAdapter) -> None:
    expected = PainPointDraft(
        has_unmet_need=True,
        problem_text="Need better debugging tool",
        audience="backend developers",
        urgency_cue="repeated complaint",
        current_workaround="",
    )
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=_parse_completion(expected)),
    ):
        result = await adapter.extract_pain_point("I wish there was a better tool")

    assert result.has_unmet_need is True
    assert result.problem_text == "Need better debugging tool"


@pytest.mark.asyncio
async def test_extract_pain_point_exception_returns_no_signal(adapter: OpenAIAdapter) -> None:
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(side_effect=Exception("API error")),
    ):
        result = await adapter.extract_pain_point("some text")

    assert isinstance(result, PainPointDraft)
    assert result.has_unmet_need is False


@pytest.mark.asyncio
async def test_extract_pain_point_content_filter_returns_no_signal(adapter: OpenAIAdapter) -> None:
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=_parse_completion(None)),
    ):
        result = await adapter.extract_pain_point("some text")

    assert result.has_unmet_need is False


@pytest.mark.asyncio
async def test_label_cluster_happy_path(adapter: OpenAIAdapter) -> None:
    expected = ClusterLabel(
        problem_statement="Developers need better debugging tools",
        audience="backend developers",
        why_now="AI-powered IDEs are emerging",
        specificity=4,
        suggested_category_slug="developer-tools",
    )
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=_parse_completion(expected)),
    ):
        result = await adapter.label_cluster(["text 1", "text 2"], ["developer-tools"])

    assert result.problem_statement == "Developers need better debugging tools"
    assert result.specificity == 4


@pytest.mark.asyncio
async def test_generate_brief_returns_string(adapter: OpenAIAdapter) -> None:
    with patch.object(
        adapter._client.chat.completions,
        "create",
        new=AsyncMock(return_value=_create_completion("This is the brief content for this niche.")),
    ):
        result = await adapter.generate_brief({"niche": "test", "evidence": []})

    assert isinstance(result, str)
    assert len(result) > 0


def test_model_name_uses_openai_prefix(adapter: OpenAIAdapter) -> None:
    assert adapter.model_name == "openai:gpt-4.1-nano"


@pytest.mark.asyncio
async def test_openai_extract_search_keywords_happy_path(adapter: OpenAIAdapter) -> None:
    from app.llm.schemas import SearchKeywords
    parsed = SearchKeywords(keywords=["adhd", "habit", "tracker"])
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=_parse_completion(parsed)),
    ):
        result = await adapter.extract_search_keywords(
            "habit tracking apps fail to engage ADHD adults",
            "ADHD adults",
        )
    assert result == ["adhd", "habit", "tracker"]


@pytest.mark.asyncio
async def test_openai_extract_search_keywords_exception_returns_empty(adapter: OpenAIAdapter) -> None:
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(side_effect=Exception("API error")),
    ):
        result = await adapter.extract_search_keywords("some problem", None)
    assert result == []


@pytest.mark.asyncio
async def test_openai_extract_search_keywords_none_response_returns_empty(adapter: OpenAIAdapter) -> None:
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=_parse_completion(None)),
    ):
        result = await adapter.extract_search_keywords("some problem", None)
    assert result == []

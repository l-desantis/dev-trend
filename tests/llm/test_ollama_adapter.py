"""Tests for OllamaAdapter v4 methods (extract_pain_point + label_cluster)."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.llm.ollama_adapter import OllamaAdapter
from app.llm.schemas import ClusterLabel, PainPointDraft


def _adapter() -> OllamaAdapter:
    return OllamaAdapter(base_url="http://localhost:11434", model="qwen2.5")


def _chat_response(text: str):
    return SimpleNamespace(message=SimpleNamespace(content=text))


async def test_extract_pain_point_returns_draft() -> None:
    payload = json.dumps({
        "has_unmet_need": True,
        "problem_text": "No good habit tracker for ADHD adults",
        "audience": "ADHD adults",
        "urgency_cue": "repeated complaint",
        "current_workaround": "",
    })
    adapter = _adapter()
    with patch.object(adapter._client, "chat", new=AsyncMock(return_value=_chat_response(payload))):
        draft = await adapter.extract_pain_point("I wish there was a habit tracker for ADHD")

    assert isinstance(draft, PainPointDraft)
    assert draft.has_unmet_need is True
    assert draft.problem_text == "No good habit tracker for ADHD adults"
    assert draft.audience == "ADHD adults"


async def test_extract_pain_point_invalid_json_falls_back_to_no_signal() -> None:
    adapter = _adapter()
    with patch.object(adapter._client, "chat", new=AsyncMock(return_value=_chat_response("not json at all"))):
        draft = await adapter.extract_pain_point("some text")

    assert draft.has_unmet_need is False


async def test_extract_pain_point_truncates_long_text() -> None:
    # Text that has a distinct marker past the 4000-char cut-off
    long_text = "a" * 4000 + "SHOULD_NOT_APPEAR_IN_PROMPT"
    captured: list[str] = []

    async def fake_chat(**kwargs):
        msg = kwargs["messages"][0]["content"]
        captured.append(msg)
        return _chat_response(json.dumps({"has_unmet_need": False, "problem_text": "", "audience": ""}))

    adapter = _adapter()
    with patch.object(adapter._client, "chat", new=fake_chat):
        await adapter.extract_pain_point(long_text)

    assert captured, "chat was never called"
    assert "SHOULD_NOT_APPEAR_IN_PROMPT" not in captured[0]


async def test_label_cluster_returns_label() -> None:
    payload = json.dumps({
        "problem_statement": "Habit tracking gap for ADHD",
        "audience": "ADHD adults",
        "why_now": "AI makes personalisation cheap",
        "specificity": 4,
        "suggested_category_slug": "wellness",
    })
    adapter = _adapter()
    with patch.object(adapter._client, "chat", new=AsyncMock(return_value=_chat_response(payload))):
        label = await adapter.label_cluster(
            ["Need habit tracker", "ADHD productivity tool missing"],
            ["wellness", "productivity"],
        )

    assert isinstance(label, ClusterLabel)
    assert label.problem_statement == "Habit tracking gap for ADHD"
    assert label.specificity == 4
    assert label.suggested_category_slug == "wellness"


async def test_label_cluster_invalid_specificity_raises() -> None:
    payload = json.dumps({
        "problem_statement": "vague",
        "audience": "someone",
        "why_now": "because",
        "specificity": 7,  # out of range
        "suggested_category_slug": None,
    })
    adapter = _adapter()
    from pydantic import ValidationError
    with patch.object(adapter._client, "chat", new=AsyncMock(return_value=_chat_response(payload))):
        with pytest.raises(ValidationError):
            await adapter.label_cluster(["evidence"], ["wellness"])


async def test_ollama_extract_search_keywords_happy_path() -> None:
    import json
    adapter = _adapter()
    payload = json.dumps({"keywords": ["adhd", "habit", "tracker"]})
    with patch.object(adapter._client, "chat", new=AsyncMock(return_value=_chat_response(payload))):
        result = await adapter.extract_search_keywords(
            "habit tracking apps fail to engage ADHD adults",
            "ADHD adults",
        )
    assert result == ["adhd", "habit", "tracker"]


async def test_ollama_extract_search_keywords_bad_json_returns_empty() -> None:
    adapter = _adapter()
    with patch.object(adapter._client, "chat", new=AsyncMock(return_value=_chat_response("not json"))):
        result = await adapter.extract_search_keywords("some problem", None)
    assert result == []

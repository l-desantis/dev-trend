from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.llm.ollama_adapter import OllamaAdapter


def _fake_chat_response(text: str):
    return SimpleNamespace(message=SimpleNamespace(content=text))


async def test_generate_brief_returns_model_text():
    adapter = OllamaAdapter(base_url="http://x", model="qwen2.5")
    with patch.object(
        adapter._client, "chat", new=AsyncMock(return_value=_fake_chat_response("hello world"))
    ) as chat:
        out = await adapter.generate_brief({
            "niche": {"name": "X", "slug": "x", "category": "c", "summary": ""},
            "scorecard": {"score_total": 50.0, "breakdown": {
                "growth": {"raw": 0, "normalized": 50},
                "demand": {"raw": 0, "normalized": 50},
                "novelty": {"raw": 0, "normalized": 50},
            }},
            "forecast": {"label": "Stable", "slope": 0.0},
            "evidence": [],
        })
    assert out == "hello world"
    chat.assert_awaited_once()
    kwargs = chat.await_args.kwargs
    assert kwargs["model"] == "qwen2.5"
    messages = kwargs["messages"]
    assert any(m["role"] == "system" for m in messages)
    assert any("X" in m["content"] for m in messages)


async def test_summarize_evidence_returns_string():
    adapter = OllamaAdapter(base_url="http://x", model="qwen2.5")
    with patch.object(
        adapter._client, "chat", new=AsyncMock(return_value=_fake_chat_response("summary"))
    ):
        out = await adapter.summarize_evidence([{"title": "t", "source_type": "github"}])
    assert out == "summary"


async def test_review_brief_returns_no_issues_dict():
    """The LLM-side review is heuristic-only in Phase 1; the adapter just
    delegates to a deterministic check so callers can rely on the shape."""
    adapter = OllamaAdapter(base_url="http://x", model="qwen2.5")
    out = await adapter.review_brief("a sufficiently long brief " * 5)
    assert isinstance(out, dict)
    assert out["has_issues"] is False
    assert out["gaps"] == []


async def test_review_brief_flags_short_text():
    adapter = OllamaAdapter(base_url="http://x", model="qwen2.5")
    out = await adapter.review_brief("short")
    assert out["has_issues"] is True
    assert "summary" in " ".join(out["gaps"]).lower() or out["gaps"]

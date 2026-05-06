"""Tests for NvidiaNimAdapter."""
import json
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.llm.nim_adapter import NvidiaNimAdapter
from app.llm.schemas import PainPointDraft


def _make_adapter() -> NvidiaNimAdapter:
    return NvidiaNimAdapter(api_key="test-key", model="meta/llama-3.1-70b-instruct")


def _chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def adapter():
    return _make_adapter()


@pytest.mark.asyncio
async def test_nim_adapter_calls_correct_endpoint(adapter):
    payload = json.dumps({
        "has_unmet_need": True,
        "problem_text": "Need better tool",
        "audience": "developers",
        "urgency_cue": "repeated complaint",
        "current_workaround": "",
    })
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _chat_response(payload)
    mock_response.raise_for_status = MagicMock()

    with patch.object(adapter._client, "post", new=AsyncMock(return_value=mock_response)) as mock_post:
        result = await adapter.extract_pain_point("I wish there was a better tool")

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "/chat/completions"
    posted_json = call_kwargs[1]["json"]
    assert posted_json["model"] == "meta/llama-3.1-70b-instruct"
    assert any(m["role"] == "system" for m in posted_json["messages"])
    assert result.has_unmet_need is True


@pytest.mark.asyncio
async def test_nim_adapter_handles_5xx_with_retry(adapter):
    payload = json.dumps({"has_unmet_need": False})

    fail_response = MagicMock()
    fail_response.status_code = 503

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.json.return_value = _chat_response(payload)
    ok_response.raise_for_status = MagicMock()

    call_count = 0

    async def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return fail_response
        return ok_response

    with patch.object(adapter._client, "post", side_effect=fake_post):
        result = await adapter.extract_pain_point("some text")

    assert call_count == 2
    assert result.has_unmet_need is False


@pytest.mark.asyncio
async def test_nim_adapter_invalid_json_falls_back_to_no_signal(adapter):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _chat_response("not valid json {{")
    mock_response.raise_for_status = MagicMock()

    with patch.object(adapter._client, "post", new=AsyncMock(return_value=mock_response)):
        result = await adapter.extract_pain_point("some text")

    assert isinstance(result, PainPointDraft)
    assert result.has_unmet_need is False


def test_nim_adapter_model_name_namespaced(adapter):
    assert adapter.model_name == "nim:meta/llama-3.1-70b-instruct"

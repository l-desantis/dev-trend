"""Tests for OpenAIEmbeddingAdapter."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.llm.openai_embedding_adapter import OpenAIEmbeddingAdapter


@pytest.fixture
def adapter() -> OpenAIEmbeddingAdapter:
    return OpenAIEmbeddingAdapter(api_key="test-key")


def _embed_response(vectors: list[list[float]]) -> MagicMock:
    data = [MagicMock(embedding=v) for v in vectors]
    resp = MagicMock()
    resp.data = data
    return resp


@pytest.mark.asyncio
async def test_embed_returns_vectors(adapter: OpenAIEmbeddingAdapter) -> None:
    vectors = [[0.1] * 1536, [0.2] * 1536]
    with patch.object(
        adapter._client.embeddings,
        "create",
        new=AsyncMock(return_value=_embed_response(vectors)),
    ):
        result = await adapter.embed(["text a", "text b"])

    assert len(result) == 2
    assert len(result[0]) == 1536
    assert len(result[1]) == 1536


@pytest.mark.asyncio
async def test_embed_batch_count_matches_input(adapter: OpenAIEmbeddingAdapter) -> None:
    texts = ["a", "b", "c"]
    with patch.object(
        adapter._client.embeddings,
        "create",
        new=AsyncMock(return_value=_embed_response([[0.1] * 1536] * 3)),
    ):
        result = await adapter.embed(texts)

    assert len(result) == len(texts)


def test_dim_is_1536(adapter: OpenAIEmbeddingAdapter) -> None:
    assert adapter.dim == 1536


def test_model_name_uses_openai_prefix(adapter: OpenAIEmbeddingAdapter) -> None:
    assert adapter.model_name == "openai:text-embedding-3-small"

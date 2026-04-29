"""Tests for EmbeddingAdapter implementations."""
import pytest

from app.llm.mock_embedding_adapter import MockEmbeddingAdapter


@pytest.fixture
def mock_embedder() -> MockEmbeddingAdapter:
    return MockEmbeddingAdapter()


async def test_mock_embedding_deterministic(mock_embedder: MockEmbeddingAdapter) -> None:
    text = "habit tracker for ADHD"
    v1 = await mock_embedder.embed([text])
    v2 = await mock_embedder.embed([text])
    assert v1 == v2


async def test_mock_embedding_different_texts_differ(mock_embedder: MockEmbeddingAdapter) -> None:
    v1 = (await mock_embedder.embed(["habit tracker"]))[0]
    v2 = (await mock_embedder.embed(["finance app"]))[0]
    assert v1 != v2


async def test_mock_embedding_dim(mock_embedder: MockEmbeddingAdapter) -> None:
    vecs = await mock_embedder.embed(["test"])
    assert len(vecs[0]) == mock_embedder.dim


async def test_mock_embedding_batch(mock_embedder: MockEmbeddingAdapter) -> None:
    texts = ["text A", "text B", "text C"]
    vecs = await mock_embedder.embed(texts)
    assert len(vecs) == 3
    assert all(len(v) == mock_embedder.dim for v in vecs)

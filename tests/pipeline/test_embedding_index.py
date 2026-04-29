"""Tests for EmbeddingIndex (cosine similarity)."""
import pytest

from app.pipeline.embedding_index import EmbeddingIndex


def test_index_nearest_returns_self_at_top() -> None:
    vec = [1.0, 0.0, 0.0, 0.0]
    idx = EmbeddingIndex([42], [vec])
    results = idx.nearest(vec, k=1)
    assert len(results) == 1
    assert results[0][0] == 42
    assert abs(results[0][1] - 1.0) < 1e-5


def test_index_threshold_filters() -> None:
    idx = EmbeddingIndex([1], [[1.0, 0.0, 0.0]])
    results = idx.nearest([0.0, 1.0, 0.0], k=1, threshold=0.5)
    assert results == []


def test_index_empty() -> None:
    idx = EmbeddingIndex([], [])
    assert idx.nearest([1.0, 0.0], k=1) == []
    assert len(idx) == 0


def test_index_handles_zero_vector() -> None:
    idx = EmbeddingIndex([1], [[1.0, 0.0]])
    results = idx.nearest([0.0, 0.0], k=1)
    assert isinstance(results, list)


def test_index_multiple_vectors() -> None:
    idx = EmbeddingIndex([1, 2, 3], [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    results = idx.nearest([1, 0, 0], k=2, threshold=0.0)
    assert results[0][0] == 1
    assert abs(results[0][1] - 1.0) < 1e-5

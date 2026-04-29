"""Tests for the adapter factory."""
import pytest

from app.config import Settings
from app.llm.factory import make_embedding_adapter, make_llm_adapter
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.mock_embedding_adapter import MockEmbeddingAdapter


def _settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        **kwargs,
    )


def test_make_llm_adapter_mock() -> None:
    s = _settings(llm_provider="mock")
    adapter = make_llm_adapter(s)
    assert isinstance(adapter, MockLLMAdapter)


def test_make_llm_adapter_nim_raises() -> None:
    s = _settings(llm_provider="nim")
    with pytest.raises(NotImplementedError, match="Plan C"):
        make_llm_adapter(s)


def test_make_llm_adapter_unknown_raises() -> None:
    s = Settings.model_construct(llm_provider="unknown")  # bypass Literal validation
    with pytest.raises(ValueError, match="unknown llm_provider"):
        make_llm_adapter(s)


def test_make_embedding_adapter_mock() -> None:
    s = _settings(embedding_provider="mock")
    adapter = make_embedding_adapter(s)
    assert isinstance(adapter, MockEmbeddingAdapter)


def test_make_embedding_adapter_nim_raises() -> None:
    s = _settings(embedding_provider="nim")
    with pytest.raises(NotImplementedError, match="Plan C"):
        make_embedding_adapter(s)


def test_make_embedding_adapter_unknown_raises() -> None:
    s = Settings.model_construct(embedding_provider="unknown")  # bypass Literal validation
    with pytest.raises(ValueError, match="unknown embedding_provider"):
        make_embedding_adapter(s)

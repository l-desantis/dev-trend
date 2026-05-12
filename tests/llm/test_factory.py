"""Tests for the adapter factory."""
import pytest

from app.config import Settings
from app.llm.factory import make_embedding_adapter, make_llm_adapter
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.mock_embedding_adapter import MockEmbeddingAdapter
from app.llm.nim_adapter import NvidiaNimAdapter
from app.llm.nim_embedding_adapter import NvidiaNimEmbeddingAdapter
from app.llm.openai_adapter import OpenAIAdapter
from app.llm.openai_embedding_adapter import OpenAIEmbeddingAdapter


def _settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        **kwargs,
    )


def test_make_llm_adapter_mock() -> None:
    s = _settings(llm_provider="mock")
    adapter = make_llm_adapter(s)
    assert isinstance(adapter, MockLLMAdapter)


def test_factory_returns_nim_adapter_when_configured() -> None:
    s = _settings(llm_provider="nim", nim_api_key="sk-test")
    adapter = make_llm_adapter(s)
    assert isinstance(adapter, NvidiaNimAdapter)


def test_factory_raises_when_nim_key_missing() -> None:
    s = _settings(llm_provider="nim", nim_api_key="")
    with pytest.raises(ValueError, match="NIM_API_KEY required"):
        make_llm_adapter(s)


def test_make_llm_adapter_unknown_raises() -> None:
    s = Settings.model_construct(llm_provider="unknown")
    with pytest.raises(ValueError, match="unknown llm_provider"):
        make_llm_adapter(s)


def test_make_embedding_adapter_mock() -> None:
    s = _settings(embedding_provider="mock")
    adapter = make_embedding_adapter(s)
    assert isinstance(adapter, MockEmbeddingAdapter)


def test_factory_returns_nim_embedding_adapter_when_configured() -> None:
    s = _settings(embedding_provider="nim", nim_api_key="sk-test")
    adapter = make_embedding_adapter(s)
    assert isinstance(adapter, NvidiaNimEmbeddingAdapter)


def test_factory_raises_when_nim_embedding_key_missing() -> None:
    s = _settings(embedding_provider="nim", nim_api_key="")
    with pytest.raises(ValueError, match="NIM_API_KEY required"):
        make_embedding_adapter(s)


def test_make_embedding_adapter_unknown_raises() -> None:
    s = Settings.model_construct(embedding_provider="unknown")
    with pytest.raises(ValueError, match="unknown embedding_provider"):
        make_embedding_adapter(s)


def test_factory_returns_openai_adapter_when_configured() -> None:
    s = _settings(llm_provider="openai", openai_api_key="sk-test")
    adapter = make_llm_adapter(s)
    assert isinstance(adapter, OpenAIAdapter)


def test_factory_raises_when_openai_llm_key_missing() -> None:
    s = _settings(llm_provider="openai", openai_api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY required"):
        make_llm_adapter(s)


def test_factory_returns_openai_embedding_adapter_when_configured() -> None:
    s = _settings(embedding_provider="openai", openai_api_key="sk-test")
    adapter = make_embedding_adapter(s)
    assert isinstance(adapter, OpenAIEmbeddingAdapter)


def test_factory_raises_when_openai_embedding_key_missing() -> None:
    s = _settings(embedding_provider="openai", openai_api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY required"):
        make_embedding_adapter(s)


def test_nim_adapters_share_limiter() -> None:
    s = _settings(
        llm_provider="nim",
        embedding_provider="nim",
        nim_api_key="k",
        nim_rate_limit_enabled=True,
    )
    llm = make_llm_adapter(s)
    emb = make_embedding_adapter(s)
    assert llm._limiter is not None
    assert llm._limiter is emb._limiter


def test_disabled_means_no_limiter() -> None:
    s = _settings(
        llm_provider="nim",
        embedding_provider="nim",
        nim_api_key="k",
        nim_rate_limit_enabled=False,
    )
    llm = make_llm_adapter(s)
    emb = make_embedding_adapter(s)
    assert llm._limiter is None
    assert emb._limiter is None


def test_non_nim_provider_has_no_limiter() -> None:
    s = _settings(llm_provider="openai", openai_api_key="sk-test")
    adapter = make_llm_adapter(s)
    assert not hasattr(adapter, "_limiter")

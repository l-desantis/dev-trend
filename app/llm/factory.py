from functools import lru_cache

from app.config import Settings
from app.llm.base import LLMAdapter
from app.llm.embedding_base import EmbeddingAdapter
from app.llm.rate_limiter import AsyncRateLimiter


@lru_cache(maxsize=8)
def _nim_limiter_for(api_key: str, rpm: int) -> AsyncRateLimiter:
    return AsyncRateLimiter(max_requests=rpm, window_seconds=60.0)


def _maybe_nim_limiter(settings: Settings) -> AsyncRateLimiter | None:
    if not settings.nim_rate_limit_enabled:
        return None
    return _nim_limiter_for(settings.nim_api_key, settings.nim_rate_limit_rpm)


def make_llm_adapter(settings: Settings) -> LLMAdapter:
    match settings.llm_provider:
        case "ollama":
            from app.llm.ollama_adapter import OllamaAdapter
            return OllamaAdapter(base_url=settings.ollama_base_url, model=settings.ollama_model)
        case "mock":
            from app.llm.mock_adapter import MockLLMAdapter
            return MockLLMAdapter()
        case "nim":
            if not settings.nim_api_key:
                raise ValueError("NIM_API_KEY required when LLM_PROVIDER=nim")
            from app.llm.nim_adapter import NvidiaNimAdapter
            return NvidiaNimAdapter(
                api_key=settings.nim_api_key,
                model=settings.nim_llm_model,
                base_url=settings.nim_base_url,
                rate_limiter=_maybe_nim_limiter(settings),
            )
        case "openai":
            if not settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY required when LLM_PROVIDER=openai")
            from app.llm.openai_adapter import OpenAIAdapter
            return OpenAIAdapter(
                api_key=settings.openai_api_key,
                model=settings.openai_llm_model,
                base_url=settings.openai_base_url,
            )
        case _:
            raise ValueError(f"unknown llm_provider: {settings.llm_provider!r}")


def make_embedding_adapter(settings: Settings) -> EmbeddingAdapter:
    match settings.embedding_provider:
        case "ollama":
            from app.llm.ollama_embedding_adapter import OllamaEmbeddingAdapter
            return OllamaEmbeddingAdapter(base_url=settings.ollama_base_url)
        case "mock":
            from app.llm.mock_embedding_adapter import MockEmbeddingAdapter
            return MockEmbeddingAdapter()
        case "nim":
            if not settings.nim_api_key:
                raise ValueError("NIM_API_KEY required when EMBEDDING_PROVIDER=nim")
            from app.llm.nim_embedding_adapter import NvidiaNimEmbeddingAdapter
            return NvidiaNimEmbeddingAdapter(
                api_key=settings.nim_api_key,
                model=settings.nim_embedding_model,
                base_url=settings.nim_base_url,
                rate_limiter=_maybe_nim_limiter(settings),
            )
        case "openai":
            if not settings.openai_api_key:
                raise ValueError("OPENAI_API_KEY required when EMBEDDING_PROVIDER=openai")
            from app.llm.openai_embedding_adapter import OpenAIEmbeddingAdapter
            return OpenAIEmbeddingAdapter(
                api_key=settings.openai_api_key,
                model=settings.openai_embedding_model,
                base_url=settings.openai_base_url,
            )
        case _:
            raise ValueError(f"unknown embedding_provider: {settings.embedding_provider!r}")

"""NVIDIA NIM embedding adapter."""
import httpx
import structlog

from app.llm.embedding_base import EmbeddingAdapter
from app.llm.rate_limiter import AsyncRateLimiter

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "nvidia/nv-embedqa-e5-v5"


class NvidiaNimEmbeddingAdapter(EmbeddingAdapter):
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        rate_limiter: "AsyncRateLimiter | None" = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self._model = model
        self._limiter = rate_limiter

    @property
    def dim(self) -> int:
        return 1024  # nv-embedqa-e5-v5

    @property
    def model_name(self) -> str:
        return f"nim:{self._model}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._limiter is not None:
            await self._limiter.acquire()
        response = await self._client.post(
            "/embeddings",
            json={"model": self._model, "input": texts, "input_type": "query"},
        )
        response.raise_for_status()
        return [item["embedding"] for item in response.json()["data"]]

    async def aclose(self) -> None:
        await self._client.aclose()

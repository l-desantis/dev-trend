"""OpenAI embedding adapter — uses the official openai Python SDK."""
import structlog
from openai import AsyncOpenAI

from app.llm.embedding_base import EmbeddingAdapter

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "text-embedding-3-small"


class OpenAIEmbeddingAdapter(EmbeddingAdapter):
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=3,
            timeout=60.0,
        )
        self._model = model

    @property
    def dim(self) -> int:
        return 1536  # text-embedding-3-small

    @property
    def model_name(self) -> str:
        return f"openai:{self._model}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [d.embedding for d in response.data]

    async def aclose(self) -> None:
        await self._client.close()

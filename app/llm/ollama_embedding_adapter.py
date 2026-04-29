"""Ollama embedding adapter using the nomic-embed-text model."""
import structlog

import ollama

from app.llm.embedding_base import EmbeddingAdapter

log = structlog.get_logger(__name__)

_NOMIC_DIM = 768


class OllamaEmbeddingAdapter(EmbeddingAdapter):
    def __init__(self, base_url: str, model: str = "nomic-embed-text") -> None:
        self._client = ollama.AsyncClient(host=base_url)
        self._model = model

    @property
    def dim(self) -> int:
        return _NOMIC_DIM

    @property
    def model_name(self) -> str:
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            response = await self._client.embeddings(model=self._model, prompt=text)
            vec = response["embedding"]
            if len(vec) != _NOMIC_DIM:
                log.warning(
                    "embedding_dim_mismatch",
                    expected=_NOMIC_DIM,
                    got=len(vec),
                    model=self._model,
                )
            results.append(vec)
        return results

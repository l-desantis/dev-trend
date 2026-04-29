import hashlib

from app.llm.embedding_base import EmbeddingAdapter

_DIM = 32


def _vec(text: str, dim: int = _DIM) -> list[float]:
    h = hashlib.sha256(text.encode()).digest()
    raw = [(b - 128) / 128 for b in h]
    return (raw * (dim // len(raw) + 1))[:dim]


class MockEmbeddingAdapter(EmbeddingAdapter):
    @property
    def dim(self) -> int:
        return _DIM

    @property
    def model_name(self) -> str:
        return "mock-embed-v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vec(t) for t in texts]

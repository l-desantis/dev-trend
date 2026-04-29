from abc import ABC, abstractmethod


class EmbeddingAdapter(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def dim(self) -> int: ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...

from abc import ABC, abstractmethod
from typing import Any


class LLMAdapter(ABC):
    @abstractmethod
    async def generate_brief(self, context: dict[str, Any]) -> str: ...

    @abstractmethod
    async def summarize_evidence(self, items: list[Any]) -> str: ...

    @abstractmethod
    async def review_brief(self, brief: str) -> dict[str, object]: ...

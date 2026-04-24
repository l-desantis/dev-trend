from abc import ABC, abstractmethod


class LLMAdapter(ABC):
    @abstractmethod
    async def generate_brief(self, context: dict) -> str: ...

    @abstractmethod
    async def summarize_evidence(self, items: list) -> str: ...

    @abstractmethod
    async def review_brief(self, brief: str) -> dict: ...

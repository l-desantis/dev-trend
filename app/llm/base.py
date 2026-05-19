from abc import ABC, abstractmethod
from typing import Any

from app.llm.schemas import ClusterLabel, PainPointDraft


class LLMAdapter(ABC):
    # Kept from v3 — will be removed in Plan C with agent code
    @abstractmethod
    async def generate_brief(self, context: dict[str, Any]) -> str: ...

    @abstractmethod
    async def summarize_evidence(self, items: list[Any]) -> str: ...

    @abstractmethod
    async def review_brief(self, brief: str) -> dict[str, object]: ...

    # v4 methods
    @abstractmethod
    async def extract_pain_point(
        self,
        source_item_text: str,
        *,
        model_hint: str | None = None,
    ) -> PainPointDraft: ...

    @abstractmethod
    async def label_cluster(
        self,
        evidence_texts: list[str],
        category_slugs: list[str],
    ) -> ClusterLabel: ...

    @abstractmethod
    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        """Return 3-5 domain-specific keywords for GitHub search.

        Returns an empty list on failure — callers must fall back to
        the stopword-based extractor in that case.
        """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stable model identifier used as the extraction cache key (e.g. 'qwen2.5')."""

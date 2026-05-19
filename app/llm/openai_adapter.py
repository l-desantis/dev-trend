"""OpenAI LLM adapter — uses the official openai Python SDK with structured outputs."""
from typing import Any

import structlog
from openai import AsyncOpenAI

from app.llm.base import LLMAdapter
from app.llm.prompts import (
    BRIEF_SYSTEM_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_PROMPT,
    KEYWORD_EXTRACT_SYSTEM_PROMPT,
    KEYWORD_EXTRACT_USER_PROMPT,
    LABEL_CLUSTER_PROMPT,
    render_brief_prompt,
)
from app.llm.schemas import ClusterLabel, PainPointDraft, SearchKeywords

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4.1-nano"


class OpenAIAdapter(LLMAdapter):
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
    def model_name(self) -> str:
        return f"openai:{self._model}"

    async def extract_pain_point(
        self,
        source_item_text: str,
        *,
        model_hint: str | None = None,
    ) -> PainPointDraft:
        try:
            completion = await self._client.beta.chat.completions.parse(
                model=model_hint or self._model,
                messages=[
                    {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": EXTRACT_USER_PROMPT.format(text=source_item_text[:4000])},
                ],
                response_format=PainPointDraft,
            )
            result = completion.choices[0].message.parsed
            if result is None:
                log.warning("openai_extract_refused", reason="content_filter")
                return PainPointDraft(has_unmet_need=False)
            return result
        except Exception as e:
            log.warning("openai_extract_failed", error=str(e))
            return PainPointDraft(has_unmet_need=False)

    async def label_cluster(
        self,
        evidence_texts: list[str],
        category_slugs: list[str],
    ) -> ClusterLabel:
        evidence_lines = "\n".join(f"- {t}" for t in evidence_texts)
        categories = ", ".join(category_slugs) if category_slugs else "(none)"
        prompt = LABEL_CLUSTER_PROMPT.format(
            evidence_lines=evidence_lines,
            categories=categories,
        )
        completion = await self._client.beta.chat.completions.parse(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format=ClusterLabel,
        )
        result = completion.choices[0].message.parsed
        if result is None:
            raise ValueError("OpenAI refused to parse ClusterLabel response")
        return result

    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        try:
            completion = await self._client.beta.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": KEYWORD_EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": KEYWORD_EXTRACT_USER_PROMPT.format(
                        problem=problem,
                        audience=audience or "(not specified)",
                    )},
                ],
                response_format=SearchKeywords,
            )
            result = completion.choices[0].message.parsed
            if result is None:
                return []
            return [k.lower().strip() for k in result.keywords if k.strip()][:5]
        except Exception as exc:
            log.warning("openai_keyword_extract_failed", error=str(exc))
            return []

    async def generate_brief(self, context: dict[str, Any]) -> str:
        prompt = render_brief_prompt(context)
        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return completion.choices[0].message.content or ""

    async def summarize_evidence(self, items: list[Any]) -> str:
        bullet = "\n".join(
            f"- [{i.get('source_type', '?')}] {i.get('title', '')}"
            for i in items
        )
        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": "Summarise the evidence in one sentence."},
                {"role": "user", "content": bullet or "(no items)"},
            ],
        )
        return completion.choices[0].message.content or ""

    async def review_brief(self, brief: str) -> dict[str, object]:
        gaps: list[str] = []
        if len(brief.strip()) < 50:
            gaps.append("summary too short")
        return {"has_issues": bool(gaps), "gaps": gaps}

    async def aclose(self) -> None:
        await self._client.close()

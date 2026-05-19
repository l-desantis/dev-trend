"""Ollama adapter — calls qwen2.5 via the ollama Python client."""
import json
from typing import Any

import structlog
from pydantic import ValidationError

import ollama

from app.llm.base import LLMAdapter
from app.llm.prompts import (
    BRIEF_SYSTEM_PROMPT,
    EXTRACT_PROMPT,
    KEYWORD_EXTRACT_SYSTEM_PROMPT,
    KEYWORD_EXTRACT_USER_PROMPT,
    LABEL_CLUSTER_PROMPT,
    render_brief_prompt,
)
from app.llm.schemas import ClusterLabel, PainPointDraft

_MIN_REVIEWABLE_CHARS = 50

log = structlog.get_logger(__name__)


class OllamaAdapter(LLMAdapter):
    def __init__(self, base_url: str, model: str) -> None:
        self._client = ollama.AsyncClient(host=base_url)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def _chat(self, prompt: str, *, model: str | None = None, format: str | None = None) -> str:
        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if format:
            kwargs["format"] = format
        response = await self._client.chat(**kwargs)
        return response.message.content or ""

    async def generate_brief(self, context: dict[str, Any]) -> str:
        prompt = render_brief_prompt(context)
        response = await self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.message.content or ""

    async def summarize_evidence(self, items: list[Any]) -> str:
        bullet = "\n".join(
            f"- [{i.get('source_type', '?')}] {i.get('title', '')}"
            for i in items
        )
        response = await self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": "Summarise the evidence in one sentence."},
                {"role": "user", "content": bullet or "(no items)"},
            ],
        )
        return response.message.content or ""

    async def review_brief(self, brief: str) -> dict[str, object]:
        gaps: list[str] = []
        if len(brief.strip()) < _MIN_REVIEWABLE_CHARS:
            gaps.append("summary too short")
        return {"has_issues": bool(gaps), "gaps": gaps}

    async def extract_pain_point(
        self,
        source_item_text: str,
        *,
        model_hint: str | None = None,
    ) -> PainPointDraft:
        prompt = EXTRACT_PROMPT.format(text=source_item_text[:4000])
        raw = await self._chat(prompt, model=model_hint or self._model, format="json")
        try:
            return PainPointDraft.model_validate_json(raw)
        except (ValidationError, json.JSONDecodeError) as e:
            log.warning("extract_pain_point_invalid_json", error=str(e), raw=raw[:200])
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
        raw = await self._chat(prompt, format="json")
        return ClusterLabel.model_validate_json(raw)

    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        try:
            response = await self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": KEYWORD_EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": KEYWORD_EXTRACT_USER_PROMPT.format(
                        problem=problem,
                        audience=audience or "(not specified)",
                    )},
                ],
                format="json",
            )
            raw = response.message.content or ""
            data = json.loads(raw)
            return [k.lower().strip() for k in data.get("keywords", []) if k.strip()][:5]
        except Exception as exc:
            log.warning("ollama_keyword_extract_failed", error=str(exc))
            return []

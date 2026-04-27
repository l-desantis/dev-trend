"""Ollama adapter for the local qwen2.5 model.

Calls only `ollama.AsyncClient.chat`. The reviewer step is intentionally
heuristic in Phase 1 — see ADR-005 — so `review_brief()` does not call the
LLM; it returns a deterministic shape that the agent's reviewer_node can
trust.
"""
from typing import Any

import ollama

from app.agents.prompts import BRIEF_SYSTEM_PROMPT, render_brief_prompt
from app.llm.base import LLMAdapter

_MIN_REVIEWABLE_CHARS = 50


class OllamaAdapter(LLMAdapter):
    def __init__(self, base_url: str, model: str) -> None:
        self._client = ollama.AsyncClient(host=base_url)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

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

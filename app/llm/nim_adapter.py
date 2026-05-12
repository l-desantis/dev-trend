"""NVIDIA NIM LLM adapter — OpenAI-compatible chat-completions endpoint."""
import json
from typing import Any

import httpx
import structlog
from pydantic import ValidationError

from app.llm.base import LLMAdapter
from app.llm.rate_limiter import AsyncRateLimiter
from app.llm.prompts import (
    BRIEF_SYSTEM_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_PROMPT,
    LABEL_CLUSTER_PROMPT,
    render_brief_prompt,
)
from app.llm.schemas import ClusterLabel, PainPointDraft

log = structlog.get_logger(__name__)

_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "meta/llama-3.1-70b-instruct"
_MAX_RETRIES = 3


class NvidiaNimAdapter(LLMAdapter):
    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        rate_limiter: "AsyncRateLimiter | None" = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self._model = model
        self._limiter = rate_limiter

    @property
    def model_name(self) -> str:
        return f"nim:{self._model}"

    async def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        json_mode: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
            "temperature": 0.0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                if self._limiter is not None:
                    await self._limiter.acquire()
                response = await self._client.post("/chat/completions", json=payload)
                if response.status_code >= 500:
                    log.warning(
                        "nim_5xx_retry",
                        attempt=attempt + 1,
                        status=response.status_code,
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"Server error {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    continue
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"] or ""
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last_exc = exc
                log.warning("nim_5xx_retry", attempt=attempt + 1, error=str(exc))

        raise last_exc or RuntimeError("NIM request failed after retries")

    async def extract_pain_point(
        self,
        source_item_text: str,
        *,
        model_hint: str | None = None,
    ) -> PainPointDraft:
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": EXTRACT_USER_PROMPT.format(text=source_item_text[:4000])},
        ]
        raw = await self._chat(messages, model=model_hint, json_mode=True)
        try:
            return PainPointDraft.model_validate_json(raw)
        except (ValidationError, json.JSONDecodeError) as e:
            log.warning("nim_extract_invalid_json", error=str(e), raw=raw[:200])
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
        messages = [{"role": "user", "content": prompt}]
        raw = await self._chat(messages, json_mode=True)
        return ClusterLabel.model_validate_json(raw)

    async def generate_brief(self, context: dict[str, Any]) -> str:
        prompt = render_brief_prompt(context)
        messages = [
            {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        return await self._chat(messages)

    async def summarize_evidence(self, items: list[Any]) -> str:
        bullet = "\n".join(
            f"- [{i.get('source_type', '?')}] {i.get('title', '')}"
            for i in items
        )
        messages = [
            {"role": "system", "content": "Summarise the evidence in one sentence."},
            {"role": "user", "content": bullet or "(no items)"},
        ]
        return await self._chat(messages)

    async def review_brief(self, brief: str) -> dict[str, object]:
        gaps: list[str] = []
        if len(brief.strip()) < 50:
            gaps.append("summary too short")
        return {"has_issues": bool(gaps), "gaps": gaps}

    async def aclose(self) -> None:
        await self._client.aclose()

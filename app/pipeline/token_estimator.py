"""Dry-run token estimator for the backfill pipeline.

Char-based heuristic (chars / 4) — accurate to within ~20% for Llama/Qwen
tokenizers, and good enough to predict whether a real backfill will cost
$0.10 or $10. No tokenizer dependency.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.llm.prompts import (
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_PROMPT,
    LABEL_CLUSTER_PROMPT,
)
from app.models import OpportunityCandidate, SourceItem

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config import Settings

# Output budgets per call (heuristic — observed JSON sizes).
_EXTRACT_OUTPUT_TOKENS = 100
_LABEL_OUTPUT_TOKENS = 200

# Heuristics for projecting NEW labelling work that depends on extraction output.
_PROJECTED_PAINPOINT_RATIO = 0.30   # 30% of extracted items become pain points
_PROJECTED_CLUSTER_SIZE = 5         # avg pain points per cluster


def chars_to_tokens(chars: int) -> int:
    """Rough token count using the 1-token-per-4-chars heuristic. Rounds up."""
    if chars <= 0:
        return 0
    return math.ceil(chars / 4)


def extract_prompt_chars(source_text: str) -> int:
    """Total prompt chars for one extract call (system + user, body capped at 4000)."""
    body = source_text[:4000]
    user = EXTRACT_USER_PROMPT.format(text=body)
    return len(EXTRACT_SYSTEM_PROMPT) + len(user)


def label_prompt_chars(
    *,
    evidence_count: int,
    avg_evidence_chars: int,
    category_count: int,
) -> int:
    """Total prompt chars for one label_cluster call."""
    evidence_lines = "\n".join(["- " + "x" * avg_evidence_chars] * evidence_count)
    categories = ", ".join(["category"] * category_count) or "(none)"
    rendered = LABEL_CLUSTER_PROMPT.format(
        evidence_lines=evidence_lines,
        categories=categories,
    )
    return len(rendered)


@dataclass
class StageEstimate:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TokenEstimate:
    extract: StageEstimate = field(default_factory=StageEstimate)
    label: StageEstimate = field(default_factory=StageEstimate)
    label_projected: StageEstimate = field(default_factory=StageEstimate)
    embed: StageEstimate = field(default_factory=StageEstimate)
    notes: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return (
            self.extract.total
            + self.label.total
            + self.label_projected.total
            + self.embed.total
        )

    def to_dict(self) -> dict:
        return {
            "extract": self.extract.__dict__,
            "label": self.label.__dict__,
            "label_projected": self.label_projected.__dict__,
            "embed": self.embed.__dict__,
            "total_tokens": self.total_tokens,
            "notes": list(self.notes),
        }


async def estimate_tokens(
    session_factory: "async_sessionmaker",
    settings: "Settings",
) -> TokenEstimate:
    """Estimate tokens that the next bulk_backfill pipeline run would spend.

    Reads (does not mutate):
      - SourceItem rows with role='extraction' AND extraction_state='pending'
        → exact extract-stage cost
      - OpportunityCandidate rows with labeller_model IS NULL
        → exact (already-existing) label-stage cost
      - Heuristic projection for NEW clusters from this run's extractions.
    """
    estimate = TokenEstimate()

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(SourceItem.title, SourceItem.body)
                .where(SourceItem.role == "extraction")
                .where(SourceItem.extraction_state == "pending")
            )
        ).all()

        for title, body in rows:
            text = f"{title or ''}\n{body or ''}"
            estimate.extract.calls += 1
            estimate.extract.input_tokens += chars_to_tokens(extract_prompt_chars(text))
            estimate.extract.output_tokens += _EXTRACT_OUTPUT_TOKENS

        unlabelled = (
            await session.execute(
                select(func.count(OpportunityCandidate.id))
                .where(OpportunityCandidate.labeller_model.is_(None))
            )
        ).scalar_one()

    # Existing unlabelled candidates (definite cost).
    if unlabelled:
        per_call_in = chars_to_tokens(
            label_prompt_chars(evidence_count=10, avg_evidence_chars=80, category_count=10)
        )
        estimate.label.calls = unlabelled
        estimate.label.input_tokens = unlabelled * per_call_in
        estimate.label.output_tokens = unlabelled * _LABEL_OUTPUT_TOKENS

    # Projected NEW clusters from this run's extractions (heuristic).
    projected_pps = int(estimate.extract.calls * _PROJECTED_PAINPOINT_RATIO)
    projected_clusters = projected_pps // _PROJECTED_CLUSTER_SIZE
    if projected_clusters:
        per_call_in = chars_to_tokens(
            label_prompt_chars(evidence_count=10, avg_evidence_chars=80, category_count=10)
        )
        estimate.label_projected.calls = projected_clusters
        estimate.label_projected.input_tokens = projected_clusters * per_call_in
        estimate.label_projected.output_tokens = projected_clusters * _LABEL_OUTPUT_TOKENS
        estimate.notes.append(
            f"Projected new clusters use heuristic: "
            f"{int(_PROJECTED_PAINPOINT_RATIO*100)}% extraction yield, "
            f"avg cluster size {_PROJECTED_CLUSTER_SIZE}."
        )

    # Embedding only billed by NIM. For Ollama/mock it's free.
    if settings.embedding_provider == "nim":
        embed_chars = projected_pps * 200
        estimate.embed.calls = 1 if projected_pps else 0
        estimate.embed.input_tokens = chars_to_tokens(embed_chars)
        estimate.notes.append(
            "Embedding cost projected from extraction count × heuristic ratio."
        )
    else:
        estimate.notes.append(
            f"Embedding provider '{settings.embedding_provider}' is not token-billed."
        )

    estimate.notes.append(
        "Token counts use chars/4 heuristic (±20% vs. real Llama/Qwen tokenizer)."
    )
    return estimate

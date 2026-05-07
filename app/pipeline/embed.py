"""Stage 2 — embed pain-point texts."""
import time
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.embedding_base import EmbeddingAdapter
from app.models import PainPoint

log = structlog.get_logger(__name__)


@dataclass
class EmbeddingReport:
    processed: int = 0
    duration_ms: int = 0


async def run_embedding(
    session: AsyncSession,
    embedder: EmbeddingAdapter,
    *,
    batch_size: int = 64,
) -> EmbeddingReport:
    """Stage 2: embed all PainPoints that have no embedding yet."""
    start = time.monotonic()
    report = EmbeddingReport()

    rows = (
        await session.execute(select(PainPoint).where(PainPoint.embedding.is_(None)))
    ).scalars().all()

    if not rows:
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [
            f"{pp.problem_text or ''}. Audience: {pp.audience or ''}. {pp.urgency_cue or ''}".strip()
            for pp in batch
        ]
        model_name = embedder.model_name
        vectors = await embedder.embed(texts)
        for pp, vec in zip(batch, vectors):
            pp.embedding = vec
            pp.embedding_model = model_name
        report.processed += len(batch)

    await session.commit()
    report.duration_ms = int((time.monotonic() - start) * 1000)
    log.info("embedding_complete", processed=report.processed)
    return report

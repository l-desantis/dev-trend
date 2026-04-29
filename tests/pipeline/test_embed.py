"""Tests for Stage 2 — embed."""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from unittest.mock import AsyncMock

from app.llm.mock_embedding_adapter import MockEmbeddingAdapter
from app.models import PainPoint, SourceItem
from app.pipeline.embed import run_embedding


async def _seed_pain_points(session: AsyncSession, n: int, with_embedding: bool = False):
    item = SourceItem(source_type="reddit", external_id="t3_base", role="extraction")
    session.add(item)
    await session.flush()
    pps = []
    for i in range(n):
        pp = PainPoint(
            source_item_id=item.id,
            extractor_model="mock",
            problem_text=f"Problem {i}",
            audience=f"audience {i}",
            embedding=[0.1, 0.2] * 16 if with_embedding else None,
        )
        session.add(pp)
        pps.append(pp)
    await session.commit()
    return pps


async def test_embed_populates_null_embeddings(session: AsyncSession) -> None:
    await _seed_pain_points(session, 3)
    embedder = MockEmbeddingAdapter()

    report = await run_embedding(session, embedder)

    assert report.processed == 3
    pps = (await session.execute(select(PainPoint))).scalars().all()
    for pp in pps:
        assert pp.embedding is not None
        assert len(pp.embedding) == embedder.dim


async def test_embed_skips_existing(session: AsyncSession) -> None:
    await _seed_pain_points(session, 1, with_embedding=True)
    embedder = MockEmbeddingAdapter()

    report = await run_embedding(session, embedder)

    assert report.processed == 0


async def test_embed_handles_empty_batch(session: AsyncSession) -> None:
    embedder = MockEmbeddingAdapter()
    spy_embed = AsyncMock(return_value=[])
    embedder.embed = spy_embed  # type: ignore

    report = await run_embedding(session, embedder)

    assert report.processed == 0
    spy_embed.assert_not_called()

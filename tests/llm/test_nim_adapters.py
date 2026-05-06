"""Tests for NIM embedding adapter and identity-resolution filtering."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.llm.nim_embedding_adapter import NvidiaNimEmbeddingAdapter


@pytest.fixture
def nim_embedder():
    return NvidiaNimEmbeddingAdapter(api_key="test-key")


def test_nim_embedding_adapter_dim(nim_embedder):
    assert nim_embedder.dim == 1024


def test_nim_embedding_model_name(nim_embedder):
    assert nim_embedder.model_name == "nim:nvidia/nv-embedqa-e5-v5"


@pytest.mark.asyncio
async def test_nim_embedding_adapter_returns_vectors(nim_embedder):
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "data": [
            {"embedding": [0.1] * 1024},
            {"embedding": [0.2] * 1024},
        ]
    }

    with patch.object(nim_embedder._client, "post", new=AsyncMock(return_value=fake_response)):
        result = await nim_embedder.embed(["text a", "text b"])

    assert len(result) == 2
    assert len(result[0]) == 1024
    assert result[0][0] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_identity_resolution_filters_by_embedding_model():
    """Candidates with different embedding_model must not match cross-model pain points."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    import numpy as np
    from sqlalchemy import StaticPool, event
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base, OpportunityCandidate, PainPoint, SourceItem
    from app.pipeline.identity_resolution import run_identity_resolution

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        # Seed a source item
        si = SourceItem(
            source_type="reddit",
            external_id="test-si-1",
            role="extraction",
            extraction_state="pending",
        )
        session.add(si)
        await session.flush()

        # Two candidates: same embedding direction, different embedding_model
        vec = [1.0, 0.0, 0.0]
        c_ollama = OpportunityCandidate(
            problem_statement="Ollama cluster",
            centroid=vec,
            embedding_model="ollama:nomic-embed-text",
            created_at=datetime.now(UTC),
        )
        c_nim = OpportunityCandidate(
            problem_statement="NIM cluster",
            centroid=vec,
            embedding_model="nim:nvidia/nv-embedqa-e5-v5",
            created_at=datetime.now(UTC),
        )
        session.add_all([c_ollama, c_nim])
        await session.flush()

        # A pain point with NIM embedding — should only match NIM candidate
        pp = PainPoint(
            source_item_id=si.id,
            extractor_model="nim:meta/llama-3.1-70b-instruct",
            embedding=vec,
            embedding_model="nim:nvidia/nv-embedqa-e5-v5",
            extracted_at=datetime.now(UTC),
        )
        session.add(pp)
        await session.commit()

    async with Session() as session:
        report = await run_identity_resolution(session, threshold=0.5)

    async with Session() as session:
        from sqlalchemy import select
        refreshed_pp = (await session.execute(select(PainPoint))).scalar_one()
        # Should attach to NIM candidate, not Ollama one
        assert refreshed_pp.candidate_id == c_nim.id
        assert report.attached == 1

    await engine.dispose()

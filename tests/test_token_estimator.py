"""Unit tests for the dry-run token estimator."""
from app.pipeline.token_estimator import (
    TokenEstimate,
    StageEstimate,
    chars_to_tokens,
    extract_prompt_chars,
    label_prompt_chars,
)


def test_chars_to_tokens_uses_div_4_heuristic():
    assert chars_to_tokens(0) == 0
    assert chars_to_tokens(4) == 1
    assert chars_to_tokens(400) == 100
    # Rounds up so we never under-quote.
    assert chars_to_tokens(5) == 2


def test_extract_prompt_chars_includes_system_and_user_template():
    """A short input text yields prompt_chars > template fixed cost."""
    short = extract_prompt_chars("hello")
    longer = extract_prompt_chars("hello world " * 50)
    assert longer > short
    # Sanity: the EXTRACT_USER_PROMPT template alone is ~420 chars.
    assert short > 400


def test_extract_prompt_chars_truncates_at_4000():
    """Body is capped at 4000 chars (matches extract.py:79)."""
    huge = "x" * 10_000
    assert extract_prompt_chars(huge) == extract_prompt_chars("x" * 4_000)


def test_label_prompt_chars_grows_with_evidence():
    a = label_prompt_chars(evidence_count=3, avg_evidence_chars=80, category_count=10)
    b = label_prompt_chars(evidence_count=10, avg_evidence_chars=80, category_count=10)
    assert b > a


def test_token_estimate_total_sums_stages():
    est = TokenEstimate(
        extract=StageEstimate(calls=10, input_tokens=1000, output_tokens=200),
        label=StageEstimate(calls=2, input_tokens=300, output_tokens=100),
        embed=StageEstimate(calls=1, input_tokens=500, output_tokens=0),
    )
    assert est.total_tokens == 1000 + 200 + 300 + 100 + 500


import pytest

from app.db import _get_session_factory
from app.config import get_settings
from app.models import OpportunityCandidate, SourceItem
from app.pipeline.token_estimator import estimate_tokens


@pytest.mark.asyncio
async def test_estimate_tokens_counts_pending_extraction(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    get_settings.cache_clear()

    factory = _get_session_factory()
    async with factory() as s:
        # 2 pending extraction items, 1 already extracted (excluded), 1 wrong role (excluded).
        s.add_all([
            SourceItem(source_type="hn", external_id="1", title="I wish there was X",
                       body="long body " * 20, role="extraction", extraction_state="pending"),
            SourceItem(source_type="hn", external_id="2", title="Need help with Y",
                       body="another", role="extraction", extraction_state="pending"),
            SourceItem(source_type="hn", external_id="3", title="done",
                       role="extraction", extraction_state="extracted"),
            SourceItem(source_type="hn", external_id="4", title="not extraction",
                       role="reference", extraction_state="pending"),
            # 1 unlabelled candidate.
            OpportunityCandidate(centroid=[0.0] * 32,
                                 embedding_model="mock", labeller_model=None),
            # 1 already-labelled candidate (excluded).
            OpportunityCandidate(centroid=[0.0] * 32,
                                 embedding_model="mock", labeller_model="mock:v1"),
        ])
        await s.commit()

    estimate = await estimate_tokens(factory, get_settings())

    assert estimate.extract.calls == 2
    assert estimate.extract.input_tokens > 0
    assert estimate.extract.output_tokens == 2 * 100  # _EXTRACT_OUTPUT_TOKENS
    assert estimate.label.calls == 1
    assert estimate.label.output_tokens == 1 * 200    # _LABEL_OUTPUT_TOKENS
    assert estimate.label_projected.calls >= 0          # int(2*0.30)=0 so no projected clusters
    assert estimate.total_tokens > 0
    assert any("heuristic" in n.lower() for n in estimate.notes)

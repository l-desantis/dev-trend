"""Tests for MockLLMAdapter deterministic behaviour."""
import pytest

from app.llm.mock_adapter import MockLLMAdapter
from app.llm.schemas import PainPointDraft, ClusterLabel


@pytest.fixture
def adapter() -> MockLLMAdapter:
    return MockLLMAdapter()


async def test_extract_returns_signal_for_high_signal_text(adapter: MockLLMAdapter) -> None:
    result = await adapter.extract_pain_point("I wish there was a habit tracker for ADHD")
    assert result.has_unmet_need is True
    assert result.problem_text is not None
    assert result.audience is not None


async def test_extract_returns_no_signal_for_low_signal_text(adapter: MockLLMAdapter) -> None:
    result = await adapter.extract_pain_point("Breaking news: tech company releases product")
    assert result.has_unmet_need is False


async def test_extract_deterministic(adapter: MockLLMAdapter) -> None:
    text = "Why is there no good time-tracking app for freelancers?"
    r1 = await adapter.extract_pain_point(text)
    r2 = await adapter.extract_pain_point(text)
    assert r1 == r2


async def test_label_cluster_deterministic(adapter: MockLLMAdapter) -> None:
    texts = ["Need habit tracker", "ADHD productivity tool missing"]
    r1 = await adapter.label_cluster(texts, ["wellness"])
    r2 = await adapter.label_cluster(texts, ["wellness"])
    assert r1 == r2


async def test_label_cluster_specificity_scales_with_size(adapter: MockLLMAdapter) -> None:
    small = await adapter.label_cluster(["one pain point"], ["wellness"])
    large = await adapter.label_cluster(["p"] * 10, ["wellness"])
    assert large.specificity >= small.specificity


async def test_label_cluster_uses_first_category(adapter: MockLLMAdapter) -> None:
    result = await adapter.label_cluster(["pain 1", "pain 2"], ["devtools", "wellness"])
    assert result.suggested_category_slug == "devtools"

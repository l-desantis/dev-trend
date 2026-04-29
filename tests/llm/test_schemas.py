"""Tests for PainPointDraft and ClusterLabel schemas."""
import pytest
from pydantic import ValidationError

from app.llm.schemas import ClusterLabel, PainPointDraft


def test_painpoint_draft_validates_coherence() -> None:
    with pytest.raises(ValidationError, match="problem_text and audience required"):
        PainPointDraft(has_unmet_need=True, problem_text=None, audience=None)


def test_painpoint_draft_no_signal_passes() -> None:
    draft = PainPointDraft(has_unmet_need=False)
    assert draft.problem_text is None
    assert draft.audience is None


def test_painpoint_draft_signal_with_required_fields() -> None:
    draft = PainPointDraft(
        has_unmet_need=True,
        problem_text="Need a habit tracker for ADHD",
        audience="ADHD adults",
    )
    assert draft.has_unmet_need is True


def test_cluster_label_specificity_bounds() -> None:
    with pytest.raises(ValidationError):
        ClusterLabel(
            problem_statement="test", audience="a", why_now="b", specificity=0
        )
    with pytest.raises(ValidationError):
        ClusterLabel(
            problem_statement="test", audience="a", why_now="b", specificity=6
        )


def test_cluster_label_valid() -> None:
    label = ClusterLabel(
        problem_statement="Opportunity in habit tracking",
        audience="productivity enthusiasts",
        why_now="AI makes personalisation cheap",
        specificity=4,
        suggested_category_slug="wellness",
    )
    assert label.specificity == 4

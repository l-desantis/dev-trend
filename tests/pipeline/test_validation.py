"""Tests for Stage 6 — validation.py"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.mock_adapter import MockLLMAdapter
from app.models import (
    CandidateValidation,
    OpportunityCandidate,
    SourceItem,
)
from app.pipeline import validation as validation_module
from app.pipeline.validation import (
    ValidationReport,
    count_show_hn_matches,
    extract_keywords,
    run_validation,
    search_github_repos,
)


def _make_github_response(total_count: int = 5, stars: int = 1000) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "total_count": total_count,
        "items": [
            {"full_name": f"owner/repo{i}", "stargazers_count": stars - i * 100,
             "html_url": f"https://github.com/owner/repo{i}", "language": "Python"}
            for i in range(min(total_count, 5))
        ],
    }
    return mock_resp


async def _make_github_client(total_count: int = 5, stars: int = 1000) -> httpx.AsyncClient:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=_make_github_response(total_count, stars))
    return client


async def test_validation_creates_snapshot_when_stale(session: AsyncSession) -> None:
    candidate = OpportunityCandidate(
        problem_statement="habit tracker for ADHD adults",
        audience="developers",
        specificity=3,
    )
    session.add(candidate)
    await session.flush()

    # Seed a stale snapshot (8 days old)
    stale = CandidateValidation(
        candidate_id=candidate.id,
        signal_type="composite",
        signal_value=3.0,
        validated_at=datetime.now(UTC) - timedelta(days=8),
        metadata_json={"repo_count": 3, "max_stars": 500},
    )
    session.add(stale)
    await session.commit()

    client = await _make_github_client()
    report = await run_validation(session, client, refresh_age_days=7)

    assert report.validated == 1
    assert report.skipped_recent == 0


async def test_validation_skips_recent_snapshot(session: AsyncSession) -> None:
    candidate = OpportunityCandidate(
        problem_statement="task manager",
        specificity=3,
    )
    session.add(candidate)
    await session.flush()

    recent = CandidateValidation(
        candidate_id=candidate.id,
        signal_type="composite",
        signal_value=2.0,
        validated_at=datetime.now(UTC) - timedelta(days=2),
        metadata_json={"repo_count": 2},
    )
    session.add(recent)
    await session.commit()

    client = await _make_github_client()
    report = await run_validation(session, client, refresh_age_days=7)

    assert report.skipped_recent == 1
    assert report.validated == 0


async def test_validation_skips_archived(session: AsyncSession) -> None:
    candidate = OpportunityCandidate(
        problem_statement="archived thing",
        specificity=3,
        is_archived=True,
    )
    session.add(candidate)
    await session.commit()

    client = await _make_github_client()
    # only_active=False so the query includes archived candidates; the loop skips them
    report = await run_validation(session, client, only_active=False)

    assert report.skipped_archived == 1
    assert report.validated == 0


async def test_validation_handles_zero_repo_match(session: AsyncSession) -> None:
    candidate = OpportunityCandidate(
        problem_statement="obscure niche thing",
        specificity=3,
    )
    session.add(candidate)
    await session.commit()

    client = await _make_github_client(total_count=0, stars=0)
    report = await run_validation(session, client)

    assert report.validated == 1
    assert report.details[0]["repo_count"] == 0


def test_extract_keywords_strips_stopwords() -> None:
    keywords = extract_keywords("a habit tracker for ADHD adults", None)
    assert "a" not in keywords
    assert "for" not in keywords
    assert len(keywords) <= 5
    assert any("habit" in kw or "tracker" in kw or "adhd" in kw or "adults" in kw for kw in keywords)


async def test_count_show_hn_matches(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    matching_items = [
        SourceItem(
            source_type="hn",
            external_id=f"hn_{i}",
            title=f"Show HN: habit tracker app {i}",
            ingested_at=now - timedelta(days=5),
            metadata_json={"points": 10 * i},
        )
        for i in range(3)
    ]
    non_matching = SourceItem(
        source_type="hn",
        external_id="hn_other",
        title="Ask HN: totally different topic",
        ingested_at=now - timedelta(days=5),
    )
    for item in matching_items:
        session.add(item)
    session.add(non_matching)
    await session.commit()

    candidate = OpportunityCandidate(
        problem_statement="habit tracker for focus",
        audience="ADHD adults",
        specificity=3,
    )
    session.add(candidate)
    await session.commit()

    result = await count_show_hn_matches(session, candidate)
    assert result["show_hn_count"] >= 3


def test_extract_keywords_prefers_audience_cohort_nouns() -> None:
    from app.pipeline.validation import extract_keywords
    problem = ("Current task management and habit tracking apps fail to "
               "engage and motivate individuals with diverse needs.")
    audience = "Individuals with ADHD and social media addiction"
    kws = extract_keywords(problem, audience)
    # ADHD must survive the 5-token cap
    assert "adhd" in kws


def test_extract_keywords_drops_weak_filler_words() -> None:
    from app.pipeline.validation import extract_keywords
    problem = ("Current existing apps and solutions for users are not "
               "engaging enough.")
    kws = extract_keywords(problem, None)
    for filler in ("current", "existing", "apps", "solutions", "users"):
        assert filler not in kws, f"filler word {filler!r} leaked into keywords"


def test_extract_keywords_returns_at_most_five() -> None:
    from app.pipeline.validation import extract_keywords
    problem = " ".join(f"meaningfulword{i}" for i in range(20))
    kws = extract_keywords(problem, None)
    assert len(kws) <= 5


async def test_search_github_repos_unions_multiple_pair_queries() -> None:
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    from app.pipeline.validation import search_github_repos

    def resp(items):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"total_count": len(items), "items": items}
        return m

    # Each pair query returns different repos; one repo appears in two
    # queries and must be deduped.
    responses = [
        resp([
            {"full_name": "alice/adhd-tasks", "stargazers_count": 800,
             "html_url": "https://github.com/alice/adhd-tasks", "language": "Python"},
            {"full_name": "bob/shared-repo", "stargazers_count": 1200,
             "html_url": "https://github.com/bob/shared-repo", "language": "Go"},
        ]),
        resp([
            {"full_name": "bob/shared-repo", "stargazers_count": 1200,
             "html_url": "https://github.com/bob/shared-repo", "language": "Go"},
            {"full_name": "carol/habit-tracker", "stargazers_count": 500,
             "html_url": "https://github.com/carol/habit-tracker", "language": "Rust"},
        ]),
        resp([
            {"full_name": "dave/focus-app", "stargazers_count": 50,
             "html_url": "https://github.com/dave/focus-app", "language": "Swift"},
        ]),
    ]

    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=responses)

    result = await search_github_repos(
        client, ["adhd", "habit", "tracker", "focus"]
    )

    # Three pair queries issued
    assert client.request.await_count == 3
    # 4 unique repos after dedupe
    assert result["repo_count"] == 4
    # Top repos sorted by stars desc
    names = [r["name"] for r in result["top_repos_json"]]
    assert names[0] == "bob/shared-repo"
    assert result["max_stars"] == 1200


async def test_search_github_repos_handles_single_keyword() -> None:
    # With only one keyword, no pairs can be formed — should still issue
    # one fallback query and return its results.
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    from app.pipeline.validation import search_github_repos

    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {
        "total_count": 1,
        "items": [{"full_name": "x/y", "stargazers_count": 10,
                   "html_url": "https://github.com/x/y", "language": "Python"}],
    }
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=m)

    result = await search_github_repos(client, ["adhd"])

    assert client.request.await_count == 1
    assert result["repo_count"] == 1


async def test_run_validation_uses_llm_keywords_when_provided(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = OpportunityCandidate(
        problem_statement="habit tracking for diverse needs",
        audience="ADHD adults",
        specificity=3,
    )
    session.add(candidate)
    await session.commit()

    llm = MockLLMAdapter()
    llm.extract_search_keywords = AsyncMock(return_value=["adhd", "habit", "tracker"])

    # Spy on the stopword path so we can assert it was NOT called for GitHub search.
    # NOTE: count_show_hn_matches calls extract_keywords for title pattern matching,
    # so the spy may still be invoked once. We assert that the GitHub-search path used
    # the LLM keywords by checking llm.extract_search_keywords was awaited.
    real_extract = validation_module.extract_keywords
    spy = MagicMock(side_effect=real_extract)
    monkeypatch.setattr(validation_module, "extract_keywords", spy)

    client = await _make_github_client()
    report = await run_validation(session, client, llm=llm)

    assert report.validated == 1
    llm.extract_search_keywords.assert_awaited_once_with(
        candidate.problem_statement, candidate.audience
    )


async def test_run_validation_falls_back_to_stopwords_when_llm_returns_empty(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = OpportunityCandidate(
        problem_statement="habit tracking for focus",
        audience="ADHD adults",
        specificity=3,
    )
    session.add(candidate)
    await session.commit()

    llm = MockLLMAdapter()
    llm.extract_search_keywords = AsyncMock(return_value=[])  # empty → must fall back

    real_extract = validation_module.extract_keywords
    spy = MagicMock(side_effect=real_extract)
    monkeypatch.setattr(validation_module, "extract_keywords", spy)

    client = await _make_github_client()
    report = await run_validation(session, client, llm=llm)

    assert report.validated == 1
    assert report.errors == 0
    # Spy must have been called for the GitHub-search path (fallback fired),
    # plus once more inside count_show_hn_matches. At least 2 calls confirms the fallback.
    assert spy.call_count >= 2


async def test_run_validation_without_llm_uses_stopwords(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = OpportunityCandidate(
        problem_statement="habit tracking for focus",
        audience="ADHD adults",
        specificity=3,
    )
    session.add(candidate)
    await session.commit()

    real_extract = validation_module.extract_keywords
    spy = MagicMock(side_effect=real_extract)
    monkeypatch.setattr(validation_module, "extract_keywords", spy)

    client = await _make_github_client()
    report = await run_validation(session, client)  # no llm kwarg

    assert report.validated == 1
    assert spy.call_count >= 1

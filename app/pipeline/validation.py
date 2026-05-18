"""Stage 6 — Validation via GitHub search and Show HN matching."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.http_utils import request_with_retry
from app.models import CandidateValidation, OpportunityCandidate, SourceItem

log = structlog.get_logger(__name__)

_STOPWORDS = frozenset(
    "a an the and or but in on at to for of with is are was were be been being "
    "have has had do does did will would could should may might shall can i we "
    "you he she it they them their its my our your his her from by about into "
    "through during before after above below between among while "
    # Weak filler / overly generic tokens that pollute GitHub queries:
    "current currently existing existed new old "
    "app apps application applications solution solutions tool tools "
    "user users people person individual individuals customer customers "
    "thing things stuff way ways "
    "need needs needed lack lacking missing "
    "feature features functionality "
    "good bad great poor better best worse worst "
    "make made making get got getting use used using "
    "really very quite rather just only also still even".split()
)


@dataclass
class ValidationReport:
    validated: int = 0
    skipped_recent: int = 0
    skipped_archived: int = 0
    errors: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", text)
            if w.lower() not in _STOPWORDS]


def extract_keywords(problem_statement: str, audience: str | None) -> list[str]:
    """Return up to 5 search keywords, preferring audience cohort nouns.

    Audience tokens are emitted first so cohort identifiers (e.g. "adhd")
    survive the 5-token cap even when the problem statement is verbose.
    """
    ordered: list[str] = []
    if audience:
        ordered.extend(_tokens(audience))
    ordered.extend(_tokens(problem_statement))

    seen: set[str] = set()
    unique: list[str] = []
    for w in ordered:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:5]


def _pair_queries(keywords: list[str], max_pairs: int = 3) -> list[str]:
    """Build short keyword-pair queries from the top keywords.

    Picks up to ``max_pairs`` 2-token combinations from the first 4 keywords.
    Falls back to a single-keyword query if only one keyword is available.
    """
    head = keywords[:4]
    if len(head) == 0:
        return []
    if len(head) == 1:
        return [head[0]]
    pairs: list[str] = []
    for i in range(len(head)):
        for j in range(i + 1, len(head)):
            pairs.append(f"{head[i]}+{head[j]}")
            if len(pairs) >= max_pairs:
                return pairs
    return pairs


async def search_github_repos(
    client: httpx.AsyncClient,
    keywords: list[str],
) -> dict[str, Any]:
    queries = _pair_queries(keywords)
    if not queries:
        return {"repo_count": 0, "top_repos_json": [], "star_delta_30d": 0, "max_stars": 0}

    url = "https://api.github.com/search/repositories"
    repos_by_name: dict[str, dict[str, Any]] = {}

    for q in queries:
        full_q = f"{q}+in:name,description,readme"
        try:
            resp = await request_with_retry(
                client, "GET", url,
                params={"q": full_q, "sort": "stars", "per_page": 10},
                headers={"Accept": "application/vnd.github+json"},
            )
            data = resp.json()
        except Exception as exc:
            log.warning("github_search_failed", q=q, error=str(exc))
            continue

        for r in data.get("items", []):
            name = r.get("full_name", "")
            if not name or name in repos_by_name:
                continue
            repos_by_name[name] = {
                "name": name,
                "stars": r.get("stargazers_count", 0),
                "url": r.get("html_url", ""),
                "language": r.get("language"),
            }

    deduped = sorted(repos_by_name.values(), key=lambda r: r["stars"], reverse=True)
    top_repos = deduped[:5]
    max_stars = max((r["stars"] for r in deduped), default=0)
    return {
        "repo_count": len(deduped),
        "top_repos_json": top_repos,
        "star_delta_30d": 0,
        "max_stars": max_stars,
    }


async def count_show_hn_matches(
    session: AsyncSession,
    candidate: OpportunityCandidate,
    *,
    window_days: int = 30,
) -> dict[str, Any]:
    keywords = extract_keywords(
        candidate.problem_statement, candidate.audience
    )
    since = datetime.now(UTC) - timedelta(days=window_days)

    conditions = [SourceItem.source_type == "hn"]
    patterns = [f"show hn:%"] + [f"%{kw}%" for kw in keywords[:3]]
    from sqlalchemy import or_
    title_cond = or_(*(
        func.lower(SourceItem.title).like(p) for p in patterns
    ))
    conditions.append(title_cond)
    conditions.append(SourceItem.ingested_at >= since)

    from sqlalchemy import and_
    cond = and_(*conditions)

    count_result = await session.execute(
        select(func.count(SourceItem.id)).where(cond)
    )
    count = count_result.scalar_one()

    rows = await session.execute(
        select(SourceItem)
        .where(cond)
        .order_by(SourceItem.ingested_at.desc())
        .limit(20)
    )
    items = rows.scalars().all()

    top = sorted(
        items,
        key=lambda si: (si.metadata_json or {}).get("points", 0) if si.metadata_json else 0,
        reverse=True,
    )[:5]
    top_show_hn = [
        {
            "title": si.title,
            "url": si.url,
            "points": (si.metadata_json or {}).get("points", 0),
        }
        for si in top
    ]
    return {"show_hn_count": count, "top_show_hn_json": top_show_hn}


async def run_validation(
    session: AsyncSession,
    github_client: httpx.AsyncClient,
    *,
    only_active: bool = True,
    refresh_age_days: int = 7,
) -> ValidationReport:
    """Stage 6: validate active candidates against GitHub and Show HN."""
    report = ValidationReport()

    q = select(OpportunityCandidate)
    if only_active:
        q = q.where(OpportunityCandidate.is_archived.is_(False))
    q = q.where(OpportunityCandidate.specificity > 0)

    result = await session.execute(q)
    candidates = result.scalars().all()

    cutoff = datetime.now(UTC) - timedelta(days=refresh_age_days)

    for candidate in candidates:
        if candidate.is_archived:
            report.skipped_archived += 1
            continue

        # Check for recent snapshot
        recent = await session.execute(
            select(CandidateValidation)
            .where(CandidateValidation.candidate_id == candidate.id)
            .where(CandidateValidation.signal_type == "composite")
            .where(CandidateValidation.validated_at >= cutoff)
            .limit(1)
        )
        if recent.scalars().first() is not None:
            report.skipped_recent += 1
            continue

        try:
            keywords = extract_keywords(candidate.problem_statement, candidate.audience)
            github_data = await search_github_repos(github_client, keywords)

            # Phase 1 star_delta_30d: compare to previous snapshot
            prev = await session.execute(
                select(CandidateValidation)
                .where(CandidateValidation.candidate_id == candidate.id)
                .where(CandidateValidation.signal_type == "composite")
                .order_by(CandidateValidation.validated_at.desc())
                .limit(1)
            )
            prev_row = prev.scalars().first()
            if prev_row and prev_row.metadata_json:
                prev_max = prev_row.metadata_json.get("max_stars", 0) or 0
                github_data["star_delta_30d"] = github_data["max_stars"] - prev_max

            show_hn_data = await count_show_hn_matches(session, candidate)

            metadata = {**github_data, **show_hn_data}
            validation = CandidateValidation(
                candidate_id=candidate.id,
                signal_type="composite",
                signal_value=float(github_data["repo_count"]),
                validated_at=datetime.now(UTC),
                metadata_json=metadata,
            )
            session.add(validation)
            await session.flush()
            report.validated += 1
            report.details.append({"candidate_id": candidate.id, **metadata})
        except Exception as exc:
            log.error("validation_failed", candidate_id=candidate.id, error=str(exc))
            report.errors += 1

    await session.commit()
    log.info(
        "stage6_validation_complete",
        validated=report.validated,
        skipped_recent=report.skipped_recent,
        errors=report.errors,
    )
    return report

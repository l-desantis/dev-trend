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
    "through during before after above below between among while".split()
)


@dataclass
class ValidationReport:
    validated: int = 0
    skipped_recent: int = 0
    skipped_archived: int = 0
    errors: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


def extract_keywords(problem_statement: str, audience: str | None) -> list[str]:
    combined = f"{problem_statement} {audience or ''}"
    words = re.findall(r"[a-zA-Z]{3,}", combined)
    candidates = [w.lower() for w in words if w.lower() not in _STOPWORDS]
    # deduplicate preserving order, prefer longer words
    seen: set[str] = set()
    unique = []
    for w in candidates:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    unique.sort(key=len, reverse=True)
    return unique[:5]


async def search_github_repos(
    client: httpx.AsyncClient,
    keywords: list[str],
) -> dict[str, Any]:
    if not keywords:
        return {"repo_count": 0, "top_repos_json": [], "star_delta_30d": 0, "max_stars": 0}

    q = "+".join(keywords) + "+in:name,description,readme"
    url = "https://api.github.com/search/repositories"
    try:
        resp = await request_with_retry(
            client, "GET", url,
            params={"q": q, "sort": "stars", "per_page": 30},
            headers={"Accept": "application/vnd.github+json"},
        )
        data = resp.json()
    except Exception as exc:
        log.warning("github_search_failed", error=str(exc))
        return {"repo_count": 0, "top_repos_json": [], "star_delta_30d": 0, "max_stars": 0}

    total = data.get("total_count", 0)
    items = data.get("items", [])[:5]
    top_repos = [
        {
            "name": r.get("full_name", ""),
            "stars": r.get("stargazers_count", 0),
            "url": r.get("html_url", ""),
            "language": r.get("language"),
        }
        for r in items
    ]
    max_stars = max((r["stars"] for r in top_repos), default=0)
    return {
        "repo_count": min(total, 30),
        "top_repos_json": top_repos,
        "star_delta_30d": 0,  # Phase 1: approximated via prev snapshot in caller
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
    rows = await session.execute(
        select(SourceItem)
        .where(and_(*conditions))
        .order_by(SourceItem.ingested_at.desc())
        .limit(20)
    )
    items = rows.scalars().all()
    count = len(items)

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

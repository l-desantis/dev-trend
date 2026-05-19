"""Keyword extraction debugger — shows what GitHub pair queries would be issued.

Usage (inline text):
    uv run python -m scripts.debug_keywords --problem "Current task management apps fail ADHD adults" --audience "Individuals with ADHD"

Usage (all active candidates from DB):
    uv run python -m scripts.debug_keywords --all

Usage (single candidate by ID):
    uv run python -m scripts.debug_keywords --candidate-id 42
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug keyword extraction and GitHub pair queries.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--problem", help="Problem statement text (use with --audience)")
    mode.add_argument("--all", dest="all_candidates", action="store_true", help="Process all active candidates from DB")
    mode.add_argument("--candidate-id", type=int, metavar="ID", help="Single candidate by ID")
    parser.add_argument("--audience", default=None, help="Audience text (used with --problem)")
    parser.add_argument("--db-url", default=None, help="Override DATABASE_URL")
    return parser.parse_args()


def _print_candidate(label: str, problem: str, audience: str | None) -> None:
    from app.pipeline.validation import _pair_queries, extract_keywords
    keywords = extract_keywords(problem, audience)
    pairs = _pair_queries(keywords)
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(f"  problem  : {problem[:120]}")
    print(f"  audience : {audience or '(none)'}")
    print(f"  keywords : {keywords}")
    print(f"  queries  : {pairs}")
    if not pairs:
        print("  *** WARNING: no queries would be issued ***")
    elif all("+" not in q for q in pairs):
        print("  *** WARNING: single-keyword fallback only ***")


async def _run(args: argparse.Namespace) -> None:
    if args.db_url:
        import os
        os.environ["DATABASE_URL"] = args.db_url

    if args.problem:
        _print_candidate("(inline)", args.problem, args.audience)
        return

    from app.config import get_settings
    from app.db import reset_engine, _get_session_factory
    from app.models import OpportunityCandidate
    from sqlalchemy import select

    reset_engine()
    session_factory = _get_session_factory()

    async with session_factory() as session:
        if args.candidate_id:
            result = await session.execute(
                select(OpportunityCandidate).where(OpportunityCandidate.id == args.candidate_id)
            )
            candidates = result.scalars().all()
        else:
            result = await session.execute(
                select(OpportunityCandidate)
                .where(OpportunityCandidate.is_archived.is_(False))
                .where(OpportunityCandidate.specificity > 0)
                .order_by(OpportunityCandidate.id)
            )
            candidates = result.scalars().all()

    if not candidates:
        print("No candidates found.", file=sys.stderr)
        return

    for c in candidates:
        _print_candidate(
            f"candidate_id={c.id}  label={c.label or '(unlabelled)'}",
            c.problem_statement,
            c.audience,
        )

    print(f"\n{'─'*60}")
    print(f"  Total: {len(candidates)} candidate(s)")


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

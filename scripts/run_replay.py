"""Synthetic historical replay harness for evaluation.

Seeds NicheSignal rows across a configurable date range with one of three
trend profiles, then re-runs the daily-scoring loop day-by-day.

Safety: aborts unless DATABASE_URL contains 'replay' or ':memory:'. Override
with --force, but be careful not to wipe a dev/prod DB.

Usage examples:
    python scripts/run_replay.py --days 60 --profile rising --yes
    python scripts/run_replay.py --days 30 --profile flat --niches ai-habit-trackers,no-code-saas
    python scripts/run_replay.py --days 60 --profile rising --force  # any DB
"""
import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import get_session, init_db
from app.features.niche_builder import sync_niches_from_yaml
from app.forecasting.scoring import score_all_niches
from app.models import Niche, NicheScoreHistory, NicheSignal
from app.utils.datetime_utils import utc_start_of_day

_SOURCES = ("github", "hn", "reddit", "appstore")
_NICHES_YAML = Path("data/niches.yaml")


def _profile_value(profile: str, day_index: int, total_days: int) -> float:
    """Return mention_count value for a given day position (0=oldest, N-1=newest)."""
    progress = day_index / max(total_days - 1, 1)
    if profile == "flat":
        return 10.0
    if profile == "rising":
        return 2.0 + progress * 18.0  # 2 → 20 over the window
    if profile == "spiky":
        # Spike every 7 days, otherwise low
        return 20.0 if (day_index % 7 == 6) else 3.0
    raise ValueError(f"Unknown profile: {profile!r}")


async def _wipe_niche_data(niche_ids: list[int]) -> None:
    async with get_session() as session:
        await session.execute(
            delete(NicheSignal).where(NicheSignal.niche_id.in_(niche_ids))
        )
        await session.execute(
            delete(NicheScoreHistory).where(NicheScoreHistory.niche_id.in_(niche_ids))
        )
        await session.commit()


async def _seed_signals(
    niche_ids: list[int], days: int, profile: str, window_end: datetime
) -> int:
    today = utc_start_of_day(window_end)
    rows_written = 0
    async with get_session() as session:
        for day_index in range(days):
            day = today - timedelta(days=(days - 1 - day_index))
            value = _profile_value(profile, day_index, days)
            for niche_id in niche_ids:
                for source in _SOURCES:
                    session.add(NicheSignal(
                        niche_id=niche_id,
                        source_type=source,
                        metric_name="mention_count",
                        metric_value=value,
                        metric_timestamp=day,
                    ))
                    rows_written += 1
        await session.commit()
    return rows_written


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    db_url = settings.database_url

    if not args.force and "replay" not in db_url and ":memory:" not in db_url:
        print(
            f"[ERROR] DATABASE_URL does not contain 'replay' or ':memory:'.\n"
            f"        Current: {db_url}\n"
            f"        Use --force to override, or set DATABASE_URL to a replay DB.",
            file=sys.stderr,
        )
        sys.exit(1)

    await init_db()
    await sync_niches_from_yaml(_NICHES_YAML)

    async with get_session() as session:
        all_niches = (await session.execute(select(Niche))).scalars().all()

    if args.niches:
        slugs = {s.strip() for s in args.niches.split(",")}
        selected = [n for n in all_niches if n.slug in slugs]
        missing = slugs - {n.slug for n in selected}
        if missing:
            print(f"[WARN] Unknown niche slugs ignored: {', '.join(sorted(missing))}")
    else:
        selected = list(all_niches)

    if not selected:
        print("[ERROR] No niches selected.", file=sys.stderr)
        sys.exit(1)

    niche_ids = [n.id for n in selected]
    slug_by_id = {n.id: n.slug for n in selected}

    print(f"Replay: {args.days} days · profile={args.profile} · niches={[n.slug for n in selected]}")

    if not args.yes:
        confirm = input(
            f"\nThis will WIPE NicheSignal and NicheScoreHistory for {len(selected)} niche(s).\n"
            f"Continue? [y/N] "
        ).strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)

    print("Wiping existing signal and score data...")
    await _wipe_niche_data(niche_ids)

    now = datetime.now(UTC)
    print(f"Seeding {args.days} days × {len(selected)} niches × {len(_SOURCES)} sources...")
    rows = await _seed_signals(niche_ids, args.days, args.profile, now)
    print(f"  {rows} NicheSignal rows written.")

    today = utc_start_of_day(now)
    window_start = today - timedelta(days=args.days - 1)
    days_range = [window_start + timedelta(days=i) for i in range(args.days)]

    print(f"Scoring {args.days} days ({window_start.date()} → {today.date()})...")
    for d in days_range:
        await score_all_niches(as_of=d)

    # Collect results
    print("\n" + "=" * 72)
    print(f"{'Niche':<30} {'Min':>6} {'Max':>6} {'End':>6} {'Slope'}")
    print("-" * 72)

    async with get_session() as session:
        for niche_id in niche_ids:
            rows_hist = (await session.execute(
                select(NicheScoreHistory)
                .where(NicheScoreHistory.niche_id == niche_id)
                .order_by(NicheScoreHistory.scored_at)
            )).scalars().all()

            if not rows_hist:
                print(f"{'  ' + slug_by_id[niche_id]:<30} {'N/A':>6}")
                continue

            scores = [r.score_total for r in rows_hist]
            end_score = scores[-1]
            recent = scores[-7:] if len(scores) >= 7 else scores
            slope_val = recent[-1] - recent[0] if len(recent) > 1 else 0.0
            slope_label = "↑ Rising" if slope_val > 2 else ("↓ Declining" if slope_val < -2 else "→ Flat")

            print(
                f"  {slug_by_id[niche_id]:<28} "
                f"{min(scores):>6.1f} "
                f"{max(scores):>6.1f} "
                f"{end_score:>6.1f} "
                f"{slope_label}"
            )

    print("=" * 72)
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay historical scoring with synthetic signals.")
    parser.add_argument("--days", type=int, default=60, help="Synthetic history depth (default: 60)")
    parser.add_argument(
        "--profile", choices=["flat", "rising", "spiky"], default="rising",
        help="Trend shape to seed (default: rising)",
    )
    parser.add_argument(
        "--niches", type=str, default="",
        help="Comma-separated niche slugs (default: all from data/niches.yaml)",
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument(
        "--force", action="store_true",
        help="Allow any DATABASE_URL (dangerous — bypasses production-DB guard)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()

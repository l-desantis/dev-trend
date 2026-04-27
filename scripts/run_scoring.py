"""Manual smoke-test: aggregate daily signals and score all niches against live DB.

Usage: python scripts/run_scoring.py
"""
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.db import init_db
from app.features.niche_builder import sync_niches_from_yaml
from app.features.signal_aggregator import aggregate_daily_signals
from app.forecasting.scoring import score_all_niches


async def main() -> None:
    await init_db()
    await sync_niches_from_yaml(Path("data/niches.yaml"))
    now = datetime.now(UTC)
    signal_rows = await aggregate_daily_signals(now)
    niches_scored = await score_all_niches(now)
    print(f"signals_written={signal_rows} niches_scored={niches_scored}")


if __name__ == "__main__":
    asyncio.run(main())

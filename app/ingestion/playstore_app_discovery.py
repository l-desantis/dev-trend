"""Play Store app discovery — static seed loader (C-00 chose path b).

The seed YAML at data/playstore_seed_apps.yaml is the source of truth for
which apps to track. The weekly job re-reads the YAML and upserts TrackedApp
rows — allowing manual curation without code changes.

Dynamic discovery via google_play_scraper.list() is not available in v1.2.7
(list() does not exist). If that changes, replace _load_from_yaml with
_load_from_play_store.
"""
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import structlog
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TrackedApp

log = structlog.get_logger(__name__)

_SEED_PATH = Path(__file__).parent.parent.parent / "data" / "playstore_seed_apps.yaml"


class AppListing(NamedTuple):
    app_id: str
    title: str
    internal_category: str


def load_seed_apps(seed_path: Path = _SEED_PATH) -> list[AppListing]:
    """Load the curated seed YAML."""
    with open(seed_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return [
        AppListing(
            app_id=entry["app_id"],
            title=entry["title"],
            internal_category=entry["internal_category"],
        )
        for entry in data.get("apps", [])
    ]


async def refresh_app_list(
    session: AsyncSession,
    *,
    seed_path: Path = _SEED_PATH,
) -> int:
    """Upsert TrackedApp rows from the seed YAML. Returns number of rows upserted."""
    listings = load_seed_apps(seed_path)
    now = datetime.now(UTC)
    upserted = 0

    for listing in listings:
        existing = await session.get(TrackedApp, listing.app_id)
        if existing is None:
            app = TrackedApp(
                app_id=listing.app_id,
                title=listing.title,
                internal_category=listing.internal_category,
                last_seen_at=now,
            )
            session.add(app)
        else:
            existing.title = listing.title
            existing.internal_category = listing.internal_category
            existing.last_seen_at = now
        upserted += 1

    await session.commit()
    log.info("playstore_app_list_refreshed", upserted=upserted)
    return upserted

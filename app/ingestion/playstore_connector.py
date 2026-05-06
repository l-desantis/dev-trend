"""Play Store reviews connector — wraps google-play-scraper."""
import asyncio
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session
from app.ingestion.base import BaseConnector, NormalizedItem
from app.models import TrackedApp

log = structlog.get_logger(__name__)

_SEMAPHORE = asyncio.Semaphore(5)


def _to_normalized(app_id: str, raw: dict[str, Any]) -> NormalizedItem:
    # Spike-verified keys: reviewId, userName, content, score, at, repliedAt, appVersion
    return NormalizedItem(
        source_type="playstore",
        external_id=raw["reviewId"],
        title="",  # Play Store reviews rarely have a title
        body=raw.get("content") or "",
        url=f"https://play.google.com/store/apps/details?id={app_id}",
        created_at=raw.get("at"),
        metadata={
            "app_id": app_id,
            "rating": raw.get("score"),
            "user_name": raw.get("userName"),
            "app_version": raw.get("appVersion"),
            "thumbs_up": raw.get("thumbsUpCount"),
        },
        role="extraction",
    )


class PlayStoreReviewsConnector(BaseConnector):
    source_type = "playstore"

    async def fetch(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[dict]:
        settings = get_settings()
        from google_play_scraper import Sort, reviews as gps_reviews

        async with get_session() as session:
            apps = (await session.execute(select(TrackedApp))).scalars().all()

        if not apps:
            log.warning("playstore_no_tracked_apps")
            return []

        raw_items: list[dict] = []
        for app in apps:
            try:
                async with _SEMAPHORE:
                    result, _ = await asyncio.to_thread(
                        gps_reviews,
                        app.app_id,
                        sort=Sort.NEWEST,
                        count=settings.playstore_reviews_per_app,
                        lang="en",
                        country="us",
                    )
                if not result:
                    log.warning("playstore_empty_result", app_id=app.app_id)
                    continue
                for r in result:
                    at: datetime | None = r.get("at")
                    if since and at and at < since:
                        continue
                    r["_app_id"] = app.app_id
                    raw_items.append(r)
                await asyncio.sleep(1.0)
            except Exception as exc:
                log.warning("playstore_fetch_failed", app_id=app.app_id, error=str(exc))
                if "throttl" in str(exc).lower() or "429" in str(exc):
                    log.warning("playstore_likely_throttled")
                    break

        return raw_items

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        seen: set[str] = set()
        items: list[NormalizedItem] = []
        for r in raw:
            review_id = r.get("reviewId", "")
            if review_id in seen:
                continue
            seen.add(review_id)
            app_id = r.pop("_app_id", "")
            items.append(_to_normalized(app_id, r))
        return items

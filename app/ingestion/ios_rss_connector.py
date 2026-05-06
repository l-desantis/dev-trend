"""iOS App Store RSS connector — optional, behind enable_ios_rss flag.

Disabled by default (settings.enable_ios_rss=False).

Apple's customer-reviews RSS: up to 500 most-recent reviews per app, free, no auth.
URL pattern: https://itunes.apple.com/us/rss/customerreviews/id={ios_app_id}/sortBy=mostRecent/page={n}/json
"""
import asyncio
from datetime import datetime

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.db import get_session
from app.ingestion.base import BaseConnector, NormalizedItem
from app.models import TrackedApp

log = structlog.get_logger(__name__)

_RSS_BASE = "https://itunes.apple.com/us/rss/customerreviews/id={ios_app_id}/sortBy=mostRecent/page={page}/json"


def _parse_entry(ios_app_id: str, entry: dict) -> NormalizedItem | None:
    try:
        attrs = {k: (v["label"] if isinstance(v, dict) else v) for k, v in entry.items()}
        review_id = attrs.get("id", "")
        title = attrs.get("title", "")
        body = attrs.get("content", "")
        rating_str = entry.get("im:rating", {}).get("label", "") if isinstance(entry.get("im:rating"), dict) else ""
        try:
            rating = int(rating_str) if rating_str else None
        except ValueError:
            rating = None
        updated_label = entry.get("updated", {}).get("label", "") if isinstance(entry.get("updated"), dict) else ""
        created_at: datetime | None = None
        if updated_label:
            try:
                from dateutil.parser import parse as _parse
                created_at = _parse(updated_label)
            except Exception:
                pass
        return NormalizedItem(
            source_type="ios_appstore",
            external_id=f"{ios_app_id}:{review_id}",
            title=title,
            body=body,
            url=f"https://apps.apple.com/app/id{ios_app_id}",
            created_at=created_at,
            metadata={"ios_app_id": ios_app_id, "rating": rating},
            role="extraction",
        )
    except Exception as exc:
        log.warning("ios_rss_parse_error", error=str(exc))
        return None


class IosRssReviewsConnector(BaseConnector):
    source_type = "ios_appstore"

    async def fetch(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[dict]:
        settings = get_settings()
        if not settings.enable_ios_rss:
            return []

        async with get_session() as session:
            apps = (
                await session.execute(
                    select(TrackedApp).where(TrackedApp.ios_app_id.is_not(None))
                )
            ).scalars().all()

        if not apps:
            log.info("ios_rss_no_tracked_apps_with_ios_id")
            return []

        raw_items: list[dict] = []
        for app in apps:
            ios_id = app.ios_app_id
            for page in range(1, 11):  # up to 10 pages × 50 = 500 reviews
                url = _RSS_BASE.format(ios_app_id=ios_id, page=page)
                try:
                    resp = await self._request_with_retry("GET", url)
                    data = resp.json()
                    entries = data.get("feed", {}).get("entry", [])
                    if not entries:
                        break
                    for entry in entries:
                        entry["_ios_app_id"] = ios_id
                        raw_items.append(entry)
                    await asyncio.sleep(0.5)
                except Exception as exc:
                    log.warning("ios_rss_fetch_error", ios_app_id=ios_id, page=page, error=str(exc))
                    break

        return raw_items

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        seen: set[str] = set()
        items: list[NormalizedItem] = []
        for entry in raw:
            ios_app_id = entry.pop("_ios_app_id", "")
            item = _parse_entry(ios_app_id, entry)
            if item and item.external_id not in seen:
                seen.add(item.external_id)
                items.append(item)
        return items

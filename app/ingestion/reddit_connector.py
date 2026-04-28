import asyncio
from datetime import UTC, datetime

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem


class RedditConnector(BaseConnector):
    source_type = "reddit"

    async def fetch(self, since: datetime | None = None) -> list[dict]:
        settings = get_settings()
        headers = {"User-Agent": settings.reddit_user_agent}
        posts = []

        for sub in settings.reddit_subreddits:
            if since is None:
                # Regular scheduled run: single page, 50 items
                resp = await self._request_with_retry(
                    "GET",
                    f"https://www.reddit.com/r/{sub}/new.json",
                    headers=headers,
                    params={"limit": 50},
                )
                children = resp.json().get("data", {}).get("children", [])
                posts.extend(children)
            else:
                # Backfill: paginate via after-cursor until since or 1000-item ceiling
                sub_posts = await self._fetch_sub_backfill(sub, headers, since)
                posts.extend(sub_posts)
            await asyncio.sleep(1)

        return posts

    async def _fetch_sub_backfill(
        self, sub: str, headers: dict, since: datetime
    ) -> list[dict]:
        all_posts: list[dict] = []
        after: str | None = None
        oldest_age_days: float | None = None

        while len(all_posts) < 1000:
            params: dict = {"limit": 100}
            if after:
                params["after"] = after

            resp = await self._request_with_retry(
                "GET",
                f"https://www.reddit.com/r/{sub}/new.json",
                headers=headers,
                params=params,
            )
            data = resp.json().get("data", {})
            children = data.get("children", [])
            if not children:
                break

            all_posts.extend(children)
            after = data.get("after")

            # Stop when we reach posts older than since
            last_data = children[-1].get("data", {})
            last_created = last_data.get("created_utc")
            if last_created is not None:
                last_dt = datetime.fromtimestamp(last_created, tz=UTC)
                if last_dt < since:
                    oldest_age_days = (datetime.now(UTC) - last_dt).total_seconds() / 86400.0
                    break

            if not after:
                break

            await asyncio.sleep(1)

        if oldest_age_days is not None:
            self.log.info(
                "Reddit sub backfill reached since boundary",
                sub=sub,
                oldest_item_age_days=round(oldest_age_days, 1),
            )
        else:
            self.log.info(
                "Reddit sub backfill hit 1000-item ceiling",
                sub=sub,
                items=len(all_posts),
            )

        return all_posts

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        items = []
        for child in raw:
            d = child.get("data", {})
            name = d.get("name")  # t3_* fullname — globally unique
            if not name:
                continue
            created = d.get("created_utc")
            items.append(NormalizedItem(
                source_type=self.source_type,
                external_id=name,
                title=d.get("title"),
                body=d.get("selftext") or "",
                url=f"https://www.reddit.com{d['permalink']}" if d.get("permalink") else None,
                created_at=datetime.fromtimestamp(created, tz=UTC) if created else None,
                metadata={
                    "subreddit": d.get("subreddit"),
                    "ups": d.get("ups"),
                    "num_comments": d.get("num_comments"),
                    "author": d.get("author"),
                },
            ))
        return items

import asyncio
from datetime import UTC, datetime

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem


class RedditConnector(BaseConnector):
    source_type = "reddit"

    async def fetch(self) -> list[dict]:
        settings = get_settings()
        headers = {"User-Agent": settings.reddit_user_agent}
        posts = []
        for sub in settings.reddit_subreddits:
            resp = await self._request_with_retry(
                "GET",
                f"https://www.reddit.com/r/{sub}/new.json",
                headers=headers,
                params={"limit": 50},
            )
            children = resp.json().get("data", {}).get("children", [])
            posts.extend(children)
            await asyncio.sleep(1)
        return posts

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

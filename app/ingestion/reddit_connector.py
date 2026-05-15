import asyncio
from datetime import UTC, datetime

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem

_REDDIT_BACKOFF_STATUSES = {429, 403}


class RedditRateLimited(Exception):
    """Raised when Reddit returns 429 or 403; abort the current run, no retry."""

    def __init__(self, status_code: int, subreddit: str) -> None:
        super().__init__(f"Reddit returned {status_code} for r/{subreddit}")
        self.status_code = status_code
        self.subreddit = subreddit


class RedditConnector(BaseConnector):
    source_type = "reddit"

    async def fetch(self, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        settings = get_settings()
        headers = {"User-Agent": settings.reddit_user_agent}
        delay = settings.reddit_delay_seconds
        subs = settings.reddit_subreddits
        cap = settings.reddit_max_subreddits_per_run
        if cap is not None and cap >= 0:
            subs = subs[:cap]

        posts: list[dict] = []
        for sub in subs:
            try:
                if since is None:
                    sub_posts = await self._fetch_sub_latest(sub, headers)
                else:
                    sub_posts = await self._fetch_sub_backfill(sub, headers, since, delay)
            except RedditRateLimited as exc:
                self.log.warning(
                    "Reddit rate limited — skipping remaining subreddits",
                    status_code=exc.status_code,
                    subreddit=exc.subreddit,
                    subreddits_completed=subs.index(sub),
                    subreddits_total=len(subs),
                    items_so_far=len(posts),
                )
                break
            posts.extend(sub_posts)
            await asyncio.sleep(delay)

        return posts

    async def _fetch_sub_latest(self, sub: str, headers: dict) -> list[dict]:
        resp = await self._request_with_retry(
            "GET",
            f"https://www.reddit.com/r/{sub}/new.json",
            headers=headers,
            params={"limit": 50},
            no_retry_statuses=_REDDIT_BACKOFF_STATUSES,
        )
        if resp.status_code in _REDDIT_BACKOFF_STATUSES:
            raise RedditRateLimited(resp.status_code, sub)
        return resp.json().get("data", {}).get("children", [])

    async def _fetch_sub_backfill(
        self, sub: str, headers: dict, since: datetime, delay: float
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
                no_retry_statuses=_REDDIT_BACKOFF_STATUSES,
            )
            if resp.status_code in _REDDIT_BACKOFF_STATUSES:
                raise RedditRateLimited(resp.status_code, sub)

            data = resp.json().get("data", {})
            children = data.get("children", [])
            if not children:
                break

            all_posts.extend(children)
            after = data.get("after")

            last_data = children[-1].get("data", {})
            last_created = last_data.get("created_utc")
            if last_created is not None:
                last_dt = datetime.fromtimestamp(last_created, tz=UTC)
                if last_dt < since:
                    oldest_age_days = (datetime.now(UTC) - last_dt).total_seconds() / 86400.0
                    break

            if not after:
                break

            await asyncio.sleep(delay)

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
                role="extraction",
            ))
        return items

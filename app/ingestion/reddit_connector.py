import asyncio
import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem

_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_PERMALINK_ID_RE = re.compile(r"/comments/([a-z0-9]+)/", re.IGNORECASE)


def _extract_name(entry: ET.Element) -> str | None:
    raw_id = (entry.findtext("a:id", default="", namespaces=_ATOM_NS) or "").strip()
    if raw_id.startswith("t3_"):
        return raw_id

    link_el = entry.find("a:link", _ATOM_NS)
    href = link_el.get("href") if link_el is not None else None
    if not href:
        return None
    m = _PERMALINK_ID_RE.search(href)
    if not m:
        return None
    return f"t3_{m.group(1)}"


def _entry_to_child(entry: ET.Element) -> dict | None:
    name = _extract_name(entry)
    if not name:
        return None

    title = (entry.findtext("a:title", default="", namespaces=_ATOM_NS) or "").strip()

    content_el = entry.find("a:content", _ATOM_NS)
    body_html = (content_el.text or "") if content_el is not None else ""

    link_el = entry.find("a:link", _ATOM_NS)
    full_url = link_el.get("href") if link_el is not None else None
    permalink: str | None = None
    if full_url:
        permalink = full_url.replace("https://www.reddit.com", "")
        if not permalink.startswith("/"):
            permalink = "/" + permalink

    category_el = entry.find("a:category", _ATOM_NS)
    subreddit = category_el.get("term") if category_el is not None else None

    author_name = entry.findtext("a:author/a:name", default="", namespaces=_ATOM_NS) or ""
    author = author_name[3:] if author_name.startswith("/u/") else author_name

    published = entry.findtext("a:published", default="", namespaces=_ATOM_NS) or ""
    created_utc: int | None = None
    if published:
        try:
            created_utc = int(
                datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp()
            )
        except ValueError:
            created_utc = None

    return {
        "data": {
            "name": name,
            "title": title,
            "selftext": body_html,
            "permalink": permalink,
            "subreddit": subreddit,
            "author": author,
            "created_utc": created_utc,
        }
    }


class RedditConnector(BaseConnector):
    source_type = "reddit"

    async def fetch(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[dict]:
        settings = get_settings()
        headers = {"User-Agent": settings.reddit_user_agent}
        delay = settings.reddit_delay_seconds
        subs = settings.reddit_subreddits
        cap = settings.reddit_max_subreddits_per_run
        if cap is not None and cap >= 0:
            subs = subs[:cap]

        since_ts = since.timestamp() if since is not None else None

        children: list[dict] = []
        for sub in subs:
            try:
                sub_children = await self._fetch_sub(sub, headers, since_ts)
            except Exception as exc:  # noqa: BLE001 — graceful per-sub degradation
                self.log.warning(
                    "Reddit RSS fetch failed — skipping subreddit",
                    subreddit=sub,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                await asyncio.sleep(delay)
                continue
            children.extend(sub_children)
            await asyncio.sleep(delay)

        return children

    async def _fetch_sub(
        self, sub: str, headers: dict, since_ts: float | None
    ) -> list[dict]:
        url = f"https://www.reddit.com/r/{sub}/new.rss"
        resp = await self._request_with_retry("GET", url, headers=headers)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        entries = root.findall("a:entry", _ATOM_NS)

        children: list[dict] = []
        for entry in entries:
            child = _entry_to_child(entry)
            if child is None:
                continue
            if since_ts is not None:
                created = child["data"].get("created_utc")
                if created is None or created < since_ts:
                    continue
            children.append(child)

        self.log.info(
            "Reddit RSS sub fetch complete",
            subreddit=sub,
            items=len(children),
        )
        return children

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        items = []
        for child in raw:
            d = child.get("data", {})
            name = d.get("name")
            if not name:
                continue
            created = d.get("created_utc")
            items.append(NormalizedItem(
                source_type=self.source_type,
                external_id=name,
                title=d.get("title"),
                body=d.get("selftext") or "",
                url=(
                    f"https://www.reddit.com{d['permalink']}"
                    if d.get("permalink")
                    else None
                ),
                created_at=(
                    datetime.fromtimestamp(created, tz=UTC) if created else None
                ),
                metadata={
                    "subreddit": d.get("subreddit"),
                    "ups": None,
                    "num_comments": None,
                    "author": d.get("author"),
                },
                role="extraction",
            ))
        return items

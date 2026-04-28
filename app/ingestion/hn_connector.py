from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem


class HNConnector(BaseConnector):
    source_type = "hn"
    _BASE = "https://hn.algolia.com/api/v1/search_by_date"

    async def fetch(self, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        if since is None:
            # Regular scheduled run: 6h window, single page, no upper bound
            since = datetime.now(UTC) - timedelta(hours=6)
            resp = await self._request_with_retry(
                "GET",
                self._BASE,
                params={
                    "tags": "story",
                    "numericFilters": f"created_at_i>{int(since.timestamp())}",
                    "hitsPerPage": 200,
                },
            )
            return resp.json().get("hits", [])

        # Backfill: paginate until Algolia cap or items cap
        settings = get_settings()
        numeric_filters = f"created_at_i>{int(since.timestamp())}"
        if until is not None:
            numeric_filters += f",created_at_i<={int(until.timestamp())}"
        max_items = settings.backfill_max_items_per_source
        all_hits: list[dict] = []
        page = 0
        while len(all_hits) < max_items:
            resp = await self._request_with_retry(
                "GET",
                self._BASE,
                params={
                    "tags": "story",
                    "numericFilters": numeric_filters,
                    "hitsPerPage": 1000,
                    "page": page,
                },
            )
            data = resp.json()
            hits = data.get("hits", [])
            if not hits:
                break
            all_hits.extend(hits)
            nb_pages = data.get("nbPages", 1)
            if page >= nb_pages - 1:
                break
            page += 1
        return all_hits[:max_items]

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        items = []
        for hit in raw:
            oid = hit.get("objectID")
            if not oid:
                continue
            items.append(NormalizedItem(
                source_type=self.source_type,
                external_id=str(oid),
                title=hit.get("title"),
                body=hit.get("story_text") or hit.get("url") or "",
                url=f"https://news.ycombinator.com/item?id={oid}",
                created_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC)
                    if "created_at_i" in hit else None,
                metadata={
                    "points": hit.get("points"),
                    "num_comments": hit.get("num_comments"),
                    "author": hit.get("author"),
                },
            ))
        return items

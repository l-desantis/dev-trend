from datetime import UTC, datetime, timedelta

from app.ingestion.base import BaseConnector, NormalizedItem


class HNConnector(BaseConnector):
    source_type = "hn"
    _BASE = "https://hn.algolia.com/api/v1/search_by_date"

    async def fetch(self) -> list[dict]:
        since = datetime.now(UTC) - timedelta(hours=6)
        since_epoch = int(since.timestamp())
        resp = await self._request_with_retry(
            "GET",
            self._BASE,
            params={
                "tags": "story",
                "numericFilters": f"created_at_i>{since_epoch}",
                "hitsPerPage": 200,
            },
        )
        return resp.json().get("hits", [])

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

import structlog
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem

log = structlog.get_logger(__name__)


class GithubConnector(BaseConnector):
    source_type = "github"
    _BASE = "https://api.github.com/search/repositories"

    async def fetch(self, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        settings = get_settings()
        headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        else:
            log.warning("GITHUB_TOKEN not set — running anonymous (60 req/h)", component="GithubConnector")

        if since is None:
            since = datetime.now(UTC) - timedelta(days=settings.github_search_lookback_days)
            # Regular scheduled run: single page, no upper bound
            q = f"stars:>{settings.github_star_threshold} pushed:>{since.strftime('%Y-%m-%d')}"
            resp = await self._request_with_retry(
                "GET",
                self._BASE,
                headers=headers,
                params={"q": q, "sort": "updated", "order": "desc", "per_page": 100},
            )
            return resp.json().get("items", [])

        # Backfill: paginate until empty or cap reached
        q = f"stars:>{settings.github_star_threshold} pushed:>{since.strftime('%Y-%m-%d')}"
        if until is not None:
            q += f" pushed:<={until.strftime('%Y-%m-%d')}"
        max_items = settings.backfill_max_items_per_source
        all_items: list[dict] = []
        page = 1
        while len(all_items) < max_items:
            resp = await self._request_with_retry(
                "GET",
                self._BASE,
                headers=headers,
                params={"q": q, "sort": "updated", "order": "desc", "per_page": 100, "page": page},
            )
            batch = resp.json().get("items", [])
            if not batch:
                break
            all_items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return all_items[:max_items]

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        items = []
        for repo in raw:
            created = repo.get("created_at")
            items.append(NormalizedItem(
                source_type=self.source_type,
                external_id=str(repo["id"]),
                title=repo["full_name"],
                body=repo.get("description") or "",
                url=repo["html_url"],
                created_at=datetime.fromisoformat(created.rstrip("Z")).replace(tzinfo=UTC)
                    if created else None,
                metadata={
                    "stars": repo.get("stargazers_count"),
                    "forks": repo.get("forks_count"),
                    "language": repo.get("language"),
                    "topics": repo.get("topics", []),
                    "pushed_at": repo.get("pushed_at"),
                },
                role="validation",
            ))
        return items

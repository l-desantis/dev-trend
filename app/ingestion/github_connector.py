import structlog
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem

log = structlog.get_logger(__name__)


class GithubConnector(BaseConnector):
    source_type = "github"
    _BASE = "https://api.github.com/search/repositories"

    async def fetch(self) -> list[dict]:
        settings = get_settings()
        headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        else:
            log.warning("GITHUB_TOKEN not set — running anonymous (60 req/h)", component="GithubConnector")

        since = (datetime.now(UTC) - timedelta(days=settings.github_search_lookback_days)).strftime("%Y-%m-%d")
        resp = await self._request_with_retry(
            "GET",
            self._BASE,
            headers=headers,
            params={
                "q": f"stars:>{settings.github_star_threshold} pushed:>{since}",
                "sort": "updated",
                "order": "desc",
                "per_page": 100,
            },
        )
        return resp.json().get("items", [])

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
            ))
        return items

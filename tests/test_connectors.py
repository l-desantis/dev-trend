import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock

import httpx
import pytest

from app.db import init_db
from app.ingestion.appstore_mock_connector import AppStoreMockConnector
from app.ingestion.base import BaseConnector, ConnectorRunRegistry, NormalizedItem
from app.ingestion.github_connector import GithubConnector
from app.ingestion.hn_connector import HNConnector
from app.ingestion.reddit_connector import RedditConnector
from app.models import SourceItem
from app.db import get_session
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _registry() -> ConnectorRunRegistry:
    return ConnectorRunRegistry()


class FakeConnector(BaseConnector):
    source_type: ClassVar[str] = "fake"

    def __init__(self, client, registry, items: list[NormalizedItem]):
        super().__init__(client, registry)
        self._items = items

    async def fetch(self, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        return [{}] * len(self._items)

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        return self._items


def _fake_items(n: int = 3) -> list[NormalizedItem]:
    return [
        NormalizedItem(
            source_type="fake",
            external_id=f"id-{i}",
            title=f"Title {i}",
            body=f"Body {i}",
            url=f"https://example.com/{i}",
            created_at=datetime(2026, 1, i + 1, tzinfo=UTC),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# BaseConnector
# ---------------------------------------------------------------------------

class TestBaseConnector:
    async def test_happy_path(self):
        await init_db()
        client = httpx.AsyncClient()
        items = _fake_items(3)
        connector = FakeConnector(client, _registry(), items)
        status = await connector.run()
        assert status.last_status == "ok"
        assert status.items_ingested == 3

    async def test_idempotent(self):
        await init_db()
        client = httpx.AsyncClient()
        items = _fake_items(3)
        registry = _registry()
        connector = FakeConnector(client, registry, items)
        await connector.run()
        status = await connector.run()
        assert status.items_ingested == 0  # all already inserted

    async def test_retry_on_429(self):
        await init_db()
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"hits": []})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        class RetryConnector(BaseConnector):
            source_type: ClassVar[str] = "retry_test"

            async def fetch(self, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
                resp = await self._request_with_retry("GET", "https://example.com/api")
                return resp.json().get("hits", [])

            def normalize(self, raw):
                return []

        connector = RetryConnector(client, _registry())
        status = await connector.run()
        assert status.last_status == "ok"
        assert call_count == 2


# ---------------------------------------------------------------------------
# Role tagging
# ---------------------------------------------------------------------------

class TestRedditRoleTagging:
    def test_reddit_normalizes_role_extraction(self):
        client = httpx.AsyncClient()
        connector = RedditConnector(client, _registry())
        raw = [{"data": {"name": "t3_abc", "title": "Test post", "selftext": "body",
                         "permalink": "/r/startups/comments/abc", "created_utc": 1700000000.0}}]
        items = connector.normalize(raw)
        assert len(items) == 1
        assert items[0].role == "extraction"


class TestGithubRoleTagging:
    def test_github_normalizes_role_validation(self):
        client = httpx.AsyncClient()
        connector = GithubConnector(client, _registry())
        raw = [{"id": 1, "full_name": "owner/repo", "html_url": "https://github.com/owner/repo",
                "description": "A tool", "created_at": "2026-01-01T00:00:00Z",
                "stargazers_count": 100, "forks_count": 5, "language": "Python",
                "topics": [], "pushed_at": "2026-04-01T00:00:00Z"}]
        items = connector.normalize(raw)
        assert len(items) == 1
        assert items[0].role == "validation"


class TestHNRoleTagging:
    def _hit(self, title: str, tags: list[str] | None = None) -> dict:
        return {
            "objectID": "12345",
            "title": title,
            "created_at_i": 1700000000,
            "_tags": tags or [],
        }

    def test_hn_normalizes_role_split(self):
        client = httpx.AsyncClient()
        connector = HNConnector(client, _registry())

        ask_hn = connector.normalize([self._hit("Ask HN: Why is there no good X?")])[0]
        show_hn = connector.normalize([self._hit("Show HN: My new tool")])[0]
        news = connector.normalize([self._hit("Some news headline")])[0]

        assert ask_hn.role == "extraction"
        assert show_hn.role == "validation"
        assert news.role == "ignored"

    def test_hn_comment_tag_yields_extraction(self):
        client = httpx.AsyncClient()
        connector = HNConnector(client, _registry())
        items = connector.normalize([self._hit("Random comment", tags=["comment"])])
        assert items[0].role == "extraction"


# ---------------------------------------------------------------------------
# GithubConnector
# ---------------------------------------------------------------------------

class TestGithubConnector:
    async def test_parses_payload(self):
        fixture = Path("tests/fixtures/github_search.json").read_text()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=fixture.encode())

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = GithubConnector(client, _registry())
        raw = await connector.fetch()
        items = connector.normalize(raw)
        assert len(items) == 3
        assert items[0].source_type == "github"
        assert items[0].external_id == "123456"
        assert items[0].title == "owner/ai-habit-tracker"
        assert items[0].url == "https://github.com/owner/ai-habit-tracker"
        assert items[0].metadata["stars"] == 312


# ---------------------------------------------------------------------------
# HNConnector
# ---------------------------------------------------------------------------

class TestHNConnector:
    async def test_parses_payload(self):
        fixture = Path("tests/fixtures/hn_search.json").read_text()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=fixture.encode())

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = HNConnector(client, _registry())
        raw = await connector.fetch()
        items = connector.normalize(raw)
        assert len(items) == 3
        assert items[0].source_type == "hn"
        assert items[0].external_id == "39000001"
        assert "news.ycombinator.com" in items[0].url
        assert items[1].metadata["points"] == 512


# ---------------------------------------------------------------------------
# RedditConnector
# ---------------------------------------------------------------------------

class TestRedditConnector:
    async def test_loops_subreddits(self, monkeypatch):
        fixture = Path("tests/fixtures/reddit_new.json").read_text()
        request_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            request_urls.append(str(request.url))
            return httpx.Response(200, content=fixture.encode())

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
        from app.config import get_settings
        get_settings.cache_clear()

        connector = RedditConnector(client, _registry())
        raw = await connector.fetch()
        assert len(request_urls) == 2
        assert any("startups" in u for u in request_urls)
        assert any("SideProject" in u for u in request_urls)


# ---------------------------------------------------------------------------
# AppStoreMockConnector
# ---------------------------------------------------------------------------

class TestAppStoreMockConnector:
    async def test_reads_tmp_dir(self, tmp_path, monkeypatch):
        await init_db()
        record = {
            "external_id": "appstore-test-001",
            "title": "TestApp",
            "description": "A test app for ai wellness coaching",
            "category": "wellness",
            "growth_index": 0.5,
            "install_proxy": 1000,
            "rating": 4.0,
            "review_sentiment": 0.6,
            "competitor_density": 2,
            "updated_at": "2026-04-20T00:00:00Z",
        }
        (tmp_path / "appstore_test.json").write_text(json.dumps([record]))

        monkeypatch.setenv("ENABLE_MOCK_APPSTORE", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        client = httpx.AsyncClient()
        connector = AppStoreMockConnector(client, _registry(), mock_dir=tmp_path)
        raw = await connector.fetch()
        items = connector.normalize(raw)
        assert len(items) == 1
        assert items[0].external_id == "appstore-test-001"
        assert items[0].title == "TestApp"

    async def test_since_param_is_ignored(self, tmp_path, monkeypatch):
        record = {
            "external_id": "appstore-since-001",
            "title": "SinceApp",
            "description": "Test",
            "category": "wellness",
            "growth_index": 0.1,
            "install_proxy": 100,
            "rating": 4.0,
            "review_sentiment": 0.5,
            "competitor_density": 1,
            "updated_at": "2026-01-01T00:00:00Z",
        }
        (tmp_path / "appstore_since.json").write_text(json.dumps([record]))

        monkeypatch.setenv("ENABLE_MOCK_APPSTORE", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        client = httpx.AsyncClient()
        connector = AppStoreMockConnector(client, _registry(), mock_dir=tmp_path)
        raw = await connector.fetch(since=datetime(2026, 4, 1, tzinfo=UTC))
        assert len(raw) == 1


# ---------------------------------------------------------------------------
# Backfill: since-aware pagination tests
# ---------------------------------------------------------------------------

def _make_github_items(n: int) -> list[dict]:
    return [
        {
            "id": 900000 + i,
            "full_name": f"owner/repo-{i}",
            "html_url": f"https://github.com/owner/repo-{i}",
            "description": f"Repo {i}",
            "created_at": "2026-01-01T00:00:00Z",
            "stargazers_count": i,
            "forks_count": 0,
            "language": "Python",
            "topics": [],
            "pushed_at": "2026-04-01T00:00:00Z",
        }
        for i in range(n)
    ]


class TestGithubConnectorSince:
    async def test_paginates_with_since(self, monkeypatch):
        monkeypatch.setenv("BACKFILL_MAX_ITEMS_PER_SOURCE", "200")
        from app.config import get_settings
        get_settings.cache_clear()

        pages_requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "")
            pages_requested.append(page)
            if page == "2":
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": _make_github_items(100)})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = GithubConnector(client, _registry())
        since = datetime(2026, 1, 1, tzinfo=UTC)
        raw = await connector.fetch(since=since)
        assert "1" in pages_requested
        assert "2" in pages_requested
        assert len(raw) == 100

    async def test_no_since_single_page(self):
        fixture = Path("tests/fixtures/github_search.json").read_text()
        pages_requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            pages_requested.append(request.url.params.get("page", ""))
            return httpx.Response(200, content=fixture.encode())

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = GithubConnector(client, _registry())
        await connector.fetch()
        assert len(pages_requested) == 1
        assert pages_requested[0] == ""

    async def test_until_added_to_query(self):
        queries: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            queries.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"items": []})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = GithubConnector(client, _registry())
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 8, tzinfo=UTC)
        await connector.fetch(since=since, until=until)
        assert len(queries) == 1
        assert "pushed:>2026-01-01" in queries[0]
        assert "pushed:<=2026-01-08" in queries[0]


class TestHNConnectorSince:
    async def test_paginates_with_since(self):
        fixture_page0 = {
            "hits": [{"objectID": "1", "title": "h1", "created_at_i": 1700000000}],
            "nbPages": 2,
        }
        fixture_page1 = {
            "hits": [{"objectID": "2", "title": "h2", "created_at_i": 1700000001}],
            "nbPages": 2,
        }
        pages_requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "0")
            pages_requested.append(page)
            if page == "0":
                return httpx.Response(200, json=fixture_page0)
            return httpx.Response(200, json=fixture_page1)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = HNConnector(client, _registry())
        since = datetime(2026, 1, 1, tzinfo=UTC)
        raw = await connector.fetch(since=since)
        assert "0" in pages_requested
        assert "1" in pages_requested
        assert len(raw) == 2

    async def test_no_since_single_page(self):
        fixture = Path("tests/fixtures/hn_search.json").read_text()
        requests_made: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(str(request.url))
            return httpx.Response(200, content=fixture.encode())

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = HNConnector(client, _registry())
        raw = await connector.fetch()
        assert len(requests_made) == 1
        assert "page" not in requests_made[0]


class TestRedditConnectorSince:
    async def test_paginates_with_since_until_boundary(self, monkeypatch):
        from datetime import timedelta
        now = datetime.now(UTC)
        old_ts = (now - timedelta(days=35)).timestamp()

        page1 = {
            "data": {
                "children": [{"data": {"name": "t3_a", "title": "T1", "created_utc": now.timestamp(),
                                        "permalink": "/r/x/a", "selftext": ""}}],
                "after": "t3_a",
            }
        }
        page2 = {
            "data": {
                "children": [{"data": {"name": "t3_b", "title": "T2", "created_utc": old_ts,
                                        "permalink": "/r/x/b", "selftext": ""}}],
                "after": None,
            }
        }
        pages: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            pages.append(len(pages))
            if "after" in request.url.params:
                return httpx.Response(200, json=page2)
            return httpx.Response(200, json=page1)

        monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
        from app.config import get_settings
        get_settings.cache_clear()

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = RedditConnector(client, _registry())
        since = now - timedelta(days=30)
        raw = await connector.fetch(since=since)
        assert len(pages) == 2
        assert len(raw) == 2

    async def test_no_since_single_page_per_sub(self, monkeypatch):
        fixture = Path("tests/fixtures/reddit_new.json").read_text()
        requests_made: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(str(request.url))
            return httpx.Response(200, content=fixture.encode())

        monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
        from app.config import get_settings
        get_settings.cache_clear()

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = RedditConnector(client, _registry())
        await connector.fetch()
        assert len(requests_made) == 1
        assert "after" not in requests_made[0]


class TestHNConnectorUntil:
    async def test_until_added_to_numeric_filters(self):
        filters_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            filters_seen.append(request.url.params.get("numericFilters", ""))
            return httpx.Response(200, json={"hits": [], "nbPages": 1})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = HNConnector(client, _registry())
        since = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 8, tzinfo=UTC)
        await connector.fetch(since=since, until=until)
        assert len(filters_seen) == 1
        assert f"created_at_i>{int(since.timestamp())}" in filters_seen[0]
        assert f"created_at_i<={int(until.timestamp())}" in filters_seen[0]


# ---------------------------------------------------------------------------
# Weekly window helper
# ---------------------------------------------------------------------------

def test_weekly_windows_30_days():
    from app.ingestion.backfill import _weekly_windows
    since = datetime(2026, 1, 1, tzinfo=UTC)
    until = datetime(2026, 1, 31, tzinfo=UTC)
    windows = _weekly_windows(since, until)
    assert len(windows) == 5
    assert windows[0][0] == since
    assert windows[-1][1] == until
    for i in range(len(windows) - 1):
        assert windows[i][1] == windows[i + 1][0]


def test_weekly_windows_exact_multiple():
    from app.ingestion.backfill import _weekly_windows
    since = datetime(2026, 1, 1, tzinfo=UTC)
    until = datetime(2026, 1, 15, tzinfo=UTC)
    windows = _weekly_windows(since, until)
    assert len(windows) == 2
    assert windows[0] == (since, datetime(2026, 1, 8, tzinfo=UTC))
    assert windows[1] == (datetime(2026, 1, 8, tzinfo=UTC), until)


# ---------------------------------------------------------------------------
# GitHub 4xx
# ---------------------------------------------------------------------------

class TestGithubConnector4xx:
    async def test_403_results_in_error_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = GithubConnector(client, _registry())
        status = await connector.run()
        assert status.last_status == "error"
        assert status.error is not None


class TestRedditCeilingLog:
    async def test_1000_item_ceiling_log_fires(self, monkeypatch):
        import structlog.testing
        from datetime import timedelta

        now_ts = datetime.now(UTC).timestamp()
        page_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            page_count[0] += 1
            children = [
                {
                    "data": {
                        "name": f"t3_{page_count[0]}_{i}",
                        "title": "T",
                        "created_utc": now_ts,
                        "permalink": "/r/startups/comments/x",
                        "selftext": "",
                    }
                }
                for i in range(100)
            ]
            after = "t3_next" if page_count[0] < 10 else None
            return httpx.Response(200, json={"data": {"children": children, "after": after}})

        monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
        from app.config import get_settings
        get_settings.cache_clear()

        from unittest.mock import AsyncMock
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = RedditConnector(client, _registry())
        since = datetime.now(UTC) - timedelta(days=60)

        with structlog.testing.capture_logs() as cap:
            await connector.fetch(since=since)

        ceiling_logs = [e for e in cap if "ceiling" in e.get("event", "").lower()]
        assert ceiling_logs, f"No ceiling log found; captured: {cap}"


class TestAppStoreSinceNoOp:
    async def test_since_does_not_filter_mock_data(self, monkeypatch):
        monkeypatch.setenv("ENABLE_MOCK_APPSTORE", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        client = httpx.AsyncClient()
        mock_dir = Path("data/mock")
        connector = AppStoreMockConnector(client, _registry(), mock_dir=mock_dir)

        raw_all = await connector.fetch()
        raw_with_since = await connector.fetch(since=datetime(2026, 4, 27, tzinfo=UTC))

        assert len(raw_all) == len(raw_with_since)
        assert raw_all == raw_with_since

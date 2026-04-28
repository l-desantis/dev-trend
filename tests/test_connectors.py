import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock

import httpx
import pytest

from app.db import init_db
from app.features.niche_builder import NicheMatcher, sync_niches_from_yaml
from app.ingestion.appstore_mock_connector import AppStoreMockConnector
from app.ingestion.base import BaseConnector, ConnectorRunRegistry, NormalizedItem
from app.ingestion.github_connector import GithubConnector
from app.ingestion.hn_connector import HNConnector
from app.ingestion.reddit_connector import RedditConnector
from app.models import Niche, SourceItem
from app.db import get_session
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_matcher() -> NicheMatcher:
    return NicheMatcher({})


def _registry() -> ConnectorRunRegistry:
    return ConnectorRunRegistry()


class FakeConnector(BaseConnector):
    source_type: ClassVar[str] = "fake"

    def __init__(self, client, matcher, registry, items: list[NormalizedItem]):
        super().__init__(client, matcher, registry)
        self._items = items

    async def fetch(self, since: datetime | None = None) -> list[dict]:
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
        connector = FakeConnector(client, _empty_matcher(), _registry(), items)
        status = await connector.run()
        assert status.last_status == "ok"
        assert status.items_ingested == 3

    async def test_idempotent(self):
        await init_db()
        client = httpx.AsyncClient()
        items = _fake_items(3)
        registry = _registry()
        connector = FakeConnector(client, _empty_matcher(), registry, items)
        await connector.run()
        status = await connector.run()
        assert status.items_ingested == 0  # all already inserted

    async def test_attaches_niche_id(self):
        await init_db()
        async with get_session() as session:
            niche = Niche(
                slug="test-niche",
                name="Test Niche",
                keywords_json=["ai wellness"],
            )
            session.add(niche)
            await session.commit()
            await session.refresh(niche)
            niche_id = niche.id

        matcher = await NicheMatcher.from_db()
        client = httpx.AsyncClient()
        items = [NormalizedItem(
            source_type="fake",
            external_id="niche-item-1",
            title="AI wellness coaching app",
            body="Daily ai wellness habit tracker",
            url=None,
            created_at=None,
        )]
        connector = FakeConnector(client, matcher, _registry(), items)
        await connector.run()

        async with get_session() as session:
            result = await session.execute(
                select(SourceItem).where(SourceItem.external_id == "niche-item-1")
            )
            row = result.scalar_one()
        assert row.niche_id == niche_id

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

            async def fetch(self, since: datetime | None = None) -> list[dict]:
                resp = await self._request_with_retry("GET", "https://example.com/api")
                return resp.json().get("hits", [])

            def normalize(self, raw):
                return []

        connector = RetryConnector(client, _empty_matcher(), _registry())
        status = await connector.run()
        assert status.last_status == "ok"
        assert call_count == 2


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
        connector = GithubConnector(client, _empty_matcher(), _registry())
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
        connector = HNConnector(client, _empty_matcher(), _registry())
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

        connector = RedditConnector(client, _empty_matcher(), _registry())
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
        connector = AppStoreMockConnector(client, _empty_matcher(), _registry(), mock_dir=tmp_path)
        raw = await connector.fetch()
        items = connector.normalize(raw)
        assert len(items) == 1
        assert items[0].external_id == "appstore-test-001"
        assert items[0].title == "TestApp"

    async def test_all_records_match_at_least_one_niche(self, tmp_path, monkeypatch):
        """Every mock app record must hit ≥1 niche keyword."""
        await init_db()
        await sync_niches_from_yaml(Path("data/niches.yaml"))
        matcher = await NicheMatcher.from_db()

        monkeypatch.setenv("ENABLE_MOCK_APPSTORE", "true")
        from app.config import get_settings
        get_settings.cache_clear()

        client = httpx.AsyncClient()
        mock_dir = Path("data/mock")
        connector = AppStoreMockConnector(client, matcher, _registry(), mock_dir=mock_dir)
        raw = await connector.fetch()
        items = connector.normalize(raw)
        assert items, "No mock records found"
        unmatched = [i for i in items if matcher.match(i.title, i.body) is None]
        assert unmatched == [], f"Items with no niche match: {[i.external_id for i in unmatched]}"

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
        connector = AppStoreMockConnector(client, _empty_matcher(), _registry(), mock_dir=tmp_path)
        # Passing a recent since should not filter the static mock data
        raw = await connector.fetch(since=datetime(2026, 4, 1, tzinfo=UTC))
        assert len(raw) == 1


# ---------------------------------------------------------------------------
# Backfill: since-aware pagination tests
# ---------------------------------------------------------------------------

def _make_github_items(n: int) -> list[dict]:
    """Build n minimal GitHub repo dicts."""
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
        """When since is provided, GithubConnector paginates: page 1 full → page 2 requested."""
        monkeypatch.setenv("BACKFILL_MAX_ITEMS_PER_SOURCE", "200")
        from app.config import get_settings
        get_settings.cache_clear()

        # page 1 returns 100 items (full page → connector requests page 2)
        # page 2 returns 0 items → connector stops
        pages_requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("page", "")
            pages_requested.append(page)
            if page == "2":
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": _make_github_items(100)})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = GithubConnector(client, _empty_matcher(), _registry())
        since = datetime(2026, 1, 1, tzinfo=UTC)
        raw = await connector.fetch(since=since)
        assert "1" in pages_requested
        assert "2" in pages_requested
        assert len(raw) == 100

    async def test_no_since_single_page(self):
        """Without since, GithubConnector uses one page (existing behavior)."""
        fixture = Path("tests/fixtures/github_search.json").read_text()
        pages_requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            pages_requested.append(request.url.params.get("page", ""))
            return httpx.Response(200, content=fixture.encode())

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = GithubConnector(client, _empty_matcher(), _registry())
        await connector.fetch()
        # No page param for single-page run
        assert len(pages_requested) == 1
        assert pages_requested[0] == ""


class TestHNConnectorSince:
    async def test_paginates_with_since(self):
        """When since is provided, HNConnector paginates through Algolia pages."""
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
        connector = HNConnector(client, _empty_matcher(), _registry())
        since = datetime(2026, 1, 1, tzinfo=UTC)
        raw = await connector.fetch(since=since)
        assert "0" in pages_requested
        assert "1" in pages_requested
        assert len(raw) == 2

    async def test_no_since_single_page(self):
        """Without since, HNConnector uses one request with 6h window."""
        fixture = Path("tests/fixtures/hn_search.json").read_text()
        requests_made: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests_made.append(str(request.url))
            return httpx.Response(200, content=fixture.encode())

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        connector = HNConnector(client, _empty_matcher(), _registry())
        raw = await connector.fetch()
        assert len(requests_made) == 1
        # No page param for regular run
        assert "page" not in requests_made[0]


class TestRedditConnectorSince:
    async def test_paginates_with_since_until_boundary(self, monkeypatch):
        """With since, RedditConnector paginates using after-cursor and stops at since."""
        from datetime import timedelta
        now = datetime.now(UTC)
        old_ts = (now - timedelta(days=35)).timestamp()  # older than since

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
        connector = RedditConnector(client, _empty_matcher(), _registry())
        since = now - timedelta(days=30)
        raw = await connector.fetch(since=since)
        # Both pages fetched, stopped after finding item older than since
        assert len(pages) == 2
        # t3_a is new (included), t3_b is old (included because already fetched)
        assert len(raw) == 2

    async def test_no_since_single_page_per_sub(self, monkeypatch):
        """Without since, RedditConnector uses single-page per sub (existing behavior)."""
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
        connector = RedditConnector(client, _empty_matcher(), _registry())
        await connector.fetch()
        assert len(requests_made) == 1
        assert "after" not in requests_made[0]

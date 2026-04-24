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

    async def fetch(self) -> list[dict]:
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

            async def fetch(self) -> list[dict]:
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

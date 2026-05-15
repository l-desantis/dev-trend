import asyncio
from pathlib import Path

import httpx
import pytest

from app.config import Settings, get_settings
from app.ingestion.base import ConnectorRunRegistry
from app.ingestion.reddit_connector import RedditConnector, RedditRateLimited

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "reddit_new.json"


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ok_response_body() -> bytes:
    return _FIXTURE.read_bytes()


def _make_connector(handler) -> tuple[RedditConnector, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    registry = ConnectorRunRegistry()
    connector = RedditConnector(client, registry)
    return connector, client


@pytest.mark.asyncio
async def test_sends_configured_user_agent(monkeypatch):
    monkeypatch.setenv("REDDIT_USER_AGENT", "MyAgent/9.9 (contact: a@b.co)")
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    seen_uas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_uas.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, content=_ok_response_body())

    connector, client = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_uas == ["MyAgent/9.9 (contact: a@b.co)"]


@pytest.mark.asyncio
async def test_delay_between_subreddits(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject,Entrepreneur")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "1.5")
    get_settings.cache_clear()

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("app.ingestion.reddit_connector.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_ok_response_body())

    connector, client = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    # 3 subs → 3 inter-sub sleeps of 1.5s each.
    assert sleeps == [1.5, 1.5, 1.5]


@pytest.mark.asyncio
async def test_max_subreddits_per_run_caps_loop(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject,Entrepreneur,reactnative")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    monkeypatch.setenv("REDDIT_MAX_SUBREDDITS_PER_RUN", "2")
    get_settings.cache_clear()

    seen_subs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        seen_subs.append(parts[1])
        return httpx.Response(200, content=_ok_response_body())

    connector, client = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_subs == ["startups", "SideProject"]


@pytest.mark.asyncio
async def test_429_short_circuits_remaining_subs(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject,Entrepreneur")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 2:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, content=_ok_response_body())

    connector, client = _make_connector(handler)
    try:
        result = await connector.fetch()
    finally:
        await client.aclose()

    # First sub succeeded, second got 429 → third never called.
    assert call_count["n"] == 2
    assert len(result) > 0


@pytest.mark.asyncio
async def test_403_short_circuits_remaining_subs(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(403)

    connector, client = _make_connector(handler)
    try:
        result = await connector.fetch()
    finally:
        await client.aclose()

    # First call returns 403 → loop aborts before second sub.
    assert call_count["n"] == 1
    assert result == []


@pytest.mark.asyncio
async def test_run_marks_success_with_partial_items_on_429(monkeypatch):
    """RedditRateLimited is caught inside fetch(); BaseConnector.run sees a normal return."""
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(200, content=_ok_response_body())
        return httpx.Response(429)

    connector, client = _make_connector(handler)

    async def _no_save(items):
        return len(items)

    connector.save = _no_save  # type: ignore[assignment]

    try:
        status = await connector.run()
    finally:
        await client.aclose()

    assert status.last_status == "ok"
    assert status.items_ingested > 0

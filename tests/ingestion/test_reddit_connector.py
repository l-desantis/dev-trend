import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.ingestion.base import ConnectorRunRegistry
from app.ingestion.reddit_connector import RedditConnector

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "reddit_new.atom.xml"


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _full_feed_bytes() -> bytes:
    return _FIXTURE.read_bytes()


def _feed_for_sub(sub: str) -> bytes:
    """Return the fixture filtered to entries whose link path includes /r/{sub}/."""
    raw = _FIXTURE.read_text()
    # Cheap split-on-entry approach so we don't pull in an XML editing dep.
    from xml.etree import ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}
    ET.register_namespace("", ns["a"])
    root = ET.fromstring(raw)
    for entry in list(root.findall("a:entry", ns)):
        link = entry.find("a:link", ns)
        href = link.get("href") if link is not None else ""
        if f"/r/{sub}/" not in href:
            root.remove(entry)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _make_connector(handler) -> tuple[RedditConnector, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    registry = ConnectorRunRegistry()
    connector = RedditConnector(client, registry)
    return connector, client


@pytest.mark.asyncio
async def test_hits_rss_endpoint_with_user_agent(monkeypatch):
    monkeypatch.setenv("REDDIT_USER_AGENT", "MyAgent/9.9")
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    seen_urls: list[str] = []
    seen_uas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        seen_uas.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, content=_feed_for_sub("startups"))

    connector, client = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_urls == ["https://www.reddit.com/r/startups/new.rss"]
    assert seen_uas == ["MyAgent/9.9"]


@pytest.mark.asyncio
async def test_normalize_round_trip(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_feed_for_sub("startups"))

    connector, client = _make_connector(handler)
    try:
        raw = await connector.fetch()
    finally:
        await client.aclose()

    items = connector.normalize(raw)
    assert len(items) == 1
    item = items[0]
    assert item.source_type == "reddit"
    assert item.external_id == "t3_abc001"
    assert item.title == "Launched my AI habit tracker with streak gamification"
    assert item.url == (
        "https://www.reddit.com/r/startups/comments/abc001/launched_my_ai_habit_tracker/"
    )
    assert item.created_at == datetime(2026, 4, 24, 12, 0, 0, tzinfo=UTC)
    assert item.metadata == {
        "subreddit": "startups",
        "ups": None,
        "num_comments": None,
        "author": "founder1",
    }
    assert item.role == "extraction"
    # Body is the HTML content (not stripped). Just sanity-check the marker.
    assert "habit tracker features" in (item.body or "")


@pytest.mark.asyncio
async def test_falls_back_to_link_for_id_when_atom_id_is_non_t3(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:reddit.com,2008:/r/startups/comments/zzz999/</id>
    <title>Edge case post</title>
    <link href="https://www.reddit.com/r/startups/comments/zzz999/edge_case/"/>
    <published>2026-04-24T15:00:00+00:00</published>
    <author><name>/u/edgeu</name></author>
    <category term="startups"/>
    <content type="html">&lt;p&gt;body&lt;/p&gt;</content>
  </entry>
</feed>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=feed)

    connector, client = _make_connector(handler)
    try:
        raw = await connector.fetch()
    finally:
        await client.aclose()

    items = connector.normalize(raw)
    assert len(items) == 1
    assert items[0].external_id == "t3_zzz999"


@pytest.mark.asyncio
async def test_max_subreddits_per_run_caps_loop(monkeypatch):
    monkeypatch.setenv(
        "REDDIT_SUBREDDITS", "startups,SideProject,Entrepreneur,reactnative"
    )
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    monkeypatch.setenv("REDDIT_MAX_SUBREDDITS_PER_RUN", "2")
    get_settings.cache_clear()

    seen_subs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        # path is /r/{sub}/new.rss
        seen_subs.append(parts[1])
        return httpx.Response(200, content=_feed_for_sub(parts[1]))

    connector, client = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_subs == ["startups", "SideProject"]


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

    monkeypatch.setattr(
        "app.ingestion.reddit_connector.asyncio.sleep", fake_sleep
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_full_feed_bytes())

    connector, client = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    # 3 subs, each successful → 3 inter-sub sleeps.
    assert sleeps == [1.5, 1.5, 1.5]


@pytest.mark.asyncio
async def test_graceful_skip_on_http_error(monkeypatch):
    """A non-2xx for one sub must not abort the whole run."""
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/r/startups/" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, content=_feed_for_sub("SideProject"))

    connector, client = _make_connector(handler)
    try:
        raw = await connector.fetch()
    finally:
        await client.aclose()

    items = connector.normalize(raw)
    ids = [it.external_id for it in items]
    assert ids == ["t3_abc002", "t3_abc003"]


@pytest.mark.asyncio
async def test_graceful_skip_on_malformed_xml(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/r/startups/" in request.url.path:
            return httpx.Response(200, content=b"<<not xml>>")
        return httpx.Response(200, content=_feed_for_sub("SideProject"))

    connector, client = _make_connector(handler)
    try:
        raw = await connector.fetch()
    finally:
        await client.aclose()

    items = connector.normalize(raw)
    ids = [it.external_id for it in items]
    assert ids == ["t3_abc002", "t3_abc003"]


@pytest.mark.asyncio
async def test_since_filter_drops_older_entries(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_feed_for_sub("SideProject"))

    connector, client = _make_connector(handler)
    try:
        # Fixture has SideProject entries at 13:00 and 14:00 UTC on 2026-04-24.
        # since=13:30 → only the 14:00 entry survives.
        since = datetime(2026, 4, 24, 13, 30, 0, tzinfo=UTC)
        raw = await connector.fetch(since=since)
    finally:
        await client.aclose()

    items = connector.normalize(raw)
    ids = [it.external_id for it in items]
    assert ids == ["t3_abc003"]


@pytest.mark.asyncio
async def test_run_marks_success_when_one_sub_fails(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/r/startups/" in request.url.path:
            return httpx.Response(429)
        return httpx.Response(200, content=_feed_for_sub("SideProject"))

    connector, client = _make_connector(handler)

    async def _no_save(items):
        return len(items)

    connector.save = _no_save  # type: ignore[assignment]

    try:
        status = await connector.run()
    finally:
        await client.aclose()

    assert status.last_status == "ok"
    assert status.items_ingested == 2

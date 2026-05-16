# Reddit connector — migrate from public JSON API to Reddit RSS

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `https://www.reddit.com/r/{sub}/new.json` with `https://www.reddit.com/r/{sub}/new.rss` inside `RedditConnector` so DevTrend keeps ingesting Reddit submissions from a datacenter IP. The JSON endpoint is blocked at the TLS-fingerprint layer by Reddit's WAF; the RSS endpoint sits on a different code path and returns 200 to the same `httpx.AsyncClient` from the same IP. `normalize()` keeps producing the same `SourceItem` shape minus two unused metadata fields (`ups`, `num_comments`), which Reddit's RSS does not expose and which nothing downstream reads.

**Architecture:** `fetch()` loops over `settings.reddit_subreddits` (optionally capped), GETs each sub's Atom feed (`/new.rss`), parses entries with stdlib `xml.etree.ElementTree`, translates each entry into the legacy nested `{"data": {…}}` "child" dict so `normalize()` stays virtually unchanged, and on **any** per-sub failure (HTTP error, malformed XML, ParseError) logs a structured warning and continues with the next sub. The `since` parameter is honored as a `created_utc > since` filter on parsed entries (RSS returns ~25 newest items per sub, no pagination — backfill ≥ a few hours back is not supported and is dropped from the connector's contract). `RedditRateLimited` and the 429/403 short-circuit are removed; Reddit's RSS endpoint has never returned 403 in our testing and the per-sub graceful skip subsumes the abort behavior.

**Tech Stack:** `httpx.AsyncClient`, `xml.etree.ElementTree` (stdlib), `app/ingestion/http_utils.py:request_with_retry`, `app/ingestion/base.py:ConnectorRunRegistry`, `structlog`, `pydantic-settings`, `pytest` + `httpx.MockTransport` (existing project convention).

---

## Context

`app/ingestion/reddit_connector.py` currently calls `https://www.reddit.com/r/{sub}/new.json`. As of 2026-05-15 that endpoint:

- Returns **403 with an HTML interstitial body** from our Hetzner VPS, even with a benign `devtrend/1.0` User-Agent and the same IP that successfully `curl`s the same URL.
- The 403 is triggered by **TLS ClientHello fingerprinting (JA3/JA4)**, not by IP, UA, or HTTP headers. Reddit's WAF (Fastly + their internal Bot Mitigation Action — visible as `server-timing: reddit-ct;desc="dn=FT,p=BMA,cs=MISS"` in the response) recognizes Python's stdlib `ssl` fingerprint and gates the JSON API before HTTP is even consulted.
- The same `httpx.AsyncClient`, same IP, same UA, hitting `…/new.rss` instead returns **200** with a well-formed Atom feed. We confirmed this with the `probe_reddit.py` script in the repo root on 2026-05-15:
  ```
  www.json   403  https://www.reddit.com/r/startups/new.json
  www.rss    200  https://www.reddit.com/r/startups/new.rss
  old.json   403  https://old.reddit.com/r/startups/new.json
  old.rss    200  https://old.reddit.com/r/startups/new.rss
  np.json    403  https://np.reddit.com/r/startups/new.json
  ```

Reddit publishes RSS as a documented public endpoint with no auth requirement. It has been frozen-stable for 15+ years.

### Atom contract returned by `/new.rss`

Each subreddit responds with an Atom 1.0 feed (`xmlns="http://www.w3.org/2005/Atom"`). Per-entry shape:

| XPath (in default `a:` namespace) | Maps to |
|---|---|
| `a:id` | The Reddit **fullname**, e.g. `t3_abc001`, byte-identical to today's `d["name"]` from the JSON API. |
| `a:title` | Post title. |
| `a:published` | ISO 8601 timestamp → `created_utc`. |
| `a:updated` | ISO 8601 timestamp; ignored (same as `published` for new submissions). |
| `a:content[@type='html']` | HTML-encoded selftext (text wrapped in `<!-- SC_OFF --><div class="md">…</div><!-- SC_ON -->`). |
| `a:link/@href` | Absolute URL to the post (`https://www.reddit.com/r/{sub}/comments/{id}/{slug}/`). |
| `a:category/@term` | Subreddit name. |
| `a:author/a:name` | `/u/username` — must strip the `/u/` prefix. |
| _not exposed_ | `ups`, `num_comments`, `score`. |

### Metadata fields we lose, and why it's safe

The current connector writes `ups` and `num_comments` into `metadata_json`. A grep of the whole codebase (`app`, `tests`, `scripts`) confirms **nothing reads them**: scoring (`app/scoring/dimensions.py:23`) only reads `metadata.subreddit`; everywhere else, those keys are dead. We keep them in the output dict with `None` values to preserve the metadata key set, so any downstream code that grows to read them later sees a stable shape.

### Body content: HTML vs markdown

The JSON API returns plain markdown in `selftext`. The RSS feed returns HTML-encoded rendered content. The downstream pipeline (`app/pipeline/extract.py` → LLM extraction) consumes `SourceItem.body` as opaque text — it does not parse markdown specifically, and HTML is fine input for the extraction LLM. **No pipeline changes are required**, but expect a tiny token-count bump per item (a few % from the HTML wrapping).

### Behaviors removed by this migration

- `RedditRateLimited` exception class.
- `_REDDIT_BACKOFF_STATUSES = {429, 403}` and the 429/403 short-circuit. Replaced by **per-sub graceful skip** on any exception.
- `_fetch_sub_backfill` — RSS has no pagination, only the ~25 newest items. Backfill of >~25 items per sub is no longer possible through this connector. (See "Out of scope".)
- The `headers={"User-Agent": settings.reddit_user_agent}` send. We still send a UA (for politeness / log identification on Reddit's end), but the `reddit_user_agent` setting is retained so operators can tune it without code changes.

### Settings hygiene — deferred

`reddit_client_id` and `reddit_client_secret` (lines 63-64 of `app/config.py`) remain unused after this migration too. They were always speculative — for an OAuth path we never shipped. Removing them is a sensible follow-up but **out of scope here** to keep this PR's blast radius minimal. They have zero behavioral cost.

## Files to modify

| File | Change |
|---|---|
| `app/ingestion/reddit_connector.py` | Full rewrite of `fetch()`. Drop `RedditRateLimited`, `_REDDIT_BACKOFF_STATUSES`, `_fetch_sub_latest`, `_fetch_sub_backfill`. Add `_entry_to_child`, `_extract_name`, `_fetch_sub`. Keep `normalize()` nearly unchanged (drop `ups`/`num_comments` to `None`). |
| `tests/ingestion/test_reddit_connector.py` | Rewrite end-to-end. Replace fixture, replace tests with RSS-shaped scenarios (round-trip, per-sub graceful skip, sub cap, delay, since filter). Use `httpx.MockTransport`. |
| `tests/fixtures/reddit_new.atom.xml` *(new)* | Small Atom feed fixture covering 3 entries across 2 subreddits (startups, SideProject). |
| `tests/fixtures/reddit_new.json` | **Delete** — no longer referenced after the test rewrite. |
| `.env.example` | Rewrite the comment block above `REDDIT_DELAY_SECONDS` to reflect RSS (no 10 req/min ceiling, no WAF concern). No new env vars. |

## Design

### 1. Parser helpers (`app/ingestion/reddit_connector.py`)

Stdlib XML parsing with a single namespace map; no third-party dependency.

```python
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_PERMALINK_ID_RE = re.compile(r"/comments/([a-z0-9]+)/", re.IGNORECASE)


def _extract_name(entry: ET.Element) -> str | None:
    """Return the Reddit fullname (t3_xxxxx) for an Atom entry.

    Prefers the entry's <id> if it already looks like a t3_ fullname.
    Otherwise derives the post id from the entry's <link href> URL —
    Reddit historically used the t3_ form, but `<id>` shape has drifted
    once or twice in the past and the link URL is the durable signal.
    """
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
    """Adapt one Atom <entry> to the legacy Reddit listing 'child' shape.

    Returns None for entries with no derivable fullname (which would make
    `external_id` unstable).
    """
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
```

### 2. `RedditConnector` rewrite

```python
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
        resp.raise_for_status()  # non-2xx → caller's try/except logs and skips

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
                    "ups": None,            # not exposed by Reddit RSS
                    "num_comments": None,   # not exposed by Reddit RSS
                    "author": d.get("author"),
                },
                role="extraction",
            ))
        return items
```

Removed relative to today:

- `_REDDIT_BACKOFF_STATUSES = {429, 403}`
- `class RedditRateLimited(Exception): ...`
- `_fetch_sub_latest`, `_fetch_sub_backfill`
- The 429/403 short-circuit and the `else_run break` flow

### 3. `.env.example` — Reddit block rewrite

Replace lines 52-65 (the current Reddit block) with:

```
# Reddit ingestion (via the public RSS feed at /r/{sub}/new.rss)
# Reddit's JSON API blocks our TLS fingerprint from datacenter IPs; RSS is
# served from a different code path and works fine. The UA below is mostly
# courtesy — RSS doesn't gate on it.
REDDIT_USER_AGENT=devtrend/1.0
# Speculative OAuth credentials; not currently consumed by the connector.
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_SUBREDDITS=startups,SideProject,Entrepreneur,reactnative,androiddev,iOSProgramming,AppIdeas
# Seconds between Reddit HTTP calls (between subs). RSS has no documented
# rate limit, but 6.0s keeps us polite. Can be lowered to ~1.0s safely.
REDDIT_DELAY_SECONDS=6.0
# How often the reddit ingestion job fires (hours).
# NOTE: Reddit RSS returns only the ~25 newest items per sub and supports no
# pagination. At 12h cadence, a subreddit posting >25 items in 12h will drop
# the older posts in that window. If that happens to a sub you care about,
# either lower this interval or move that sub off the RSS path.
REDDIT_CRON_INTERVAL_HOURS=12
# Optional cap on subreddits per run. Leave empty for "all configured subs".
REDDIT_MAX_SUBREDDITS_PER_RUN=
```

### 4. Test fixture (`tests/fixtures/reddit_new.atom.xml`)

A minimal Atom feed with 3 entries spanning 2 subreddits. Subreddit names embedded in the `<category term>` attribute and the `<link href>` paths so the per-sub test helper can filter.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-US">
  <category term="startups" label="r/startups"/>
  <updated>2026-04-24T12:30:00+00:00</updated>
  <id>/r/startups/new/.rss</id>
  <link rel="self" href="https://www.reddit.com/r/startups/new/.rss" type="application/atom+xml"/>
  <link rel="alternate" href="https://www.reddit.com/r/startups/new" type="text/html"/>
  <title>newest submissions : startups</title>
  <entry>
    <author>
      <name>/u/founder1</name>
      <uri>https://www.reddit.com/user/founder1</uri>
    </author>
    <category term="startups" label="r/startups"/>
    <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;Built an ai wellness coaching app with habit tracker features. Looking for feedback.&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt;</content>
    <id>t3_abc001</id>
    <link href="https://www.reddit.com/r/startups/comments/abc001/launched_my_ai_habit_tracker/"/>
    <updated>2026-04-24T12:00:00+00:00</updated>
    <published>2026-04-24T12:00:00+00:00</published>
    <title>Launched my AI habit tracker with streak gamification</title>
  </entry>
  <entry>
    <author>
      <name>/u/hacker42</name>
      <uri>https://www.reddit.com/user/hacker42</uri>
    </author>
    <category term="SideProject" label="r/SideProject"/>
    <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;Running local llm and edge ai app on my phone. Performance is surprisingly good.&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt;</content>
    <id>t3_abc002</id>
    <link href="https://www.reddit.com/r/SideProject/comments/abc002/local_llm_running/"/>
    <updated>2026-04-24T13:00:00+00:00</updated>
    <published>2026-04-24T13:00:00+00:00</published>
    <title>Local LLM running on-device — my experience with private ai</title>
  </entry>
  <entry>
    <author>
      <name>/u/weekender</name>
      <uri>https://www.reddit.com/user/weekender</uri>
    </author>
    <category term="SideProject" label="r/SideProject"/>
    <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;Just a tiny mood tracker I built over the weekend.&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt;</content>
    <id>t3_abc003</id>
    <link href="https://www.reddit.com/r/SideProject/comments/abc003/minimalist_mood_tracker/"/>
    <updated>2026-04-24T14:00:00+00:00</updated>
    <published>2026-04-24T14:00:00+00:00</published>
    <title>Side project: minimalist mood tracker</title>
  </entry>
</feed>
```

### Testing approach

Same convention as every other connector test in this repo: `httpx.MockTransport`. The handler routes by URL path (`/r/{sub}/new.rss`) and returns either a filtered Atom feed or an error response.

## Tasks

Per `CLAUDE.md`: every command that invokes `uv` / `python` / `pytest` must be **requested from the user** ("please run …") and the output pasted back into the conversation. The plan executor never runs those directly.

### Task 1 — Add the Atom RSS fixture

**Files:**
- Create: `tests/fixtures/reddit_new.atom.xml`

- [ ] **Step 1: Create the fixture file**

Write the exact XML content shown in **Design §4** to `tests/fixtures/reddit_new.atom.xml`.

- [ ] **Step 2: Sanity-check it parses with stdlib**

Ask the user to run:

```
uv run python -c "from xml.etree import ElementTree as ET; r = ET.parse('tests/fixtures/reddit_new.atom.xml').getroot(); ns={'a':'http://www.w3.org/2005/Atom'}; print(len(r.findall('a:entry', ns)))"
```

Expected output: `3`.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/reddit_new.atom.xml
git commit -m "test(reddit): add Atom RSS fixture"
```

### Task 2 — Rewrite the Reddit connector test suite (red)

> We deliberately write the new tests **before** rewriting the connector so the suite drives implementation (TDD). The existing tests against `RedditRateLimited` / Reddit JSON shape are replaced wholesale.

**Files:**
- Rewrite: `tests/ingestion/test_reddit_connector.py`
- Delete (in Task 4): `tests/fixtures/reddit_new.json`

- [ ] **Step 1: Replace the test file contents**

Overwrite `tests/ingestion/test_reddit_connector.py` with:

```python
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
```

- [ ] **Step 2: Run the new test file to verify every test fails**

Ask the user to run:

```
uv run pytest tests/ingestion/test_reddit_connector.py -v
```

Expected: collection error or every test FAILS. The most likely failure is an `ImportError` on `from app.ingestion.reddit_connector import RedditConnector` not failing, but the tests themselves break because the connector still hits `.json` and the mock transport now expects `.rss`.

- [ ] **Step 3: Commit (red)**

```bash
git add tests/ingestion/test_reddit_connector.py
git commit -m "test(reddit): rewrite suite for RSS adapter (red)"
```

> Committing red here is a deliberate handoff point. The next task makes the suite green.

### Task 3 — Rewrite `RedditConnector` to use RSS

**Files:**
- Rewrite: `app/ingestion/reddit_connector.py` (full file replacement)

- [ ] **Step 1: Apply the rewrite**

Replace `app/ingestion/reddit_connector.py` in full with:

```python
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
```

- [ ] **Step 2: Run the Reddit suite**

Ask the user to run:

```
uv run pytest tests/ingestion/test_reddit_connector.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 3: Sweep the rest of the suite for fallout**

Ask the user to run:

```
uv run pytest -v
```

Expected: green. The known risk is residual imports of `RedditRateLimited` — verify with:

```
grep -rn "RedditRateLimited" app tests scripts
```

If any remain (outside the deleted test file), fix them in this task — do not defer.

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/reddit_connector.py
git commit -m "$(cat <<'EOF'
feat(reddit): switch connector from JSON API to RSS feed

Reddit's WAF blocks Python's TLS fingerprint on the JSON endpoint
(403 + bot-mitigation interstitial) from datacenter IPs, even when
the same UA and IP curl successfully. The /new.rss endpoint sits
on a different code path and returns 200.

- fetch() now GETs /r/{sub}/new.rss and parses Atom 1.0 with stdlib
  xml.etree.ElementTree (no new dependencies).
- Per-sub graceful degradation: any HTTP error or XML parse failure
  logs a structured warning and continues with the next sub.
- Drops RedditRateLimited and the 429/403 short-circuit (subsumed by
  the per-sub skip; RSS does not exhibit those failure modes).
- Drops _fetch_sub_backfill: RSS has no pagination beyond ~25 newest
  items. The fetch() since= parameter is honored as a created_utc
  filter on the parsed page.
- normalize() still emits the same SourceItem shape; metadata loses
  ups/num_comments (set to None — both fields were unused downstream).
EOF
)"
```

### Task 4 — Cleanup

**Files:**
- Delete: `tests/fixtures/reddit_new.json`
- Modify: `.env.example` lines 52-65 (Reddit block)

- [ ] **Step 1: Delete the stale JSON fixture**

```bash
git rm tests/fixtures/reddit_new.json
```

- [ ] **Step 2: Update `.env.example`**

Replace lines 52-65 with the block shown in **Design §3**. The set of `REDDIT_*` keys stays the same; only the surrounding comments change.

- [ ] **Step 3: Verify `.env.example` parses for the existing config test**

Ask the user to run:

```
uv run pytest tests/test_config.py -v
```

Expected: all tests in `tests/test_config.py` PASS. None of them reference the Reddit-block comments, but this catches accidental key removal.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/reddit_new.json .env.example
git commit -m "chore(reddit): drop legacy JSON fixture; refresh env example"
```

### Task 5 — Final sweep + lint

- [ ] **Step 1: Run lint / type-check**

Ask the user to run whichever of these the repo uses (check `pyproject.toml` / `.pre-commit-config.yaml`):

```
uv run ruff check app tests
uv run mypy app
```

Most likely lint hits: unused imports left over from the rewrite (none anticipated, but verify). Fix anything that lights up.

- [ ] **Step 2: Final commit (if anything was fixed)**

```bash
git add <touched files>
git commit -m "chore: lint cleanup after RSS migration"
```

If nothing changed, skip the commit.

## Verification

End-to-end checks the user can run on the VPS (or a local dev shell with the prod-like `.env`).

> All commands assume `docker compose` because that matches the deployment shape. If running outside Docker, swap `docker compose exec app` for `uv run`.

### 1. RSS reaches the connector from the VPS

```
docker compose exec app sh -c "wget -qO- 'https://www.reddit.com/r/startups/new.rss' | head -c 400"
```

Expected: an XML blob starting with `<?xml version="1.0" encoding="UTF-8"?>` and containing `<feed xmlns="http://www.w3.org/2005/Atom"`. If this returns HTML or a 403, Reddit has begun gating RSS too — fall back to PullPush per the parallel plan.

### 2. Real ingestion run against the dev DB

```
docker compose exec -e REDDIT_MAX_SUBREDDITS_PER_RUN=2 -e REDDIT_DELAY_SECONDS=1 \
  app python -m scripts.run_job --job reddit_ingestion
```

Expected log lines (structlog, in order):

```
Reddit RSS sub fetch complete  subreddit=startups   items=<N>
Reddit RSS sub fetch complete  subreddit=SideProject items=<M>
Ingestion complete  component=RedditConnector  source_type=reddit  status=ok  items_ingested=<N+M>
```

Then confirm rows landed in `source_items`:

```
docker compose exec postgres psql -U devtrend -d devtrend -c \
  "select count(*), max(created_at) from source_items where source_type='reddit';"
```

`count` should match the sum of `items_ingested` from the previous step (within dedup tolerance). `max(created_at)` should be recent.

### 3. Second run is idempotent

Run step 2 again immediately. Expected: `items_ingested=0` (dedup via `(source_type, external_id)` unique index).

### 4. Graceful per-sub degradation

Force a connection failure by pointing the connector at a closed proxy:

```
docker compose exec \
  -e REDDIT_MAX_SUBREDDITS_PER_RUN=2 \
  -e REDDIT_DELAY_SECONDS=0 \
  -e HTTPS_PROXY=http://127.0.0.1:1 \
  app python -m scripts.run_job --job reddit_ingestion
```

Expected:
- Two `Reddit RSS fetch failed — skipping subreddit … error_type=ConnectError` warning lines.
- One `Ingestion complete … status=ok items_ingested=0` line.
- Process exit code 0.

Unset `HTTPS_PROXY` afterwards and re-run step 2 to confirm recovery.

### 5. Body-quality smoke test (HTML vs markdown)

After step 2 has ingested some real RSS-sourced items, run one mocked-LLM extraction pass and eyeball a few `PainPointDraft` rows to make sure HTML-wrapped bodies don't visibly hurt extraction quality:

```
docker compose exec -e LLM_PROVIDER=mock -e EMBEDDING_PROVIDER=mock \
  app python -m scripts.run_job --job daily_pipeline
```

Then:

```
docker compose exec postgres psql -U devtrend -d devtrend -c \
  "select pp.id, pp.summary, si.title from pain_points pp \
   join source_items si on si.id = pp.source_item_id \
   where si.source_type='reddit' order by pp.id desc limit 5;"
```

Expected: `summary` reads as plain prose, not as raw `<p>...</p>` markup. If it does contain markup, the extraction prompts may need a small HTML-aware tweak — flag it for a follow-up but do not block this PR (extraction quality regression, not a correctness regression).

### 6. Cap=0 sanity (no HTTP calls)

```
docker compose exec -e REDDIT_MAX_SUBREDDITS_PER_RUN=0 \
  app python -m scripts.run_job --job reddit_ingestion
```

Expected: `items_ingested=0`, zero `Reddit RSS sub fetch complete` lines, exit 0.

## Out of scope

- **Backfill of >25 items per sub.** RSS returns only ~25 newest entries with no pagination. Today's `_fetch_sub_backfill` (up to 1000 items via `after=` cursors) is dropped. Steady-state ingestion at the 12h cron cadence has enormous headroom (≈25 items × 7 subs × 2 polls/day = 350 items/day capacity) so this is not a regression for normal operation. If a future spike makes one sub flood >25 items in 12h, either cut the cron interval or move that sub to PullPush.
- **Reddit OAuth.** Was never used (`reddit_client_id`/`reddit_client_secret` are empty and unread). Settings remain in `config.py` for now; their removal is a separate cleanup.
- **Removing the `reddit_user_agent` setting.** We still send a UA on every RSS request (it's polite and identifies us in Reddit's logs even though the WAF doesn't gate on it). The setting stays.
- **HTML → markdown body conversion.** RSS gives us HTML in `<content>`; the downstream LLM extraction handles either. A future pipeline pass can canonicalise to markdown if extraction quality dips.
- **Switching tests to `pytest-httpx`.** Project convention is `httpx.MockTransport`. Stays.
- **Migrating other connectors to per-sub graceful skip.** The pattern is good but out of scope here.
- **RSS-availability canary.** Reddit could begin gating RSS too (no signal of this today, but the 2025 policy crackdown makes it non-zero). Adding a recurring health-check that probes `/new.rss` and warns on a non-200 / HTML body is a sensible follow-up but is not part of this connector migration.
- **Refactor of `test_run_marks_success_when_one_sub_fails`.** That test mocks `connector.save` directly to avoid a DB dependency. It works today but is slightly coupled to internals; a future pass can route it through a real (in-memory or testcontainer) DB.

# Reddit connector — migrate from public JSON API to PullPush.io

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the data source behind `RedditConnector` with [PullPush.io](https://api.pullpush.io/) — an unauthenticated Reddit mirror with a 1,000 req/hour budget — so DevTrend keeps ingesting Reddit submissions from a datacenter IP without being blocked by Reddit's WAF. `normalize()` and the `SourceItem` shape stay exactly as they are; only `fetch()` and a handful of settings change.

**Architecture:** PullPush exposes `GET https://api.pullpush.io/reddit/search/submission/` with flat-JSON responses (`{"data": [ {…submission…}, … ]}`). Inside `fetch()` we (1) loop over `settings.reddit_subreddits` (optionally capped), (2) paginate each subreddit with `after`/`before` cursors until either the per-sub item cap (`REDDIT_MAX_POSTS_PER_SUB`) is reached or no more posts are returned, (3) translate each PullPush submission into Reddit's nested `{"data": {…}}` child shape so the existing `normalize()` method works unchanged, and (4) on **any** PullPush failure (HTTP error, timeout, malformed JSON) we log a structured warning, skip that subreddit, and continue. Incremental fetch uses `ConnectorRunRegistry.get(source_type).last_run_at` as the `after` cursor; a first run with no prior timestamp falls back to `now - REDDIT_CRON_INTERVAL_HOURS` so we don't sweep up the whole subreddit on cold start.

**Tech Stack:** `httpx.AsyncClient`, `app/ingestion/http_utils.py:request_with_retry`, `app/ingestion/base.py:ConnectorRunRegistry`, `structlog`, `pydantic-settings`, `pytest` + `httpx.MockTransport` (existing project convention — see "Testing approach" below).

---

## Context

`app/ingestion/reddit_connector.py` currently calls `https://www.reddit.com/r/{sub}/new.json`. That endpoint:

- Imposes an unauthenticated 10 req/min cap globally on the IP and, in practice, returns 403 from datacenter IPs even when well under the cap (Reddit's WAF identifies the AS).
- Returns Reddit's classic listing shape: `{"data": {"after": "t3_…", "children": [ {"kind": "t3", "data": {…}} ]}}`.
- Has been guarded with a global 6s inter-request delay + 429/403 short-circuit (`RedditRateLimited` exception). That logic was added in commit `e015e44` / `840590e`; it stops the bleeding but doesn't fix the root problem — Reddit's WAF blocks datacenter IPs regardless.

**PullPush.io** is a community Reddit mirror. Key contract:

| Aspect | Value |
|---|---|
| Base URL | `https://api.pullpush.io/reddit/search/submission/` |
| Auth | None |
| Rate limit | 1,000 req/hour per IP (~16.6 req/min). We will run ≪ this. |
| `subreddit` param | Single subreddit name, e.g. `subreddit=startups` |
| `size` param | Items per page, default 25, max 100 |
| `sort` / `order` | `sort=created_utc&order=desc` for newest-first |
| `after` param | Unix timestamp, **exclusive lower bound** on `created_utc` |
| `before` param | Unix timestamp, **exclusive upper bound** on `created_utc` |
| Response shape | `{"data": [ {"id": "abc001", "title": "...", "selftext": "...", "permalink": "/r/.../comments/...", "subreddit": "...", "created_utc": 1745500000, "score": 42, "num_comments": 7, "author": "..."}, ... ], "metadata": {...}}` |

Notes:

- PullPush's `id` is the **bare** Reddit submission id (e.g. `abc001`). Reddit's listing API exposes the same submission as `name = "t3_abc001"`. Our existing `normalize()` reads `d["name"]` as the `external_id`. To keep `external_id` byte-identical to historical rows (so the `(source_type, external_id)` unique index dedupes correctly against existing data), the adapter **must prefix** `t3_` before handing the row to `normalize()`.
- PullPush returns `score`, not `ups`. The current `normalize()` reads `d.get("ups")`. To preserve metadata parity we map PullPush `score` → adapted `ups` (the field is a best-effort upvote count in both APIs; keeping the same key avoids a `metadata_json` schema drift).
- `permalink` shape matches Reddit's (leading slash, no host), so the existing URL construction `f"https://www.reddit.com{d['permalink']}"` works unchanged.
- `selftext` and `author` map 1:1.
- PullPush has no concept of pagination tokens beyond `after`/`before` on `created_utc`. The pattern is: keep `after` fixed at the run-start cursor, advance `before` to the oldest `created_utc` seen in the previous page until a short page (or empty page) is returned.

**Incremental fetch — registry vs. naive window.** Today's HN connector uses a hard-coded 6h lookback when called with `since=None`. The spec asks for something better for Reddit: persist progress via `ConnectorRunRegistry.last_run_at`, which is set on `mark_success` at the end of every run. Caveats:

- The registry is **in-memory only** (`ConnectorRunRegistry._statuses: dict[…]`). It resets on process restart. We therefore need a fallback for the first run after a fresh process: use `now - REDDIT_CRON_INTERVAL_HOURS` so a cold restart re-fetches at most the last `cron_interval` window.
- `mark_running` does **not** clear `last_run_at`. Reading the field at the start of `fetch()` is safe.
- The cursor is "wall-clock at end of previous run", not "newest item ingested". A run that completes at `T` and the next run that starts at `T + interval` will use `T` as the `after` cursor → fetches items with `created_utc > T`. That's exactly what we want.

**Settings hygiene.** During the rate-limiting work we kept three Reddit-OAuth-anticipation settings in case we ever registered an app:

- `reddit_client_id` — never read anywhere outside `config.py`.
- `reddit_client_secret` — never read anywhere outside `config.py`.
- `reddit_user_agent` — read only by `RedditConnector.fetch()` to build the `User-Agent` header for the public JSON API. PullPush does not care about the UA. With this migration both the variable and the reads disappear.

These three should be removed from `app/config.py` and `.env.example` per spec requirement #7.

## Files to modify

| File | Change |
|---|---|
| `app/config.py` | Remove `reddit_client_id`, `reddit_client_secret`, `reddit_user_agent`. Add `reddit_max_posts_per_sub: int = 100`. Keep `reddit_subreddits`, `reddit_delay_seconds`, `reddit_cron_interval_hours`, `reddit_max_subreddits_per_run` as-is. |
| `app/ingestion/reddit_connector.py` | Full rewrite of `fetch()` and its helpers around PullPush. Drop `RedditRateLimited` and `_REDDIT_BACKOFF_STATUSES` (no longer applicable). Keep `normalize()` byte-identical to today (lines 123-146). |
| `.env.example` | Replace the Reddit block (currently lines 52-65). Drop UA/OAuth lines. Add `REDDIT_MAX_POSTS_PER_SUB` with rationale. Re-document `REDDIT_DELAY_SECONDS` now that the 10 RPM ceiling is gone. |
| `tests/ingestion/test_reddit_connector.py` | Rewrite end-to-end. Replace fixture, replace tests with PullPush-shaped scenarios (pagination, incremental cursor, graceful skip, sub cap, normalize parity). Use `httpx.MockTransport` to match the existing test style. |
| `tests/fixtures/pullpush_submissions.json` *(new)* | A small fixture in PullPush response shape with 3 submissions across 2 subreddits. |
| `tests/fixtures/reddit_new.json` | **Delete** — no longer referenced after the test rewrite. |
| `tests/test_config.py` | Update the existing reddit defaults tests to drop UA/OAuth assertions and add `reddit_max_posts_per_sub` defaults. |
| `tests/test_scheduler.py` | No change expected — search to confirm; if anything imports `reddit_user_agent` or asserts on it, adjust. |

## Design

### 1. Settings diff (`app/config.py`)

**Remove** (currently lines 63-65):

```python
reddit_client_id: str = ""
reddit_client_secret: str = ""
reddit_user_agent: str = "devtrend/1.0"
```

**Add** to the existing Reddit block (after `reddit_max_subreddits_per_run`, currently line 77):

```python
# PullPush pagination: max submissions to fetch per subreddit per run.
# PullPush page size is capped at 100 per request, so this == max_pages * 100.
# Default 100 = 1 request per subreddit per run (~7 requests total).
reddit_max_posts_per_sub: int = 100
```

**Keep unchanged:**

```python
reddit_subreddits: list[str] = [ "startups", "SideProject", ... ]
reddit_delay_seconds: float = 6.0           # inter-request spacing (purpose shifts — see §3 below)
reddit_cron_interval_hours: int = 12
reddit_max_subreddits_per_run: int | None = None
```

> The `reddit_delay_seconds` default of 6.0s is now over-conservative (PullPush allows 16.6 req/min, not 10). Leaving it at 6.0s keeps total request rate well under PullPush's budget *and* gives a courtesy gap between subs. The user can lower it (e.g. `1.0`) safely; we deliberately do not change the default in this plan to keep blast radius small. A separate follow-up can tune it.

### 2. PullPush → Reddit-child adapter

A small private function inside `reddit_connector.py` that converts one PullPush submission into the nested shape `normalize()` expects:

```python
def _pullpush_to_child(post: dict) -> dict | None:
    """Adapt a PullPush submission dict to Reddit's listing-child shape.

    Returns None for posts missing the bare id (which would make `external_id`
    unstable). `normalize()` already skips items where `d.get("name")` is falsy
    so this is also handled downstream, but we filter early to keep counts honest.
    """
    pid = post.get("id")
    if not pid:
        return None
    return {
        "data": {
            "name": f"t3_{pid}",
            "title": post.get("title"),
            "selftext": post.get("selftext") or "",
            "permalink": post.get("permalink"),
            "subreddit": post.get("subreddit"),
            "ups": post.get("score"),  # PullPush exposes score; normalize() reads "ups"
            "num_comments": post.get("num_comments"),
            "author": post.get("author"),
            "created_utc": post.get("created_utc"),
        }
    }
```

This is the **only** translation step. `normalize()` then runs over `list[child]` exactly as today.

### 3. `RedditConnector.fetch()` rewrite

```python
import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem

_PULLPUSH_BASE = "https://api.pullpush.io/reddit/search/submission/"
_PAGE_SIZE = 100  # PullPush max


class RedditConnector(BaseConnector):
    source_type = "reddit"

    async def fetch(self, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        settings = get_settings()
        delay = settings.reddit_delay_seconds
        max_posts = settings.reddit_max_posts_per_sub
        subs = settings.reddit_subreddits
        cap = settings.reddit_max_subreddits_per_run
        if cap is not None and cap >= 0:
            subs = subs[:cap]

        # Determine the lower-bound cursor for this run.
        if since is None:
            last_run = self.registry.get(self.source_type).last_run_at
            if last_run is not None:
                since = last_run
            else:
                since = datetime.now(UTC) - timedelta(hours=settings.reddit_cron_interval_hours)

        after_ts = int(since.timestamp())
        before_ts: int | None = int(until.timestamp()) if until is not None else None

        children: list[dict] = []
        for sub in subs:
            try:
                sub_children = await self._fetch_sub(sub, after_ts, before_ts, max_posts, delay)
            except Exception as exc:  # noqa: BLE001 — graceful degradation per spec
                self.log.warning(
                    "PullPush fetch failed — skipping subreddit",
                    subreddit=sub,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                # Still honor the inter-sub delay so a flood of failures can't hammer the API.
                await asyncio.sleep(delay)
                continue

            children.extend(sub_children)
            await asyncio.sleep(delay)

        return children

    async def _fetch_sub(
        self,
        sub: str,
        after_ts: int,
        before_ts: int | None,
        max_posts: int,
        delay: float,
    ) -> list[dict]:
        collected: list[dict] = []
        # PullPush sorts desc by created_utc, so we walk the time window from
        # newest to oldest; `before` advances down to the oldest seen so far.
        page_before = before_ts

        while len(collected) < max_posts:
            page_size = min(_PAGE_SIZE, max_posts - len(collected))
            params = {
                "subreddit": sub,
                "size": page_size,
                "sort": "created_utc",
                "order": "desc",
                "after": after_ts,
            }
            if page_before is not None:
                params["before"] = page_before

            resp = await self._request_with_retry("GET", _PULLPUSH_BASE, params=params)
            payload = resp.json()
            posts = payload.get("data", [])
            if not posts:
                break

            oldest_ts: int | None = None
            for post in posts:
                child = _pullpush_to_child(post)
                if child is None:
                    continue
                collected.append(child)
                created = post.get("created_utc")
                if isinstance(created, (int, float)):
                    oldest_ts = int(created) if oldest_ts is None else min(oldest_ts, int(created))

            # Short page → no more results in the window.
            if len(posts) < page_size:
                break
            # No usable timestamp to advance the cursor → stop to avoid an infinite loop.
            if oldest_ts is None:
                break
            # Advance the upper bound. PullPush `before` is exclusive, so the
            # next page picks up strictly older posts than oldest_ts.
            page_before = oldest_ts

            await asyncio.sleep(delay)

        self.log.info(
            "PullPush sub fetch complete",
            subreddit=sub,
            items=len(collected),
            after_ts=after_ts,
            before_ts=before_ts,
        )
        return collected

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        # ── UNCHANGED — copy verbatim from current implementation, lines 123-146 ──
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
                url=f"https://www.reddit.com{d['permalink']}" if d.get("permalink") else None,
                created_at=datetime.fromtimestamp(created, tz=UTC) if created else None,
                metadata={
                    "subreddit": d.get("subreddit"),
                    "ups": d.get("ups"),
                    "num_comments": d.get("num_comments"),
                    "author": d.get("author"),
                },
                role="extraction",
            ))
        return items
```

Removed in this rewrite (relative to today):

- `_REDDIT_BACKOFF_STATUSES = {429, 403}`
- `class RedditRateLimited(Exception): ...`
- `_fetch_sub_latest`, `_fetch_sub_backfill` (replaced by unified `_fetch_sub` driven by `since`/`until`).
- `headers = {"User-Agent": ...}` and the reads of `settings.reddit_user_agent`.

### 4. `.env.example` block

Replace lines 52-65 with:

```
# Reddit ingestion via PullPush.io
# PullPush is a community Reddit mirror (no auth, 1000 req/hour). We do not
# need a User-Agent or OAuth credentials — those settings have been removed.
REDDIT_SUBREDDITS=startups,SideProject,Entrepreneur,reactnative,androiddev,iOSProgramming,AppIdeas
# Seconds between PullPush HTTP calls (both between subs and between
# pagination pages within a sub). 6.0s keeps us comfortably below PullPush's
# 1000 req/hour budget; can be lowered to ~1.0s safely.
REDDIT_DELAY_SECONDS=6.0
# Max submissions to fetch per subreddit per scheduled run. PullPush page
# size is 100, so default = 1 request per sub per run.
REDDIT_MAX_POSTS_PER_SUB=100
# How often the reddit ingestion job fires (hours).
REDDIT_CRON_INTERVAL_HOURS=12
# Optional cap on subreddits per run. Leave empty for "all configured subs".
REDDIT_MAX_SUBREDDITS_PER_RUN=
```

### 5. Test fixture (`tests/fixtures/pullpush_submissions.json`)

```json
{
  "data": [
    {
      "id": "abc001",
      "title": "Launched my AI habit tracker with streak gamification",
      "selftext": "Built an ai wellness coaching app with habit tracker features.",
      "permalink": "/r/startups/comments/abc001/launched_my_ai_habit_tracker/",
      "subreddit": "startups",
      "score": 342,
      "num_comments": 78,
      "author": "founder1",
      "created_utc": 1745500000
    },
    {
      "id": "abc002",
      "title": "Local LLM running on-device — my experience with private ai",
      "selftext": "Running local llm and edge ai app on my phone.",
      "permalink": "/r/SideProject/comments/abc002/local_llm_running/",
      "subreddit": "SideProject",
      "score": 198,
      "num_comments": 45,
      "author": "hacker42",
      "created_utc": 1745503600
    },
    {
      "id": "abc003",
      "title": "Side project: minimalist mood tracker",
      "selftext": "Just a tiny mood tracker I built over the weekend.",
      "permalink": "/r/SideProject/comments/abc003/minimalist_mood_tracker/",
      "subreddit": "SideProject",
      "score": 87,
      "num_comments": 12,
      "author": "weekender",
      "created_utc": 1745504000
    }
  ],
  "metadata": {"size": 3}
}
```

### Testing approach (deviation from spec)

The spec asks for `pytest-httpx` fixtures, but every other connector test in this repo (`tests/ingestion/test_*_connector.py`, plus the current `test_reddit_connector.py`) uses `httpx.MockTransport` directly. Introducing `pytest-httpx` would (a) add a new dev dep, (b) make the Reddit tests stylistically inconsistent with the rest of the suite, and (c) provides no capability we lack — `MockTransport` already covers per-request handler logic, header inspection, sequenced responses, and error injection.

**Decision:** keep `httpx.MockTransport`. If the user prefers the new dep, that's a one-line `pyproject.toml` change applied during Task 4; flag for review before committing.

## Tasks

Per `CLAUDE.md`: every command that invokes `uv` / `python` / `pytest` must be **requested from the user** ("please run …") and the output pasted back into the conversation. The plan executor never runs those directly.

### Task 1 — Settings additions and removals

**Files:**
- Modify: `app/config.py:63-77` (Reddit settings block)
- Modify: `tests/test_config.py:49-61` (existing reddit defaults tests)
- Modify: `.env.example:52-65` (Reddit block)
- Modify: `tests/test_config.py:29-46` (`test_env_example_covers_required_settings` — add the new key if needed; the current `required` set does not include reddit keys so likely no change, but verify)

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`, replace the existing `test_reddit_settings_defaults` and `test_reddit_max_subreddits_env` with:

```python
def test_reddit_settings_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.reddit_delay_seconds == 6.0
    assert s.reddit_cron_interval_hours == 12
    assert s.reddit_max_subreddits_per_run is None
    assert s.reddit_max_posts_per_sub == 100


def test_reddit_max_subreddits_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDDIT_MAX_SUBREDDITS_PER_RUN", "3")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0.5")
    monkeypatch.setenv("REDDIT_MAX_POSTS_PER_SUB", "200")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.reddit_max_subreddits_per_run == 3
    assert s.reddit_delay_seconds == 0.5
    assert s.reddit_max_posts_per_sub == 200


def test_reddit_oauth_settings_removed() -> None:
    """OAuth was never shipped — settings must not silently linger."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert not hasattr(s, "reddit_client_id")
    assert not hasattr(s, "reddit_client_secret")
    assert not hasattr(s, "reddit_user_agent")
```

- [ ] **Step 2: Run test to verify it fails**

Ask the user to run:

```
uv run pytest tests/test_config.py -v
```

Expected: `test_reddit_settings_defaults` FAILS on `reddit_max_posts_per_sub` (attribute missing). `test_reddit_oauth_settings_removed` FAILS (the attributes still exist).

- [ ] **Step 3: Implement settings changes**

Edit `app/config.py`:

1. Delete lines 63-65 (`reddit_client_id`, `reddit_client_secret`, `reddit_user_agent`).
2. After `reddit_max_subreddits_per_run` (currently line 77), insert:

```python
reddit_max_posts_per_sub: int = 100
```

- [ ] **Step 4: Update `.env.example`**

Replace lines 52-65 with the block shown in **Design §4**. Sanity check: `REDDIT_USER_AGENT`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` are no longer present anywhere in the file.

- [ ] **Step 5: Re-run config tests**

Ask the user to run:

```
uv run pytest tests/test_config.py -v
```

Expected: all three tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/config.py .env.example tests/test_config.py
git commit -m "$(cat <<'EOF'
refactor(reddit): drop OAuth/UA settings, add REDDIT_MAX_POSTS_PER_SUB

Reddit OAuth was never shipped — remove reddit_client_id,
reddit_client_secret, and reddit_user_agent from Settings and
.env.example. Prep for the PullPush migration (no auth required,
no UA needed).

Add reddit_max_posts_per_sub (default 100) to bound per-sub
pagination on the upcoming PullPush adapter.
EOF
)"
```

### Task 2 — Add PullPush fixture

**Files:**
- Create: `tests/fixtures/pullpush_submissions.json`

- [ ] **Step 1: Create the fixture**

Write the JSON file with the exact contents in **Design §5** to `tests/fixtures/pullpush_submissions.json`.

- [ ] **Step 2: Sanity-check it parses**

Ask the user to run:

```
uv run python -c "import json; print(len(json.load(open('tests/fixtures/pullpush_submissions.json'))['data']))"
```

Expected output: `3`.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/pullpush_submissions.json
git commit -m "test(reddit): add PullPush submissions fixture"
```

### Task 3 — Rewrite the Reddit connector test suite

> We deliberately write the new tests **before** rewriting the connector so the suite drives implementation (TDD). The existing tests against `RedditRateLimited` / Reddit JSON shape are replaced wholesale.

**Files:**
- Rewrite: `tests/ingestion/test_reddit_connector.py`
- Delete (after this task): `tests/fixtures/reddit_new.json`

- [ ] **Step 1: Replace the test file contents**

Overwrite `tests/ingestion/test_reddit_connector.py` with:

```python
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.ingestion.base import ConnectorRunRegistry
from app.ingestion.reddit_connector import RedditConnector

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "pullpush_submissions.json"


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text())


def _ok_response(posts: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"data": posts, "metadata": {"size": len(posts)}})


def _make_connector(handler) -> tuple[RedditConnector, httpx.AsyncClient, ConnectorRunRegistry]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    registry = ConnectorRunRegistry()
    connector = RedditConnector(client, registry)
    return connector, client, registry


@pytest.mark.asyncio
async def test_calls_pullpush_endpoint_with_subreddit(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    monkeypatch.setenv("REDDIT_MAX_POSTS_PER_SUB", "100")
    get_settings.cache_clear()

    seen_urls: list[str] = []
    seen_params: list[dict] = []
    fixture = _load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url).split("?")[0])
        seen_params.append(dict(request.url.params))
        # Return just the startups post from the fixture.
        return _ok_response([fixture["data"][0]])

    connector, client, _ = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_urls == ["https://api.pullpush.io/reddit/search/submission/"]
    p = seen_params[0]
    assert p["subreddit"] == "startups"
    assert p["sort"] == "created_utc"
    assert p["order"] == "desc"
    assert int(p["size"]) == 100
    assert "after" in p  # cold-start uses now - cron_interval


@pytest.mark.asyncio
async def test_normalize_round_trip_matches_legacy_shape(monkeypatch):
    """The adapter + normalize() pipeline produces the same SourceItem fields
    we would have produced from the legacy Reddit JSON listing for an equivalent post."""
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    fixture = _load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response([fixture["data"][0]])

    connector, client, _ = _make_connector(handler)
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
    assert item.url == "https://www.reddit.com/r/startups/comments/abc001/launched_my_ai_habit_tracker/"
    assert item.created_at == datetime.fromtimestamp(1745500000, tz=UTC)
    assert item.metadata == {
        "subreddit": "startups",
        "ups": 342,  # mapped from PullPush "score"
        "num_comments": 78,
        "author": "founder1",
    }
    assert item.role == "extraction"


@pytest.mark.asyncio
async def test_uses_registry_last_run_as_after_cursor(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    last_run = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    seen_after: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_after.append(int(request.url.params["after"]))
        return _ok_response([])

    connector, client, registry = _make_connector(handler)
    # Simulate a previous successful run.
    registry.mark_running("reddit")
    registry.mark_success("reddit", items=0, duration=0.0)
    registry._statuses["reddit"].last_run_at = last_run  # override clock

    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_after == [int(last_run.timestamp())]


@pytest.mark.asyncio
async def test_cold_start_falls_back_to_cron_interval_window(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    monkeypatch.setenv("REDDIT_CRON_INTERVAL_HOURS", "6")
    get_settings.cache_clear()

    seen_after: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_after.append(int(request.url.params["after"]))
        return _ok_response([])

    connector, client, _ = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    # Cold start: cursor ≈ now - 6h. Allow a small window for clock skew.
    now_ts = int(datetime.now(UTC).timestamp())
    expected = now_ts - 6 * 3600
    assert abs(seen_after[0] - expected) < 30


@pytest.mark.asyncio
async def test_pagination_advances_before_until_short_page(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    monkeypatch.setenv("REDDIT_MAX_POSTS_PER_SUB", "250")
    get_settings.cache_clear()

    # Build 250 fake posts with strictly descending created_utc.
    posts = [
        {
            "id": f"p{i:04d}",
            "title": f"post {i}",
            "selftext": "",
            "permalink": f"/r/startups/comments/p{i:04d}/post/",
            "subreddit": "startups",
            "score": 1,
            "num_comments": 0,
            "author": "u",
            "created_utc": 1_800_000_000 - i * 60,
        }
        for i in range(250)
    ]

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        # Filter by before / size: return the next `size` items strictly older
        # than `before` (or all if before missing).
        before = int(params["before"]) if "before" in params else None
        size = int(params["size"])
        eligible = [p for p in posts if before is None or p["created_utc"] < before]
        return _ok_response(eligible[:size])

    connector, client, _ = _make_connector(handler)
    try:
        raw = await connector.fetch()
    finally:
        await client.aclose()

    # 250 posts at 100 per page → 3 pages (100 + 100 + 50). Last page is short → stop.
    assert len(calls) == 3
    assert len(raw) == 250
    # Pagination must advance `before` to the oldest seen on each subsequent page.
    assert "before" not in calls[0]
    assert int(calls[1]["before"]) == posts[99]["created_utc"]
    assert int(calls[2]["before"]) == posts[199]["created_utc"]


@pytest.mark.asyncio
async def test_max_subreddits_per_run_caps_loop(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject,Entrepreneur,reactnative")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    monkeypatch.setenv("REDDIT_MAX_SUBREDDITS_PER_RUN", "2")
    get_settings.cache_clear()

    seen_subs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_subs.append(request.url.params["subreddit"])
        return _ok_response([])

    connector, client, _ = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_subs == ["startups", "SideProject"]


@pytest.mark.asyncio
async def test_graceful_skip_on_http_error(monkeypatch):
    """Any PullPush failure for one sub must not abort the whole run."""
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    fixture = _load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        sub = request.url.params["subreddit"]
        if sub == "startups":
            return httpx.Response(503)
        return _ok_response([p for p in fixture["data"] if p["subreddit"] == "SideProject"])

    connector, client, _ = _make_connector(handler)
    try:
        raw = await connector.fetch()
    finally:
        await client.aclose()

    # First sub failed — second sub still ingested.
    ids = [c["data"]["name"] for c in raw]
    assert ids == ["t3_abc002", "t3_abc003"]


@pytest.mark.asyncio
async def test_graceful_skip_on_malformed_json(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        sub = request.url.params["subreddit"]
        if sub == "startups":
            return httpx.Response(200, content=b"<<not json>>")
        return _ok_response([])

    connector, client, _ = _make_connector(handler)
    try:
        raw = await connector.fetch()
    finally:
        await client.aclose()

    assert raw == []


@pytest.mark.asyncio
async def test_run_marks_success_even_when_one_sub_fails(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    fixture = _load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        sub = request.url.params["subreddit"]
        if sub == "startups":
            return httpx.Response(429)
        return _ok_response([p for p in fixture["data"] if p["subreddit"] == "SideProject"])

    connector, client, _ = _make_connector(handler)

    async def _no_save(items):
        return len(items)

    connector.save = _no_save  # type: ignore[assignment]

    try:
        status = await connector.run()
    finally:
        await client.aclose()

    assert status.last_status == "ok"
    assert status.items_ingested == 2  # the two SideProject posts


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
        return _ok_response([])

    connector, client, _ = _make_connector(handler)
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    # 3 subs, each succeeded with a single empty page → 3 inter-sub sleeps.
    assert sleeps == [1.5, 1.5, 1.5]
```

- [ ] **Step 2: Run the new test file to verify it fails**

Ask the user to run:

```
uv run pytest tests/ingestion/test_reddit_connector.py -v
```

Expected: every test FAILS (the new fetch() implementation doesn't exist yet — the file still has the old Reddit JSON code).

- [ ] **Step 3: Delete the stale fixture**

```bash
git rm tests/fixtures/reddit_new.json
```

(We do this here, not earlier, because the old test file still references it until Step 1 lands.)

- [ ] **Step 4: Commit the test rewrite (still red)**

```bash
git add tests/ingestion/test_reddit_connector.py tests/fixtures/reddit_new.json
git commit -m "test(reddit): rewrite suite for PullPush adapter (red)"
```

> Committing red here is a deliberate handoff point. The next task makes the suite green.

### Task 4 — Rewrite `RedditConnector.fetch()`

**Files:**
- Modify: `app/ingestion/reddit_connector.py` (full rewrite of `fetch`, add `_pullpush_to_child`, add `_fetch_sub`, drop `RedditRateLimited`, `_REDDIT_BACKOFF_STATUSES`, `_fetch_sub_latest`, `_fetch_sub_backfill`)
- Keep verbatim: `normalize()` (lines 123-146 of the current file)

- [ ] **Step 1: Apply the rewrite**

Replace `app/ingestion/reddit_connector.py` in full with the implementation shown in **Design §2** + **Design §3**. Concretely the new file is:

```python
import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem

_PULLPUSH_BASE = "https://api.pullpush.io/reddit/search/submission/"
_PAGE_SIZE = 100  # PullPush max


def _pullpush_to_child(post: dict) -> dict | None:
    pid = post.get("id")
    if not pid:
        return None
    return {
        "data": {
            "name": f"t3_{pid}",
            "title": post.get("title"),
            "selftext": post.get("selftext") or "",
            "permalink": post.get("permalink"),
            "subreddit": post.get("subreddit"),
            "ups": post.get("score"),
            "num_comments": post.get("num_comments"),
            "author": post.get("author"),
            "created_utc": post.get("created_utc"),
        }
    }


class RedditConnector(BaseConnector):
    source_type = "reddit"

    async def fetch(self, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        settings = get_settings()
        delay = settings.reddit_delay_seconds
        max_posts = settings.reddit_max_posts_per_sub
        subs = settings.reddit_subreddits
        cap = settings.reddit_max_subreddits_per_run
        if cap is not None and cap >= 0:
            subs = subs[:cap]

        if since is None:
            last_run = self.registry.get(self.source_type).last_run_at
            if last_run is not None:
                since = last_run
            else:
                since = datetime.now(UTC) - timedelta(hours=settings.reddit_cron_interval_hours)

        after_ts = int(since.timestamp())
        before_ts: int | None = int(until.timestamp()) if until is not None else None

        children: list[dict] = []
        for sub in subs:
            try:
                sub_children = await self._fetch_sub(sub, after_ts, before_ts, max_posts, delay)
            except Exception as exc:  # noqa: BLE001
                self.log.warning(
                    "PullPush fetch failed — skipping subreddit",
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
        self,
        sub: str,
        after_ts: int,
        before_ts: int | None,
        max_posts: int,
        delay: float,
    ) -> list[dict]:
        collected: list[dict] = []
        page_before = before_ts

        while len(collected) < max_posts:
            page_size = min(_PAGE_SIZE, max_posts - len(collected))
            params: dict = {
                "subreddit": sub,
                "size": page_size,
                "sort": "created_utc",
                "order": "desc",
                "after": after_ts,
            }
            if page_before is not None:
                params["before"] = page_before

            resp = await self._request_with_retry("GET", _PULLPUSH_BASE, params=params)
            payload = resp.json()
            posts = payload.get("data", [])
            if not posts:
                break

            oldest_ts: int | None = None
            for post in posts:
                child = _pullpush_to_child(post)
                if child is None:
                    continue
                collected.append(child)
                created = post.get("created_utc")
                if isinstance(created, (int, float)):
                    oldest_ts = int(created) if oldest_ts is None else min(oldest_ts, int(created))

            if len(posts) < page_size:
                break
            if oldest_ts is None:
                break
            page_before = oldest_ts

            await asyncio.sleep(delay)

        self.log.info(
            "PullPush sub fetch complete",
            subreddit=sub,
            items=len(collected),
            after_ts=after_ts,
            before_ts=before_ts,
        )
        return collected

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
                url=f"https://www.reddit.com{d['permalink']}" if d.get("permalink") else None,
                created_at=datetime.fromtimestamp(created, tz=UTC) if created else None,
                metadata={
                    "subreddit": d.get("subreddit"),
                    "ups": d.get("ups"),
                    "num_comments": d.get("num_comments"),
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

Expected: all 10 tests PASS.

- [ ] **Step 3: Run the full test suite to catch upstream breakage**

Ask the user to run:

```
uv run pytest -v
```

Expected: green. The most likely failures:

- Anything that imports `RedditRateLimited` outside `tests/ingestion/test_reddit_connector.py` — `grep -rn "RedditRateLimited" app tests scripts` to confirm none exist before claiming success.
- Anything that imports `reddit_user_agent` from settings — should not exist after Task 1, but verify with `grep -rn "reddit_user_agent" app tests scripts`.

If anything is red: fix it in this task, do not defer.

- [ ] **Step 4: Commit**

```bash
git add app/ingestion/reddit_connector.py
git commit -m "$(cat <<'EOF'
feat(reddit): migrate connector from Reddit JSON API to PullPush.io

Replaces calls to reddit.com/r/{sub}/new.json with PullPush
(api.pullpush.io/reddit/search/submission/), an unauthenticated
Reddit mirror with a 1000 req/hour budget. Datacenter IPs are not
blocked.

- Pagination via after/before cursors on created_utc, capped by
  REDDIT_MAX_POSTS_PER_SUB (default 100 = one request per sub per run).
- Incremental fetch driven by ConnectorRunRegistry.last_run_at;
  cold start falls back to a REDDIT_CRON_INTERVAL_HOURS window.
- Graceful per-sub degradation: any HTTP error / bad JSON logs a
  structured warning and continues with the next sub instead of
  aborting the run.
- normalize() and the SourceItem shape are unchanged; the adapter
  prefixes "t3_" to PullPush's bare id so external_id stays stable
  across the migration.
- Removes RedditRateLimited and the 429/403 short-circuit; PullPush
  has no equivalent ban risk for our request volume.
EOF
)"
```

### Task 5 — Final full sweep + lint

- [ ] **Step 1: Run lint / type-check**

Ask the user to run whichever of these the repo uses (check `pyproject.toml` / `.pre-commit-config.yaml`):

```
uv run ruff check app tests
uv run mypy app
```

Fix anything that lights up — most likely an unused-import warning from the removed `_REDDIT_BACKOFF_STATUSES`.

- [ ] **Step 2: Final commit (if anything was fixed)**

```bash
git add <touched files>
git commit -m "chore: lint cleanup after PullPush migration"
```

If nothing changed, skip the commit.

## Verification

End-to-end checks the user can run on the VPS (or a local dev shell with the prod-like `.env`).

> All commands assume `docker compose` because that matches the deployment shape per `docs/superpowers/plans/2026-05-12-cicd-plan-c-cd-secrets-rollback.md`. If running outside Docker, swap `docker compose exec app` for `uv run`.

### 1. PullPush is reachable from the VPS

```
docker compose exec app python -m scripts.run_job --list
```

Expected: `reddit_ingestion` is listed with its `interval` trigger.

Then probe PullPush from inside the container (no Python deps needed):

```
docker compose exec app sh -c "wget -qO- 'https://api.pullpush.io/reddit/search/submission/?subreddit=startups&size=1' | head -c 400"
```

Expected: a JSON blob starting with `{"data":[{` and containing an `id` field. If this hangs or returns a 5xx, PullPush is down — fall back to a curl from the host to confirm before assuming a code bug.

### 2. Real ingestion run against the dev DB

```
docker compose exec -e REDDIT_MAX_SUBREDDITS_PER_RUN=2 -e REDDIT_DELAY_SECONDS=1 \
  app python -m scripts.run_job --job reddit_ingestion
```

Expected log lines (structlog, in order):

```
PullPush sub fetch complete  subreddit=startups   items=<N>   after_ts=<…>
PullPush sub fetch complete  subreddit=SideProject items=<M>  after_ts=<…>
Ingestion complete  component=RedditConnector  source_type=reddit  status=ok  items_ingested=<N+M>
```

Then confirm rows landed in `source_items`:

```
docker compose exec postgres psql -U devtrend -d devtrend -c \
  "select count(*), max(created_at) from source_items where source_type='reddit';"
```

`count` should match the sum of `items_ingested` from the previous step (within deduplication tolerance). `max(created_at)` should be recent.

### 3. Incremental fetch — second run fetches ≈ 0 items

Run step 2 again **without restarting the app container** (so the in-memory `ConnectorRunRegistry` retains `last_run_at`):

```
docker compose exec -e REDDIT_MAX_SUBREDDITS_PER_RUN=2 -e REDDIT_DELAY_SECONDS=1 \
  app python -m scripts.run_job --job reddit_ingestion
```

Expected: `items_ingested=0` (or 1-2 if a new submission landed in the few seconds between runs). The `after_ts` log value should now equal the wall-clock of the first run, not `now - cron_interval_hours`.

> If the registry is *not* preserved between `run_job.py` invocations (each `run_job` invocation is a separate process), the second-run cursor falls back to the cron-interval window. In that case re-run the same job back-to-back via `scheduler.add_job(...).func()` in a Python REPL inside the container, or trust the production scheduled-run logs (which share a process).

### 4. Graceful degradation when PullPush is unreachable

Force a DNS failure for PullPush by pointing the connector at a bogus host:

```
docker compose exec \
  -e REDDIT_MAX_SUBREDDITS_PER_RUN=2 \
  -e REDDIT_DELAY_SECONDS=0 \
  -e LLM_PROVIDER=mock \
  -e EMBEDDING_PROVIDER=mock \
  -e HTTPS_PROXY=http://127.0.0.1:1 \
  app python -m scripts.run_job --job reddit_ingestion
```

> Setting `HTTPS_PROXY` to a closed port reliably breaks outbound HTTPS without code changes. (We use `--llm-provider mock` equivalents via env so the brief-generation downstream doesn't error on the now-empty pipeline.)

Expected:
- Two `PullPush fetch failed — skipping subreddit  subreddit=… error=… error_type=ConnectError` warning lines.
- One `Ingestion complete … status=ok items_ingested=0` line.
- Process exit code 0.

Unset `HTTPS_PROXY` afterwards and re-run step 2 to confirm recovery on the very next run.

### 5. Cap=0 sanity (no HTTP calls)

```
docker compose exec -e REDDIT_MAX_SUBREDDITS_PER_RUN=0 \
  app python -m scripts.run_job --job reddit_ingestion
```

Expected: `items_ingested=0`, zero `PullPush sub fetch complete` lines, exit 0.

## Out of scope

- **PullPush response shape drift.** PullPush is community-run; if they rename `score` → `ups` or change the envelope, the adapter breaks. We deliberately do not add schema validation here — the test suite mocks PullPush, so a real-world drift would surface as a sudden drop in ingestion count, which is exactly the kind of thing the existing `Ingestion complete  items_ingested=0` log + scheduler health check should catch.
- **Per-subreddit jitter / parallel fetch.** The list is ≤ 10 subs and we have a 1000 req/hour budget — serial, fixed-delay fetch is plenty. Revisit if the sub list grows past 30.
- **Backfill mode tuning.** `since`/`until` are honored by `_fetch_sub`, but the 1000-per-sub backfill ceiling of the old code is now governed by `REDDIT_MAX_POSTS_PER_SUB`. A separate task can re-tune the backfill ceiling and restore explicit "hit X-item ceiling" logging if the operator wants it.
- **Switching tests to `pytest-httpx`.** Noted in the spec, deferred for consistency with the rest of the suite. See "Testing approach" in **Design**.
- **Migrating other connectors** (HN, GitHub) to use `ConnectorRunRegistry` for incremental cursors. The pattern works there too but is out of scope for this PR.

# Reddit connector — rate limiting & request hygiene

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add rate limiting, configurable cadence, and graceful 429/403 handling to the Reddit connector so the new VPS does not get blocked by Reddit's public JSON API.

**Architecture:** The connector keeps using the public JSON endpoint (`https://www.reddit.com/r/{sub}/new.json`) — Reddit OAuth registration is unavailable. We enforce a global **≤10 requests/minute** ceiling across the entire Reddit connector by spacing every outbound HTTP call by at least `REDDIT_DELAY_SECONDS` seconds (default **6.0s** = 60s ÷ 10 req); since the same delay is used between subreddits *and* between backfill pagination pages, the worst-case sustained rate over any 60-second window stays ≤10 requests. On top of that we add a configurable cron interval, an optional per-run cap on subreddits, and a "fail-soft" path that aborts the rest of the run on 429/403 without immediate retry. All settings are env-driven via `app/config.py`.

**Tech Stack:** `httpx.AsyncClient`, `app/ingestion/http_utils.py:request_with_retry`, APScheduler `IntervalTrigger`, `structlog`, pydantic-settings.

---

## Context

`app/ingestion/reddit_connector.py` fetches posts from `https://www.reddit.com/r/{sub}/new.json` for each subreddit in `settings.reddit_subreddits` using `request_with_retry` (which retries 429 up to 3 times). The previous VPS IP was blocked by Reddit (HTTP 403) after sustained datacenter traffic; we are migrating to a new IP and need to keep this one alive.

Constraints:
- **No OAuth.** We cannot register a Reddit OAuth app right now, so we continue with the unauthenticated public JSON API (10 req/min soft cap).
- **Conventions** must match `app/ingestion/hn_connector.py` and reuse `request_with_retry`.
- All logging via `structlog`; async throughout.
- Per `CLAUDE.md`, the engineer running this plan must ask the user to execute `uv` / `pytest` / `python` commands and paste output back.

Today's gaps vs. the goal:
| # | Goal | Current state |
|---|---|---|
| 1 | Unique, descriptive `User-Agent` configurable via `REDDIT_USER_AGENT` | Already wired — `settings.reddit_user_agent` is sent. Default `"DevTrend/1.0 (by /u/yourhandle)"` is generic; we'll tighten the example. |
| 2 | Configurable delay between Reddit HTTP calls (acts as the global ≤10 RPM ceiling) | Hard-coded `asyncio.sleep(1)` in both `fetch` and `_fetch_sub_backfill`. With 1s spacing, backfill pagination sustains ~60 req/min — well over Reddit's unauthenticated cap. |
| 3 | Configurable cron interval (12h default) | `app/ingestion/scheduler.py:46` hard-codes `IntervalTrigger(hours=12)`. We make it env-driven. |
| 4 | Cap subreddits per run | The loop iterates all of `settings.reddit_subreddits` unconditionally. |
| 5 | Graceful 429/403 handling: log + skip remaining subs + no immediate retry | `request_with_retry` *retries* 429 up to 3 times (then raises `RuntimeError`); 403 raises `httpx.HTTPStatusError` via `raise_for_status`. Either failure currently aborts the whole connector run via the exception path in `BaseConnector.run`. |
| 6 | Config + `.env.example` documentation | Three new env vars to add. |
| 7 | Tests | No `tests/ingestion/test_reddit_connector.py` exists. We create it. |

## Files to modify

| File | Change |
|---|---|
| `app/config.py` (after the existing Reddit block, ~line 71) | Add `reddit_delay_seconds: float = 6.0` (= 10 RPM ceiling), `reddit_cron_interval_hours: int = 12`, `reddit_max_subreddits_per_run: int \| None = None`. Tighten default `reddit_user_agent`. |
| `app/ingestion/http_utils.py` | Add a keyword-only `no_retry_statuses: set[int] \| None = None` parameter. When the response status is in that set, return immediately without retrying or raising. |
| `app/ingestion/reddit_connector.py` | Define `RedditRateLimited` exception; pass `no_retry_statuses={429, 403}` to every Reddit HTTP call; raise `RedditRateLimited` when one of those statuses is observed; catch it at the subreddit-loop boundary to short-circuit the rest of the run. Apply `settings.reddit_delay_seconds` between subreddit calls (replacing the hard-coded `1`). Apply `settings.reddit_max_subreddits_per_run` to slice the subreddit list. |
| `app/ingestion/scheduler.py:46` | Replace `IntervalTrigger(hours=12)` with `IntervalTrigger(hours=settings.reddit_cron_interval_hours)`. |
| `.env.example` (around line 56) | Document the four Reddit knobs (`REDDIT_USER_AGENT`, `REDDIT_DELAY_SECONDS`, `REDDIT_CRON_INTERVAL_HOURS`, `REDDIT_MAX_SUBREDDITS_PER_RUN`). |
| `tests/ingestion/test_reddit_connector.py` *(new)* | Unit tests for delay, cap, 429/403 short-circuit, user-agent header, scheduled-vs-backfill paths. |
| `tests/test_scheduler.py` | Add one assertion that the reddit job's interval reflects `settings.reddit_cron_interval_hours`. |
| `tests/test_http_utils.py` *(may or may not exist — see Task 2)* | Cover the new `no_retry_statuses` branch. |

## Design

### 1. `Settings` additions (`app/config.py`, ~line 65-71)

In the existing "Data Sources" block:

```python
reddit_user_agent: str = "DevTrend/1.0 (research-only; contact: you@example.com)"
# Ingestion behavior
reddit_subreddits: list[str] = [
    "startups", "SideProject", "Entrepreneur",
    "reactnative", "androiddev", "iOSProgramming",
    "AppIdeas",
]
# Minimum spacing between *any* two Reddit HTTP calls (between subs in the
# scheduled path AND between pagination pages in backfill). 6.0s = 60s / 10
# req → enforces the ≤10 req/min ceiling globally for the connector.
reddit_delay_seconds: float = 6.0
reddit_cron_interval_hours: int = 12
reddit_max_subreddits_per_run: int | None = None  # None = all
```

`pydantic-settings` maps these to `REDDIT_DELAY_SECONDS`, `REDDIT_CRON_INTERVAL_HOURS`, `REDDIT_MAX_SUBREDDITS_PER_RUN` automatically. `int | None` parses an empty env var as `None` (we already set `env_ignore_empty=True` in `SettingsConfigDict`).

Reasoning for the defaults:
- `6.0s` between every Reddit request is the **rate-limit mechanism**: since the connector is fully serial (no parallel `httpx` calls to Reddit), spacing each call by ≥6.0s means the sliding 60-second window can hold at most 10 requests. This holds for *both* the scheduled path (7 subs × 1 req = ~36s per run) and the backfill path (10 pages × 7 subs = ~6 minutes for a full backfill). The user-tunable knob (`REDDIT_DELAY_SECONDS`) is the single control surface for the rate cap.
- `12h` cadence cuts daily request volume in half vs the old 6h cron.
- `None` for cap keeps the current behavior so existing deployments aren't surprised.

### 2. `request_with_retry` — opt-out of 429 retry (`app/ingestion/http_utils.py`)

Add a small kw-only parameter so callers can ask the helper to return certain statuses verbatim instead of retrying or raising:

```python
async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    no_retry_statuses: set[int] | None = None,
    **kwargs,
) -> httpx.Response:
    no_retry = no_retry_statuses or set()
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code in no_retry:
                return resp
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(min(retry_after, 30))
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(min(2 ** attempt, 30))
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            await asyncio.sleep(min(2 ** attempt, 30))
    raise last_exc or RuntimeError(f"Failed after 3 attempts: {url}")
```

This is a backward-compatible addition: every existing caller (HN, GitHub, Play Store, iOS RSS) still gets the same behavior. Reddit alone opts in with `no_retry_statuses={429, 403}` and inspects the returned status itself.

### 3. Reddit connector rewrite (`app/ingestion/reddit_connector.py`)

```python
import asyncio
from datetime import UTC, datetime

from app.config import get_settings
from app.ingestion.base import BaseConnector, NormalizedItem

# Statuses that mean "Reddit is unhappy — back off and try again next cron tick".
_REDDIT_BACKOFF_STATUSES = {429, 403}


class RedditRateLimited(Exception):
    """Raised when Reddit returns 429 or 403; abort the current run, no retry."""

    def __init__(self, status_code: int, subreddit: str) -> None:
        super().__init__(f"Reddit returned {status_code} for r/{subreddit}")
        self.status_code = status_code
        self.subreddit = subreddit


class RedditConnector(BaseConnector):
    source_type = "reddit"

    async def fetch(self, since: datetime | None = None, until: datetime | None = None) -> list[dict]:
        settings = get_settings()
        headers = {"User-Agent": settings.reddit_user_agent}
        delay = settings.reddit_delay_seconds
        subs = settings.reddit_subreddits
        cap = settings.reddit_max_subreddits_per_run
        if cap is not None and cap >= 0:
            subs = subs[:cap]

        posts: list[dict] = []
        for sub in subs:
            try:
                if since is None:
                    sub_posts = await self._fetch_sub_latest(sub, headers)
                else:
                    sub_posts = await self._fetch_sub_backfill(sub, headers, since, delay)
            except RedditRateLimited as exc:
                self.log.warning(
                    "Reddit rate limited — skipping remaining subreddits",
                    status_code=exc.status_code,
                    subreddit=exc.subreddit,
                    subreddits_completed=subs.index(sub),
                    subreddits_total=len(subs),
                    items_so_far=len(posts),
                )
                break
            posts.extend(sub_posts)
            await asyncio.sleep(delay)

        return posts

    async def _fetch_sub_latest(self, sub: str, headers: dict) -> list[dict]:
        resp = await self._request_with_retry(
            "GET",
            f"https://www.reddit.com/r/{sub}/new.json",
            headers=headers,
            params={"limit": 50},
            no_retry_statuses=_REDDIT_BACKOFF_STATUSES,
        )
        if resp.status_code in _REDDIT_BACKOFF_STATUSES:
            raise RedditRateLimited(resp.status_code, sub)
        return resp.json().get("data", {}).get("children", [])

    async def _fetch_sub_backfill(
        self, sub: str, headers: dict, since: datetime, delay: float
    ) -> list[dict]:
        all_posts: list[dict] = []
        after: str | None = None
        oldest_age_days: float | None = None

        while len(all_posts) < 1000:
            params: dict = {"limit": 100}
            if after:
                params["after"] = after

            resp = await self._request_with_retry(
                "GET",
                f"https://www.reddit.com/r/{sub}/new.json",
                headers=headers,
                params=params,
                no_retry_statuses=_REDDIT_BACKOFF_STATUSES,
            )
            if resp.status_code in _REDDIT_BACKOFF_STATUSES:
                raise RedditRateLimited(resp.status_code, sub)

            data = resp.json().get("data", {})
            children = data.get("children", [])
            if not children:
                break

            all_posts.extend(children)
            after = data.get("after")

            last_data = children[-1].get("data", {})
            last_created = last_data.get("created_utc")
            if last_created is not None:
                last_dt = datetime.fromtimestamp(last_created, tz=UTC)
                if last_dt < since:
                    oldest_age_days = (datetime.now(UTC) - last_dt).total_seconds() / 86400.0
                    break

            if not after:
                break

            await asyncio.sleep(delay)

        if oldest_age_days is not None:
            self.log.info(
                "Reddit sub backfill reached since boundary",
                sub=sub,
                oldest_item_age_days=round(oldest_age_days, 1),
            )
        else:
            self.log.info(
                "Reddit sub backfill hit 1000-item ceiling",
                sub=sub,
                items=len(all_posts),
            )

        return all_posts

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        # ... unchanged from current implementation ...
```

`normalize` is unchanged — copy verbatim from the current file (lines 90-113).

Key behaviors:
- `RedditRateLimited` is caught at the per-subreddit loop only. `BaseConnector.run` therefore observes a *successful* run with partial items (no `mark_error`) — the warning log signals what happened.
- The outer `asyncio.sleep(delay)` runs only between subreddits in the scheduled-run path; the backfill path passes `delay` into `_fetch_sub_backfill` for its inner page-pagination sleep, so backfill cadence also scales with the new setting.
- If `cap` slices the list to fewer subreddits, we still log the "completed" count using the (possibly sliced) `subs` list — accurate by construction.

### 4. Scheduler interval (`app/ingestion/scheduler.py:46`)

```python
scheduler.add_job(
    _make_job("reddit"),
    IntervalTrigger(hours=settings.reddit_cron_interval_hours),
    id="reddit_ingestion",
    max_instances=1,
    coalesce=True,
    misfire_grace_time=300,
)
```

### 5. `.env.example`

Replace the existing Reddit lines (`52-56`) with:

```
# Reddit ingestion
# User-Agent MUST be unique and descriptive per Reddit's API rules
# (https://github.com/reddit-archive/reddit/wiki/API). Generic strings get
# blocked. Include the project name and a contact identifier.
REDDIT_USER_AGENT=DevTrend/1.0 (research-only; contact: you@example.com)
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_SUBREDDITS=startups,SideProject,Entrepreneur,reactnative,androiddev,iOSProgramming,AppIdeas
# Minimum seconds between *any* two Reddit HTTP calls (between subs AND
# between backfill pagination pages). Acts as the global rate-limit ceiling:
# 6.0s ⇒ ≤10 req/min over any sliding 60-second window, matching Reddit's
# unauthenticated cap. Lower values risk a 403 IP block.
REDDIT_DELAY_SECONDS=6.0
# How often the reddit ingestion job fires (hours). Lower = more fresh data
# but more daily requests. 12h is the recommended floor for the public JSON API.
REDDIT_CRON_INTERVAL_HOURS=12
# Optional cap on subreddits per run. Leave empty for "all configured subs".
REDDIT_MAX_SUBREDDITS_PER_RUN=
```

## Tasks

Each task ends with a suggested commit message (Conventional Commits style, matching `git log`). Run tests after each task and ask the user to execute the `uv run pytest …` commands.

### Task 1 — Add `no_retry_statuses` to `request_with_retry`

**Files:**
- Modify: `app/ingestion/http_utils.py`
- Modify (or create): `tests/test_http_utils.py`

**Tests to write first** (TDD):

In `tests/test_http_utils.py`, add (or create the file with):

```python
import httpx
import pytest

from app.ingestion.http_utils import request_with_retry


@pytest.mark.asyncio
async def test_returns_no_retry_status_verbatim() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await request_with_retry(
            client, "GET", "https://example.com",
            no_retry_statuses={429},
        )

    assert resp.status_code == 429
    assert calls["n"] == 1  # no retry was attempted


@pytest.mark.asyncio
async def test_429_still_retries_when_not_opted_out() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Return 429 twice then 200 to confirm retry loop still works.
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await request_with_retry(client, "GET", "https://example.com")

    assert resp.status_code == 200
    assert calls["n"] == 3
```

Run: `uv run pytest tests/test_http_utils.py -v` → expect both to FAIL (param doesn't exist yet / signature mismatch).

**Implementation:** apply the body shown in **Design §2** above to `app/ingestion/http_utils.py`.

Re-run: `uv run pytest tests/test_http_utils.py -v` → both PASS.

**Suggested commit:**
```
feat(http): add no_retry_statuses opt-out to request_with_retry

Lets callers (e.g. the Reddit connector) inspect specific statuses
without the helper retrying or raising. Backward-compatible default.
```

### Task 2 — Add Reddit settings + tighten `.env.example`

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `tests/test_config.py` *(if it exists — search first)* OR add a small new test.

**Search first:** `grep -n "reddit" tests/test_config.py` — if the file exists, add a test there; otherwise add to `tests/ingestion/test_reddit_connector.py` in Task 4.

**Test (add to existing config tests if present):**

```python
def test_reddit_settings_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.reddit_delay_seconds == 6.0
    assert s.reddit_cron_interval_hours == 12
    assert s.reddit_max_subreddits_per_run is None


def test_reddit_max_subreddits_env(monkeypatch) -> None:
    monkeypatch.setenv("REDDIT_MAX_SUBREDDITS_PER_RUN", "3")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0.5")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.reddit_max_subreddits_per_run == 3
    assert s.reddit_delay_seconds == 0.5
```

Run: `uv run pytest tests/test_config.py -v` → FAIL (fields don't exist).

**Implementation:**
1. Edit `app/config.py` — add the three new fields per **Design §1**; update the default of `reddit_user_agent` to the more specific string.
2. Edit `.env.example` — replace lines 52-56 with the block shown in **Design §5**.

Re-run the test → PASS.

**Suggested commit:**
```
feat(config): add reddit rate-limiting knobs (delay, interval, cap)

REDDIT_DELAY_SECONDS, REDDIT_CRON_INTERVAL_HOURS,
REDDIT_MAX_SUBREDDITS_PER_RUN. Tighten REDDIT_USER_AGENT example
to be unique and contactable per Reddit's API rules.
```

### Task 3 — Wire `reddit_cron_interval_hours` into the scheduler

**Files:**
- Modify: `app/ingestion/scheduler.py:46`
- Modify: `tests/test_scheduler.py`

**Test to add to `tests/test_scheduler.py`** (after the existing tests):

```python
def test_reddit_job_uses_configured_interval() -> None:
    client = httpx.AsyncClient()
    registry = ConnectorRunRegistry()
    connectors = [
        GithubConnector(client, registry),
        HNConnector(client, registry),
        RedditConnector(client, registry),
    ]
    settings = Settings(_env_file=None, reddit_cron_interval_hours=24)  # type: ignore[call-arg]
    scheduler = build_scheduler(connectors, registry, settings)

    reddit_job = next(j for j in scheduler.get_jobs() if j.id == "reddit_ingestion")
    # APScheduler IntervalTrigger stores interval as a timedelta.
    assert reddit_job.trigger.interval.total_seconds() == 24 * 3600
```

Run: `uv run pytest tests/test_scheduler.py -v` → FAIL (still hard-coded 12h).

**Implementation:** apply **Design §4**.

Re-run → PASS.

**Suggested commit:**
```
feat(scheduler): make reddit ingestion interval configurable

Reads from settings.reddit_cron_interval_hours (default 12).
```

### Task 4 — Reddit connector: User-Agent, delay, sub cap, 429/403 short-circuit

**Files:**
- Modify: `app/ingestion/reddit_connector.py`
- Create: `tests/ingestion/test_reddit_connector.py`

**Tests to write first** (`tests/ingestion/test_reddit_connector.py`, new file):

```python
import asyncio
import json
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


def _make_connector(handler, settings: Settings) -> tuple[RedditConnector, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    registry = ConnectorRunRegistry()
    connector = RedditConnector(client, registry)
    # Inject settings via env override
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

    connector, client = _make_connector(handler, get_settings())
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_uas == ["MyAgent/9.9 (contact: a@b.co)"]


@pytest.mark.asyncio
async def test_delay_between_subreddits(monkeypatch):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject,Entrepreneur")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("app.ingestion.reddit_connector.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_ok_response_body())

    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "1.5")
    get_settings.cache_clear()

    connector, client = _make_connector(handler, get_settings())
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
        # URL form: https://www.reddit.com/r/<sub>/new.json
        parts = request.url.path.strip("/").split("/")
        seen_subs.append(parts[1])
        return httpx.Response(200, content=_ok_response_body())

    connector, client = _make_connector(handler, get_settings())
    try:
        await connector.fetch()
    finally:
        await client.aclose()

    assert seen_subs == ["startups", "SideProject"]


@pytest.mark.asyncio
async def test_429_short_circuits_remaining_subs(monkeypatch, caplog):
    monkeypatch.setenv("REDDIT_SUBREDDITS", "startups,SideProject,Entrepreneur")
    monkeypatch.setenv("REDDIT_DELAY_SECONDS", "0")
    get_settings.cache_clear()

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 2:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, content=_ok_response_body())

    connector, client = _make_connector(handler, get_settings())
    try:
        result = await connector.fetch()
    finally:
        await client.aclose()

    # First sub succeeded, second got 429 → third never called.
    assert call_count["n"] == 2
    # Items from sub #1 only.
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

    connector, client = _make_connector(handler, get_settings())
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

    # Skip DB writes: stub `save`.
    connector, client = _make_connector(handler, get_settings())

    async def _no_save(items):
        return len(items)

    connector.save = _no_save  # type: ignore[assignment]

    try:
        status = await connector.run()
    finally:
        await client.aclose()

    assert status.last_status == "ok"
    assert status.items_ingested > 0
```

> Note: The `test_delay_between_subreddits` test patches `app.ingestion.reddit_connector.asyncio.sleep`. The implementation in **Design §3** uses `asyncio.sleep(delay)` directly via the module-level `asyncio` import, which makes that patch reach it. Keep the import as `import asyncio` (not `from asyncio import sleep`) for this to work.

Run: `uv run pytest tests/ingestion/test_reddit_connector.py -v` → all FAIL (no `RedditRateLimited`, behavior not implemented).

**Implementation:** Apply the rewrite in **Design §3** to `app/ingestion/reddit_connector.py`. Keep `normalize()` verbatim from the current file.

Re-run → all PASS.

**Suggested commit:**
```
feat(reddit): rate limiting + graceful 429/403 handling

- Inter-request delay (REDDIT_DELAY_SECONDS, default 6.0s) enforces a
  global ≤10 req/min ceiling on the connector, applied both between subs
  and between backfill pagination pages
- Optional per-run subreddit cap (REDDIT_MAX_SUBREDDITS_PER_RUN)
- 429/403 raises RedditRateLimited, which the fetch loop catches to
  skip remaining subs and let the next cron tick retry naturally
- request_with_retry no longer retries 429 for Reddit (passes
  no_retry_statuses={429, 403})

The previous VPS IP was blocked (403) after sustained datacenter
traffic. These knobs let us stay under Reddit's 10 req/min cap on
the new IP without OAuth.
```

### Task 5 — Full test sweep

Ask the user to run the full suite to catch any regression elsewhere (in particular tests that mock `request_with_retry` or import from `app.ingestion.http_utils`):

```
uv run pytest -v
```

Expect: green. If any pre-existing reddit/http test breaks, fix it before moving on.

**No commit** unless something needed fixing.

## Verification

End-to-end checks the user can run on the **new VPS** to confirm the connector works without getting blocked. All use `scripts/run_job.py` per `2026-05-07-run-backfill-progress-bar.md` / `run-job-script.md`.

1. **List jobs and confirm interval changed:**
   ```
   docker compose exec app python -m scripts.run_job --list
   ```
   Expected: `reddit_ingestion` shows `interval[12:00:00]` (or whatever `REDDIT_CRON_INTERVAL_HOURS` is set to).

2. **One real Reddit ingestion run** (uses the 6.0s default delay → ≤10 RPM):
   ```
   docker compose exec -e REDDIT_MAX_SUBREDDITS_PER_RUN=2 \
     app python -m scripts.run_job --job reddit_ingestion
   ```
   Expected logs:
   - `Ingestion complete component=RedditConnector source_type=reddit status=ok items_ingested=<N>`
   - No `RedditRateLimited` warning.
   - Elapsed time ≥ 12s (2 subs × 6s default delay).

3. **Force backoff path locally** (against a Mock transport — already covered by the test suite, but worth a manual smoke against the real prod env):
   ```
   docker compose exec -e REDDIT_USER_AGENT="generic-bot" -e REDDIT_MAX_SUBREDDITS_PER_RUN=3 \
     app python -m scripts.run_job --job reddit_ingestion
   ```
   If Reddit blocks the bad UA: structured `Reddit rate limited` warning with `status_code=403`, run still exits 0 with partial `items_ingested`. Then immediately restore the real UA and re-run step 2 to confirm recovery on the very next invocation (no 30-min cooldown).

4. **Cap=0 sanity** (should produce zero items, no HTTP calls):
   ```
   docker compose exec -e REDDIT_MAX_SUBREDDITS_PER_RUN=0 \
     app python -m scripts.run_job --job reddit_ingestion
   ```
   Expected: `items_ingested=0`, no warning, exit 0.

5. **Confirm scheduled cadence on the live VPS** (after deploy):
   ```
   docker compose logs app | grep reddit_ingestion
   ```
   Expected: only one `Ingestion complete … source_type=reddit` per 12h period.

## Out of scope

- **Reddit OAuth migration** — tracked separately. Once we can register an app, the JSON endpoint gets replaced with `oauth.reddit.com/r/{sub}/new` and a Bearer token, raising the rate limit to 100 req/min. The knobs introduced here remain useful (per-sub delay, max subs per run) but the 429/403 logic should additionally refresh the token on 401.
- **Persistent backoff across runs** — we deliberately do not stash a "blocked until" timestamp. The 12h interval already gives Reddit a long cool-down; persistent state would add complexity without measurable benefit and would hide ongoing issues from the operator who is watching warnings.
- **Per-subreddit jitter** — uniform delay is fine for ≤10 subs. If the list grows past 20, revisit with randomized jitter.
- **`backfill_max_items_per_source` honoring** — the backfill path still hard-codes the 1000-item ceiling. That predates this work and is out of scope.

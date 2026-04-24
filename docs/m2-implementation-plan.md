# DevTrend — Milestone 2: Ingestion Layer — Implementation Plan

> Generated: 2026-04-24
> Status: Approved, ready to execute

---

## Design decisions

- **Run status**: in-memory `ConnectorRunRegistry` + DB fallback (no new table). `/sources` merges registry + `MAX(ingested_at)` from `source_items` for cold starts.
- **Reddit auth**: public JSON + UA header (`REDDIT_USER_AGENT`). No OAuth in M2. Defer to Phase 1.5 if rate-limited.
- **GitHub threshold**: `stars:>50` (niche matcher filters noise).

---

## Implementation order (follow exactly)

1. ~~`app/models.py` — add unique constraint~~ ✅
2. ~~`app/config.py` + `.env.example` — new settings~~ ✅
3. ~~`app/features/niche_builder.py` — `sync_niches_from_yaml()` + `NicheMatcher`~~ ✅
4. ~~`app/ingestion/base.py` — `BaseConnector`, `ConnectorRunRegistry`, `NormalizedItem`, `RunStatus`, retry helper~~ ✅
5. ~~`scripts/seed_mock_data.py` + `app/ingestion/appstore_mock_connector.py` (no network)~~ ✅
6. ~~`app/ingestion/hn_connector.py` (no auth, simplest real HTTP)~~ ✅
7. ~~`app/ingestion/github_connector.py`~~ ✅
8. ~~`app/ingestion/reddit_connector.py`~~ ✅
9. ~~`app/ingestion/scheduler.py`~~ ✅
10. ~~`app/main.py` — lifespan integration~~ ✅
11. ~~`app/bot/handlers.py` (`/sources`) + `app/bot/formatter.py` (escape helper)~~ ✅
12. ~~`scripts/run_ingestion.py`~~ ✅
13. ~~Tests~~ ✅
14. ~~`README.md` updates~~ ✅

---

## Step-by-step spec

### Step 1 — `app/models.py`

Add to `SourceItem` (import `UniqueConstraint` from `sqlalchemy`):

```python
__table_args__ = (
    UniqueConstraint("source_type", "external_id", name="uq_source_items_source_external"),
)
```

---

### Step 2 — `app/config.py` + `.env.example`

Add to `Settings` (after existing Data Sources block):

```python
# Ingestion behaviour
reddit_subreddits: list[str] = [
    "startups", "SideProject", "Entrepreneur",
    "reactnative", "androiddev", "iOSProgramming",
]
github_star_threshold: int = 50
github_search_lookback_days: int = 14
ingestion_http_timeout_s: float = 20.0
ingestion_job_timeout_s: float = 180.0

@field_validator("reddit_subreddits", mode="before")
@classmethod
def parse_subreddits(cls, v: object) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, list):
        return [str(x) for x in v]
    return []
```

Add to `.env.example`:
```env
REDDIT_SUBREDDITS=startups,SideProject,Entrepreneur,reactnative,androiddev,iOSProgramming
GITHUB_STAR_THRESHOLD=50
GITHUB_SEARCH_LOOKBACK_DAYS=14
INGESTION_HTTP_TIMEOUT_S=20.0
INGESTION_JOB_TIMEOUT_S=180.0
```

---

### Step 3 — `app/features/niche_builder.py`

```python
import re
from pathlib import Path
import yaml
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.db import get_session
from app.models import Niche

async def sync_niches_from_yaml(path: Path) -> int:
    """Upsert niches from YAML into DB. Returns count of niches synced."""
    data = yaml.safe_load(path.read_text())
    niches = data.get("niches", [])
    async with get_session() as session:
        for n in niches:
            stmt = sqlite_insert(Niche).values(
                slug=n["slug"],
                name=n["name"],
                summary=n.get("summary"),
                category=n.get("category"),
                keywords_json=n.get("keywords", []),
            ).on_conflict_do_update(
                index_elements=["slug"],
                set_={
                    "name": n["name"],
                    "summary": n.get("summary"),
                    "category": n.get("category"),
                    "keywords_json": n.get("keywords", []),
                },
            )
            await session.execute(stmt)
        await session.commit()
    return len(niches)


class NicheMatcher:
    def __init__(self, patterns: dict[int, re.Pattern]) -> None:
        self._patterns = patterns

    @classmethod
    async def from_db(cls) -> "NicheMatcher":
        """Load all niches from DB and precompile keyword regex patterns."""
        patterns: dict[int, re.Pattern] = {}
        async with get_session() as session:
            result = await session.execute(select(Niche))
            niches = result.scalars().all()
        for niche in niches:
            keywords: list[str] = niche.keywords_json or []
            if not keywords:
                continue
            escaped = [re.escape(kw) for kw in sorted(keywords, key=len, reverse=True)]
            pattern = re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)
            patterns[niche.id] = pattern
        return cls(patterns)

    def match(self, title: str | None, body: str | None) -> int | None:
        """Return niche_id with most keyword hits. Ties broken by lowest id. None if no hits."""
        text = f"{title or ''}\n{body or ''}"
        best_id: int | None = None
        best_count = 0
        for niche_id, pattern in self._patterns.items():
            count = len(pattern.findall(text))
            if count > best_count or (count == best_count and count > 0 and (best_id is None or niche_id < best_id)):
                best_count = count
                best_id = niche_id
        return best_id if best_count > 0 else None
```

---

### Step 4 — `app/ingestion/base.py`

```python
import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar, Literal

import httpx
import structlog

from app.db import get_session
from app.models import SourceItem
from app.features.niche_builder import NicheMatcher
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

log = structlog.get_logger(__name__)


@dataclass
class NormalizedItem:
    source_type: str
    external_id: str
    title: str | None
    body: str | None
    url: str | None
    created_at: datetime | None
    metadata: dict | None = None


@dataclass
class RunStatus:
    source_type: str
    last_run_at: datetime | None = None
    last_status: Literal["never", "running", "ok", "error"] = "never"
    items_ingested: int = 0
    duration_s: float | None = None
    error: str | None = None


class ConnectorRunRegistry:
    def __init__(self) -> None:
        self._statuses: dict[str, RunStatus] = {}

    def mark_running(self, source_type: str) -> None:
        s = self._statuses.setdefault(source_type, RunStatus(source_type=source_type))
        s.last_status = "running"

    def mark_success(self, source_type: str, items: int, duration: float) -> None:
        s = self._statuses[source_type]
        s.last_status = "ok"
        s.last_run_at = datetime.now(UTC)
        s.items_ingested = items
        s.duration_s = duration
        s.error = None

    def mark_error(self, source_type: str, err: str, duration: float) -> None:
        s = self._statuses.setdefault(source_type, RunStatus(source_type=source_type))
        s.last_status = "error"
        s.last_run_at = datetime.now(UTC)
        s.duration_s = duration
        s.error = err

    def get(self, source_type: str) -> RunStatus:
        return self._statuses.get(source_type, RunStatus(source_type=source_type))

    def all(self) -> list[RunStatus]:
        return list(self._statuses.values())


class BaseConnector(ABC):
    source_type: ClassVar[str]

    def __init__(
        self,
        client: httpx.AsyncClient,
        matcher: NicheMatcher,
        registry: ConnectorRunRegistry,
    ) -> None:
        self.client = client
        self.matcher = matcher
        self.registry = registry
        self.log = structlog.get_logger(self.__class__.__name__)

    @abstractmethod
    async def fetch(self) -> list[dict]:
        ...

    @abstractmethod
    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        ...

    async def save(self, items: list[NormalizedItem]) -> int:
        if not items:
            return 0
        async with get_session() as session:
            rows = [
                {
                    "source_type": item.source_type,
                    "external_id": item.external_id,
                    "title": item.title,
                    "body": item.body,
                    "url": item.url,
                    "created_at": item.created_at,
                    "niche_id": self.matcher.match(item.title, item.body),
                    "metadata_json": item.metadata,
                }
                for item in items
            ]
            stmt = sqlite_insert(SourceItem).values(rows).on_conflict_do_nothing(
                index_elements=["source_type", "external_id"]
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def run(self) -> RunStatus:
        self.registry.mark_running(self.source_type)
        start = time.monotonic()
        try:
            raw = await self.fetch()
            items = self.normalize(raw)
            inserted = await self.save(items)
            duration = time.monotonic() - start
            self.registry.mark_success(self.source_type, inserted, duration)
            self.log.info(
                "Ingestion complete",
                component=self.__class__.__name__,
                source_type=self.source_type,
                status="ok",
                items_ingested=inserted,
                duration_ms=int(duration * 1000),
            )
        except Exception as exc:
            duration = time.monotonic() - start
            self.registry.mark_error(self.source_type, str(exc), duration)
            self.log.error(
                "Ingestion failed",
                component=self.__class__.__name__,
                source_type=self.source_type,
                status="error",
                error=str(exc),
                duration_ms=int(duration * 1000),
            )
        return self.registry.get(self.source_type)

    async def _request_with_retry(
        self, method: str, url: str, **kwargs
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self.client.request(method, url, **kwargs)
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

---

### Step 5 — Mock seed data + AppStore connector

**`scripts/seed_mock_data.py`**

Write six JSON files into `data/mock/`. Each file: list of app records. Apps must have titles/descriptions that contain keywords from `data/niches.yaml` so the matcher attaches them. Use `random.seed(42)` for numeric fields.

Record shape:
```json
{
  "external_id": "appstore-wellness-001",
  "title": "ZenStreak",
  "description": "AI habit tracker with adaptive streaks and ai wellness coaching",
  "category": "wellness",
  "growth_index": 0.42,
  "install_proxy": 12500,
  "rating": 4.6,
  "review_sentiment": 0.71,
  "competitor_density": 3,
  "updated_at": "2026-04-20T00:00:00Z"
}
```

Six categories: `wellness`, `finance`, `devtools`, `productivity`, `education`, `entertainment`. 4–6 apps per file.

**`app/ingestion/appstore_mock_connector.py`**

```python
from pathlib import Path
from datetime import datetime, UTC
from app.ingestion.base import BaseConnector, NormalizedItem
from app.config import get_settings

class AppStoreMockConnector(BaseConnector):
    source_type = "appstore"

    def __init__(self, *args, mock_dir: Path = Path("data/mock"), **kwargs):
        super().__init__(*args, **kwargs)
        self.mock_dir = mock_dir

    async def fetch(self) -> list[dict]:
        settings = get_settings()
        if not settings.enable_mock_appstore:
            return []
        records = []
        for path in sorted(self.mock_dir.glob("appstore_*.json")):
            import json
            records.extend(json.loads(path.read_text()))
        return records

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        items = []
        for r in raw:
            items.append(NormalizedItem(
                source_type=self.source_type,
                external_id=r["external_id"],
                title=r["title"],
                body=r["description"],
                url=r.get("url"),
                created_at=datetime.fromisoformat(r["updated_at"].rstrip("Z")).replace(tzinfo=UTC),
                metadata={
                    k: r[k] for k in
                    ["category","growth_index","install_proxy","rating","review_sentiment","competitor_density","updated_at"]
                    if k in r
                },
            ))
        return items
```

---

### Step 6 — `app/ingestion/hn_connector.py`

```python
from datetime import UTC, datetime, timedelta
from app.ingestion.base import BaseConnector, NormalizedItem
from app.config import get_settings

class HNConnector(BaseConnector):
    source_type = "hn"
    _BASE = "https://hn.algolia.com/api/v1/search_by_date"

    async def fetch(self) -> list[dict]:
        settings = get_settings()
        since = datetime.now(UTC) - timedelta(hours=6)
        since_epoch = int(since.timestamp())
        resp = await self._request_with_retry(
            "GET",
            self._BASE,
            params={
                "tags": "story",
                "numericFilters": f"created_at_i>{since_epoch}",
                "hitsPerPage": 200,
            },
        )
        return resp.json().get("hits", [])

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        items = []
        for hit in raw:
            oid = hit.get("objectID")
            if not oid:
                continue
            items.append(NormalizedItem(
                source_type=self.source_type,
                external_id=str(oid),
                title=hit.get("title"),
                body=hit.get("story_text") or hit.get("url") or "",
                url=f"https://news.ycombinator.com/item?id={oid}",
                created_at=datetime.fromtimestamp(hit["created_at_i"], tz=UTC)
                    if "created_at_i" in hit else None,
                metadata={
                    "points": hit.get("points"),
                    "num_comments": hit.get("num_comments"),
                    "author": hit.get("author"),
                },
            ))
        return items
```

---

### Step 7 — `app/ingestion/github_connector.py`

```python
import structlog
from datetime import UTC, datetime, timedelta
from app.ingestion.base import BaseConnector, NormalizedItem
from app.config import get_settings

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
                "q": f"stars:>{settings.github_star_threshold}+pushed:>{since}",
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
```

---

### Step 8 — `app/ingestion/reddit_connector.py`

```python
import asyncio
from datetime import UTC, datetime
from app.ingestion.base import BaseConnector, NormalizedItem
from app.config import get_settings

class RedditConnector(BaseConnector):
    source_type = "reddit"

    async def fetch(self) -> list[dict]:
        settings = get_settings()
        headers = {"User-Agent": settings.reddit_user_agent}
        posts = []
        for sub in settings.reddit_subreddits:
            resp = await self._request_with_retry(
                "GET",
                f"https://www.reddit.com/r/{sub}/new.json",
                headers=headers,
                params={"limit": 50},
            )
            children = resp.json().get("data", {}).get("children", [])
            posts.extend(children)
            await asyncio.sleep(1)  # polite delay between subreddits
        return posts

    def normalize(self, raw: list[dict]) -> list[NormalizedItem]:
        items = []
        for child in raw:
            d = child.get("data", {})
            name = d.get("name")  # t3_* fullname — globally unique
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
            ))
        return items
```

---

### Step 9 — `app/ingestion/scheduler.py`

```python
import asyncio
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from app.ingestion.base import BaseConnector, ConnectorRunRegistry
from app.config import Settings

log = structlog.get_logger(__name__)


def build_scheduler(
    connectors: list[BaseConnector],
    registry: ConnectorRunRegistry,
    settings: Settings,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    connector_map = {c.source_type: c for c in connectors}

    def _make_job(source_type: str):
        async def _job():
            connector = connector_map[source_type]
            try:
                await asyncio.wait_for(connector.run(), timeout=settings.ingestion_job_timeout_s)
            except asyncio.TimeoutError:
                registry.mark_error(source_type, "job timed out", settings.ingestion_job_timeout_s)
                log.error("Ingestion job timed out", source_type=source_type)
            except Exception as exc:
                log.error("Ingestion job crashed", source_type=source_type, error=str(exc))
        return _job

    scheduler.add_job(_make_job("github"), IntervalTrigger(hours=6), id="ingest_github", max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_make_job("hn"), IntervalTrigger(hours=6), id="ingest_hn", max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_make_job("reddit"), IntervalTrigger(hours=12), id="ingest_reddit", max_instances=1, coalesce=True, misfire_grace_time=300)
    scheduler.add_job(_make_job("appstore"), CronTrigger(hour=7, minute=0), id="ingest_appstore", max_instances=1, coalesce=True, misfire_grace_time=300)

    log.info("Scheduler built", component="scheduler", jobs=list(connector_map.keys()))
    return scheduler
```

---

### Step 10 — `app/main.py` lifespan

Replace the existing lifespan with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _configure_logging()
    log = structlog.get_logger("main")
    settings = get_settings()

    await init_db()
    log.info("Database initialised", component="main")

    from pathlib import Path
    from app.features.niche_builder import sync_niches_from_yaml, NicheMatcher
    count = await sync_niches_from_yaml(Path("data/niches.yaml"))
    log.info("Niches synced", component="main", count=count)

    import httpx
    from app.ingestion.base import ConnectorRunRegistry
    from app.ingestion.github_connector import GithubConnector
    from app.ingestion.hn_connector import HNConnector
    from app.ingestion.reddit_connector import RedditConnector
    from app.ingestion.appstore_mock_connector import AppStoreMockConnector
    from app.ingestion.scheduler import build_scheduler

    http_client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s)
    registry = ConnectorRunRegistry()
    matcher = await NicheMatcher.from_db()

    connectors = [
        GithubConnector(http_client, matcher, registry),
        HNConnector(http_client, matcher, registry),
        RedditConnector(http_client, matcher, registry),
        AppStoreMockConnector(http_client, matcher, registry),
    ]

    bot_app = None
    if settings.telegram_bot_token:
        from app.bot.bot import build_application
        bot_app = build_application()
        bot_app.bot_data["run_registry"] = registry
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling()
        log.info("Telegram bot started", component="main")
    else:
        log.warning("TELEGRAM_BOT_TOKEN not set — bot disabled", component="main")

    scheduler = build_scheduler(connectors, registry, settings)
    scheduler.start()
    log.info("Scheduler started", component="main")

    yield

    scheduler.shutdown(wait=False)
    if bot_app is not None:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        log.info("Telegram bot stopped", component="main")
    await http_client.aclose()
```

---

### Step 11 — `app/bot/formatter.py` + `app/bot/handlers.py`

**`app/bot/formatter.py`** — add escape helper:

```python
import re

_SPECIAL = r'\\_*[]()~`>#+-=|{}.!'

def md_escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    return re.sub(r'([\\\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])', r'\\\1', str(text))
```

**`app/bot/handlers.py`** — replace `sources_handler`:

```python
async def sources_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return

    from datetime import UTC
    from sqlalchemy import func, select
    from app.db import get_session
    from app.models import SourceItem
    from app.ingestion.base import ConnectorRunRegistry, RunStatus
    from app.bot.formatter import md_escape

    registry: ConnectorRunRegistry | None = context.application.bot_data.get("run_registry")
    source_types = ["github", "hn", "reddit", "appstore"]
    lines = ["*Sources — last ingestion status*\n"]

    for st in source_types:
        status: RunStatus = registry.get(st) if registry else None

        if status is None or status.last_status == "never":
            # Fall back to DB
            async with get_session() as session:
                row = await session.execute(
                    select(func.max(SourceItem.ingested_at), func.count(SourceItem.id))
                    .where(SourceItem.source_type == st)
                )
                max_at, count = row.one()
            if max_at:
                ts = md_escape(max_at.strftime("%Y-%m-%d %H:%M UTC"))
                lines.append(f"*{md_escape(st)}* — DB: {count} items, last at {ts}")
            else:
                lines.append(f"*{md_escape(st)}* — never run")
        else:
            emoji = {"ok": "✅", "error": "⚠️", "running": "🔄"}.get(status.last_status, "❓")
            ts = md_escape(status.last_run_at.strftime("%Y-%m-%d %H:%M UTC")) if status.last_run_at else "unknown"
            dur = f"{status.duration_s:.1f}s" if status.duration_s else "—"
            line = f"{emoji} *{md_escape(st)}* — {status.items_ingested} items in {md_escape(dur)} at {ts}"
            if status.error:
                line += f"\n  _{md_escape(status.error[:80])}_"
            lines.append(line)

    await update.effective_message.reply_text("\n".join(lines), parse_mode="MarkdownV2")
```

---

### Step 12 — `scripts/run_ingestion.py`

```python
#!/usr/bin/env python3
"""Manual ingestion runner. Usage: python -m scripts.run_ingestion --source <name|all>"""
import argparse, asyncio
from pathlib import Path

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["github","hn","reddit","appstore","all"], default="all")
    args = parser.parse_args()

    from app.db import init_db
    from app.features.niche_builder import sync_niches_from_yaml, NicheMatcher
    from app.ingestion.base import ConnectorRunRegistry
    from app.ingestion.github_connector import GithubConnector
    from app.ingestion.hn_connector import HNConnector
    from app.ingestion.reddit_connector import RedditConnector
    from app.ingestion.appstore_mock_connector import AppStoreMockConnector
    import httpx
    from app.config import get_settings

    settings = get_settings()
    await init_db()
    await sync_niches_from_yaml(Path("data/niches.yaml"))

    client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s)
    registry = ConnectorRunRegistry()
    matcher = await NicheMatcher.from_db()

    all_connectors = {
        "github": GithubConnector(client, matcher, registry),
        "hn": HNConnector(client, matcher, registry),
        "reddit": RedditConnector(client, matcher, registry),
        "appstore": AppStoreMockConnector(client, matcher, registry),
    }

    targets = list(all_connectors.values()) if args.source == "all" else [all_connectors[args.source]]

    for connector in targets:
        print(f"\nRunning {connector.source_type}...")
        status = await connector.run()
        print(f"  status={status.last_status} items={status.items_ingested} duration={status.duration_s:.1f}s")
        if status.error:
            print(f"  error: {status.error}")

    await client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
```

---

### Step 13 — Tests

**`tests/test_connectors.py`** — key test cases:

1. `test_base_connector_happy_path` — fake subclass, 3 items → 3 rows in DB.
2. `test_base_connector_idempotent` — run twice → still 3 rows.
3. `test_base_connector_attaches_niche_id` — seed a Niche + keyword; item title matches → `niche_id` set.
4. `test_base_connector_retry_on_429` — `httpx.MockTransport` returning 429 then 200.
5. `test_github_connector_parses_payload` — fixture `tests/fixtures/github_search.json`.
6. `test_hn_connector_parses_payload` — fixture `tests/fixtures/hn_search.json`.
7. `test_reddit_connector_loops_subreddits` — MockTransport, verify N requests for N subs.
8. `test_appstore_mock_connector_reads_tmp_dir` — `tmp_path` fixture.

Use `httpx.MockTransport` (built-in, no extra dep). Pattern:
```python
transport = httpx.MockTransport(handler)
client = httpx.AsyncClient(transport=transport)
```

**`tests/test_niche_builder.py`** (new file):
1. `test_sync_niches_from_yaml_upserts` — run twice, still same count.
2. `test_matcher_picks_highest_hits`.
3. `test_matcher_returns_none_when_no_hits`.

**`tests/test_bot_handlers.py`** — extend with:
- `test_sources_handler_empty_registry` — cold start, empty DB → "never run".
- `test_sources_handler_ok_status` — seed registry → formatted status lines.

**`tests/fixtures/`** — small JSON payloads (3–5 items) for GitHub, HN, Reddit.

---

### Step 14 — `README.md`

Add a **Data Sources** section covering:
- Reddit User-Agent format: `DevTrend/1.0 (by /u/yourhandle)` — required by Reddit ToS.
- GitHub: token optional; anonymous = 60 req/h (enough for 1 page/6h).
- How to run: `python -m scripts.seed_mock_data` and `python -m scripts.run_ingestion --source hn`.

---

## Verification

```bash
# Unit tests
pytest tests/test_connectors.py -v
pytest tests/test_niche_builder.py -v
pytest tests/test_bot_handlers.py -v
pytest   # full suite — M1 tests must still pass

# Seed mock data
python -m scripts.seed_mock_data
ls data/mock/   # 6 appstore_*.json files

# Smoke test connectors
python -m scripts.run_ingestion --source appstore
python -m scripts.run_ingestion --source hn

# DB check
sqlite3 devtrend.db \
  "SELECT source_type, COUNT(*), COUNT(niche_id) AS attached FROM source_items GROUP BY source_type;"

# Full process (optional — needs Telegram token)
uvicorn app.main:app
# Then: /sources in Telegram → shows "never run" initially
```

### KANBAN acceptance checks
- [ ] M2-01: `BaseConnector` with abstract `fetch/normalize`, concrete `save/run`
- [ ] M2-02: GitHub connector ingests from `api.github.com`
- [ ] M2-03: HN connector ingests from `hn.algolia.com`
- [ ] M2-04: Reddit connector ingests per-subreddit via public JSON
- [ ] M2-05: App Store mock reads `data/mock/*.json`
- [ ] M2-06: ≥6 category JSON files in `data/mock/`
- [ ] M2-07: `source_items.niche_id` populated for matching items
- [ ] M2-08: `AsyncIOScheduler` in lifespan, four jobs at correct intervals
- [ ] M2-09: `/sources` command returns per-connector last-run info in MarkdownV2

---

## Risks

- **Reddit 429s**: if they appear in production, revisit OAuth (already in P15-05 backlog).
- **GitHub anonymous**: safe at 1 page/6h within 60 req/h limit; tests must use `MockTransport` (offline).
- **Mock data coverage**: ensure every seeded app record hits ≥1 niche keyword; add an assertion in `test_appstore_mock_connector` for this.
- **`NicheMatcher.from_db()`**: must be called once per process — not inside each connector run.

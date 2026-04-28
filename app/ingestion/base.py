import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal, cast

import httpx
import structlog

from app.db import get_session
from app.features.niche_builder import NicheMatcher
from app.models import SourceItem
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult

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
    async def fetch(self, since: datetime | None = None) -> list[dict]:
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
            result = cast(CursorResult[Any], await session.execute(stmt))
            await session.commit()
            return result.rowcount

    async def run(self, since: datetime | None = None) -> RunStatus:
        self.registry.mark_running(self.source_type)
        start = time.monotonic()
        try:
            raw = await self.fetch(since=since)
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

import pytest
pytest.skip("v3 — references deleted ORM entities; deferred to Plan C", allow_module_level=True)

from pathlib import Path

from app.db import get_session, init_db
from app.features.niche_builder import NicheMatcher, sync_niches_from_yaml
from app.models import Niche
from sqlalchemy import select


class TestSyncNichesFromYaml:
    async def test_upserts_idempotent(self):
        await init_db()
        count1 = await sync_niches_from_yaml(Path("data/niches.yaml"))
        count2 = await sync_niches_from_yaml(Path("data/niches.yaml"))
        assert count1 == count2
        async with get_session() as session:
            result = await session.execute(select(Niche))
            db_count = len(result.scalars().all())
        assert db_count == count1


class TestNicheMatcher:
    async def _seed_niche(self, slug: str, keywords: list[str]) -> int:
        async with get_session() as session:
            niche = Niche(slug=slug, name=slug, keywords_json=keywords)
            session.add(niche)
            await session.commit()
            await session.refresh(niche)
            return niche.id

    async def test_picks_highest_hits(self):
        await init_db()
        id_a = await self._seed_niche("niche-a", ["habit tracker", "streak"])
        id_b = await self._seed_niche("niche-b", ["local llm"])

        matcher = await NicheMatcher.from_db()
        # "habit tracker" hits twice in title+body for niche-a, only 0 for niche-b
        result = matcher.match("My habit tracker app", "Best habit tracker with streak support")
        assert result == id_a

    async def test_returns_none_when_no_hits(self):
        await init_db()
        await self._seed_niche("niche-c", ["habit tracker"])
        matcher = await NicheMatcher.from_db()
        result = matcher.match("Totally unrelated title", "Nothing matching here at all")
        assert result is None

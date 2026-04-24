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

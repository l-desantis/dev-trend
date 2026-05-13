from pathlib import Path

import structlog
import yaml
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category

log = structlog.get_logger(__name__)

_DEFAULT_PATH = Path(__file__).parent.parent.parent / "data" / "categories.yaml"


async def sync_categories_from_yaml(
    session: AsyncSession,
    path: Path = _DEFAULT_PATH,
) -> None:
    """Read categories.yaml and upsert each row into the categories table.

    Existing rows are updated by slug; rows not in the YAML are not deleted
    (preserves FK integrity for any candidates already assigned to them).
    Logs categories_synced count=N once.
    """
    data = yaml.safe_load(path.read_text())
    categories = data.get("categories", [])
    for cat in categories:
        stmt = (
            pg_insert(Category)
            .values(
                slug=cat["slug"],
                name=cat["name"],
                description=cat.get("description"),
            )
            .on_conflict_do_update(
                index_elements=["slug"],
                set_={
                    "name": cat["name"],
                    "description": cat.get("description"),
                },
            )
        )
        await session.execute(stmt)
    await session.commit()
    log.info("categories_synced", count=len(categories))

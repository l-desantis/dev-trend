"""Schema migration: add embedding_model and merged_into_id columns (v4.2).

Run with:
    uv run python scripts/migrate_to_v4_2.py --confirm

Safe to run multiple times (uses ALTER TABLE ... IF NOT EXISTS via
exception-swallowing — SQLite does not support IF NOT EXISTS on ALTER TABLE).
"""
import argparse
import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings


async def migrate(database_url: str) -> None:
    engine = create_async_engine(database_url, echo=False)

    alterations = [
        # PainPoint — embedding model tracking
        "ALTER TABLE pain_points ADD COLUMN embedding_model VARCHAR(150)",
        # OpportunityCandidate — embedding model + merge chain
        "ALTER TABLE opportunity_candidates ADD COLUMN embedding_model VARCHAR(150)",
        "ALTER TABLE opportunity_candidates ADD COLUMN merged_into_id INTEGER REFERENCES opportunity_candidates(id)",
        # v4.D: honest labelling timestamp for relabel heuristic
        "ALTER TABLE opportunity_candidates ADD COLUMN last_labelled_at DATETIME",
    ]

    async with engine.begin() as conn:
        for stmt in alterations:
            try:
                await conn.execute(text(stmt))
                print(f"  ✅  {stmt[:70]}")
            except Exception as exc:
                if "duplicate column" in str(exc).lower() or "already exists" in str(exc).lower():
                    print(f"  ⏭   already exists, skipping: {stmt[:70]}")
                else:
                    print(f"  ❌  {stmt[:70]}: {exc}")
                    raise

    await engine.dispose()
    print("\nMigration complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate DB schema to v4.2")
    parser.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args()

    if not args.confirm:
        print("Pass --confirm to run the migration.")
        sys.exit(1)

    settings = get_settings()
    asyncio.run(migrate(settings.database_url))


if __name__ == "__main__":
    main()

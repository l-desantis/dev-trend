"""Manual smoke-test: run the agent graph against the live DB.

Usage:
  python scripts/run_agent.py            # Runs for all niches with MockLLMAdapter
  python scripts/run_agent.py --ollama   # Use OllamaAdapter (requires running Ollama + qwen2.5)
  python scripts/run_agent.py --niche ai-habit-trackers
"""
import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session, init_db
from app.agents.graph import run_brief_for_niche
from app.features.niche_builder import sync_niches_from_yaml
from app.llm.mock_adapter import MockLLMAdapter
from app.llm.ollama_adapter import OllamaAdapter
from app.models import Niche


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama", action="store_true", help="Use OllamaAdapter")
    parser.add_argument("--niche", help="Specific niche slug (default: all)")
    args = parser.parse_args()

    await init_db()
    await sync_niches_from_yaml(Path("data/niches.yaml"))

    if args.ollama:
        s = get_settings()
        adapter = OllamaAdapter(base_url=s.ollama_base_url, model=s.ollama_model)
    else:
        adapter = MockLLMAdapter()

    async with get_session() as session:
        stmt = select(Niche.id, Niche.slug)
        if args.niche:
            stmt = stmt.where(Niche.slug == args.niche)
        niches = (await session.execute(stmt)).all()

    now = datetime.now(UTC)
    for nid, slug in niches:
        try:
            brief_id = await run_brief_for_niche(nid, adapter, as_of=now, triggered_by="command")
            print(f"  {slug}: brief_id={brief_id}")
        except Exception as exc:
            print(f"  {slug}: FAILED — {exc}")


if __name__ == "__main__":
    asyncio.run(main())

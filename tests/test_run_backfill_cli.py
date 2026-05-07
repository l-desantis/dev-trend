"""CLI smoke test for scripts/run_backfill.py (A-22 coverage)."""
import pytest

from app.ingestion.backfill import BackfillReport
from app.ingestion.base import RunStatus
from app.ingestion.github_connector import GithubConnector
from app.ingestion.hn_connector import HNConnector
from app.ingestion.reddit_connector import RedditConnector
from app.models import SourceItem


def test_run_backfill_cli_returns_report_with_painpoints(
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() returns a BackfillReport and the pipeline creates ≥1 PainPoint."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'backfill_test.db'}"

    async def _fake_run(self, since=None, until=None) -> RunStatus:
        from app.db import get_session

        async with get_session() as session:
            session.add(
                SourceItem(
                    source_type=self.source_type,
                    external_id=f"{self.source_type}-cli-001",
                    title="I wish there was a habit tracker for ADHD adults",
                    role="extraction",
                )
            )
            await session.commit()
        return RunStatus(source_type=self.source_type, last_status="ok", items_ingested=1)

    monkeypatch.setattr(GithubConnector, "run", _fake_run)
    monkeypatch.setattr(HNConnector, "run", _fake_run)
    monkeypatch.setattr(RedditConnector, "run", _fake_run)

    from scripts.run_backfill import main

    report = main(
        [
            "--history-days", "1",
            "--llm-provider", "mock",
            "--embedding-provider", "mock",
            "--db-url", db_url,
        ]
    )

    assert isinstance(report, BackfillReport)
    assert report.painpoints_created >= 1


def test_run_backfill_cli_dry_run_skips_pipeline(
    tmp_path,
    monkeypatch,
) -> None:
    """--dry-run ingests but does NOT create pain points; report carries an estimate."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dryrun.db'}"

    async def _fake_run(self, since=None, until=None) -> RunStatus:
        from app.db import get_session
        async with get_session() as session:
            session.add(SourceItem(
                source_type=self.source_type,
                external_id=f"{self.source_type}-dry-001",
                title="I wish there was a habit tracker",
                role="extraction",
            ))
            await session.commit()
        return RunStatus(source_type=self.source_type, last_status="ok", items_ingested=1)

    monkeypatch.setattr(GithubConnector, "run", _fake_run)
    monkeypatch.setattr(HNConnector, "run", _fake_run)
    monkeypatch.setattr(RedditConnector, "run", _fake_run)

    from scripts.run_backfill import main

    report = main([
        "--history-days", "1",
        "--llm-provider", "mock",
        "--embedding-provider", "mock",
        "--db-url", db_url,
        "--dry-run",
    ])

    assert isinstance(report, BackfillReport)
    assert report.painpoints_created == 0       # pipeline did NOT run
    assert report.estimate is not None
    assert report.estimate.extract.calls >= 3   # one per fake-ingested item
    assert report.estimate.total_tokens > 0

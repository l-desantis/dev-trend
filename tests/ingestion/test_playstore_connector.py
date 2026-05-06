"""Tests for PlayStoreReviewsConnector."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from app.ingestion.base import ConnectorRunRegistry
from app.ingestion.playstore_connector import PlayStoreReviewsConnector, _to_normalized


def _make_connector() -> PlayStoreReviewsConnector:
    client = MagicMock(spec=httpx.AsyncClient)
    registry = ConnectorRunRegistry()
    return PlayStoreReviewsConnector(client=client, registry=registry)


def _fake_review(review_id: str = "r1", score: int = 3, days_old: int = 0) -> dict:
    from datetime import timedelta
    at = datetime(2026, 5, 1, tzinfo=UTC) - timedelta(days=days_old)
    return {
        "reviewId": review_id,
        "userName": "TestUser",
        "userImage": "",
        "content": "Great app!",
        "score": score,
        "thumbsUpCount": 5,
        "reviewCreatedVersion": "1.0",
        "at": at,
        "replyContent": None,
        "repliedAt": None,
        "appVersion": "1.0.0",
        "_app_id": "com.example.app",
    }


def test_to_normalized_produces_extraction_role() -> None:
    raw = _fake_review()
    item = _to_normalized("com.example.app", raw)
    assert item.role == "extraction"
    assert item.source_type == "playstore"
    assert item.external_id == "r1"
    assert "play.google.com" in (item.url or "")


def test_normalize_dedupes_by_external_id() -> None:
    connector = _make_connector()
    reviews = [_fake_review("same-id"), _fake_review("same-id")]
    items = connector.normalize(reviews)
    assert len(items) == 1


def test_normalize_respects_since() -> None:
    """Reviews older than since should be filtered during fetch (not normalize)."""
    connector = _make_connector()
    old = _fake_review("old", days_old=60)
    new = _fake_review("new", days_old=0)
    # normalize itself doesn't filter by since — fetch does; both pass normalize
    items = connector.normalize([old, new])
    assert len(items) == 2


@pytest.mark.asyncio
async def test_playstore_fetch_filters_by_since() -> None:
    connector = _make_connector()
    old_review = _fake_review("old", days_old=60)
    new_review = _fake_review("new", days_old=0)

    from app.models import TrackedApp
    from datetime import UTC, datetime
    mock_app = MagicMock(spec=TrackedApp)
    mock_app.app_id = "com.example.app"

    since = datetime(2026, 4, 20, tzinfo=UTC)

    with (
        patch("app.ingestion.playstore_connector.get_session") as mock_gs,
        patch("app.ingestion.playstore_connector.asyncio.to_thread") as mock_thread,
        patch("app.ingestion.playstore_connector.asyncio.sleep", new=AsyncMock()),
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_app]))))
        )
        mock_gs.return_value = mock_session
        mock_thread.return_value = ([old_review, new_review], None)

        result = await connector.fetch(since=since)

    # old review (60 days ago) should be filtered out
    assert all(r["reviewId"] != "old" for r in result)
    assert any(r["reviewId"] == "new" for r in result)


@pytest.mark.asyncio
async def test_playstore_continues_on_app_failure() -> None:
    connector = _make_connector()
    app1 = MagicMock()
    app1.app_id = "com.failing.app"
    app2 = MagicMock()
    app2.app_id = "com.working.app"

    good_review = _fake_review("good-review")

    with (
        patch("app.ingestion.playstore_connector.get_session") as mock_gs,
        patch("app.ingestion.playstore_connector.asyncio.to_thread") as mock_thread,
        patch("app.ingestion.playstore_connector.asyncio.sleep", new=AsyncMock()),
    ):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[app1, app2])))
            )
        )
        mock_gs.return_value = mock_session

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("network error")
            return ([good_review], None)

        mock_thread.side_effect = side_effect
        result = await connector.fetch()

    # Should have continued to app2 after app1 failed
    assert any(r.get("reviewId") == "good-review" for r in result)

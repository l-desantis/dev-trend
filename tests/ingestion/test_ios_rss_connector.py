"""Tests for iOS RSS connector."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ingestion.base import ConnectorRunRegistry
from app.ingestion.ios_rss_connector import IosRssReviewsConnector


def _make_connector() -> IosRssReviewsConnector:
    client = MagicMock(spec=httpx.AsyncClient)
    registry = ConnectorRunRegistry()
    return IosRssReviewsConnector(client=client, registry=registry)


@pytest.mark.asyncio
async def test_ios_rss_disabled_by_flag() -> None:
    from app.config import Settings
    connector = _make_connector()

    with patch("app.ingestion.ios_rss_connector.get_settings") as mock_settings:
        mock_settings.return_value = Settings.model_construct(enable_ios_rss=False)
        result = await connector.fetch()

    assert result == []


@pytest.mark.asyncio
async def test_ios_rss_happy_path() -> None:
    connector = _make_connector()
    mock_app = MagicMock()
    mock_app.ios_app_id = "12345"

    fake_entry = {
        "id": {"label": "rev-1"},
        "title": {"label": "Great app"},
        "content": {"label": "Really love it"},
        "im:rating": {"label": "5"},
        "updated": {"label": "2026-05-01T10:00:00Z"},
    }

    with (
        patch("app.ingestion.ios_rss_connector.get_settings") as mock_settings,
        patch("app.ingestion.ios_rss_connector.get_session") as mock_gs,
        patch.object(connector, "_request_with_retry") as mock_req,
        patch("app.ingestion.ios_rss_connector.asyncio.sleep", new=AsyncMock()),
    ):
        from app.config import Settings
        mock_settings.return_value = Settings.model_construct(enable_ios_rss=True)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[mock_app]))
                )
            )
        )
        mock_gs.return_value = mock_session

        # First page returns one entry, second returns empty (terminates)
        def make_response(entries):
            r = MagicMock()
            r.json.return_value = {"feed": {"entry": entries}}
            return r

        mock_req.side_effect = [
            make_response([fake_entry]),
            make_response([]),  # terminates pagination
        ]

        raw = await connector.fetch()

    assert len(raw) == 1
    items = connector.normalize(raw)
    assert len(items) == 1
    assert items[0].source_type == "ios_appstore"
    assert items[0].external_id == "12345:rev-1"

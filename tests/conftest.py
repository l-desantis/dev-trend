import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import reset_engine


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("BACKFILL_ON_EMPTY", "false")
    get_settings.cache_clear()
    reset_engine()


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

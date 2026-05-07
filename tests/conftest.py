import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import reset_engine

# Python 3.12 deprecated the built-in sqlite3 datetime adapter; register an explicit one.
sqlite3.register_adapter(datetime, lambda d: d.isoformat())


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

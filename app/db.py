from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        from app.config import get_settings
        _engine = create_async_engine(get_settings().database_url, echo=False)
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(_get_engine(), expire_on_commit=False)
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with _get_session_factory()() as session:
        yield session


async def check_db_reachable() -> None:
    """Fail fast if the database is unreachable; rely on Alembic for schema."""
    from sqlalchemy import text
    async with _get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


def reset_engine() -> None:
    """Reset cached engine and session factory. Intended for use in tests only."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None

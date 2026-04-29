"""Tests for app/bot/v4_handlers.py"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    CandidateScoreHistory,
    Category,
    OpportunityCandidate,
    SourceItem,
    PainPoint,
)


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _make_update(text: str = "/opportunities") -> MagicMock:
    update = MagicMock()
    update.effective_message = AsyncMock()
    update.effective_message.reply_text = AsyncMock()
    return update


def _make_ctx(args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


async def _seed_candidate(
    session: AsyncSession,
    *,
    score: float = 50.0,
    specificity: int = 3,
    is_archived: bool = False,
    lifecycle_state: str | None = None,
    category_id: int | None = None,
) -> OpportunityCandidate:
    c = OpportunityCandidate(
        problem_statement=f"problem s={score}",
        specificity=specificity,
        is_archived=is_archived,
        lifecycle_state=lifecycle_state,
        category_id=category_id,
    )
    session.add(c)
    await session.flush()

    row = CandidateScoreHistory(
        candidate_id=c.id,
        score_total=score,
        score_breakdown_json={
            "frequency": {"raw": 5, "score": 50},
            "momentum": {"raw": 0.3, "score": 50},
            "source_diversity": {"raw": 2, "score": 50},
            "validation": 70,
            "specificity": 60,
            "weights": {"frequency": 0.25, "momentum": 0.30, "source_diversity": 0.15, "validation": 0.20, "specificity": 0.10},
        },
        scored_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    return c


async def test_opportunities_returns_top_n(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    for score in [10.0, 90.0, 50.0, 70.0, 30.0]:
        await _seed_candidate(session, score=score)

    update = _make_update()
    ctx = _make_ctx()

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_opportunities
        await cmd_opportunities(update, ctx)

    update.effective_message.reply_text.assert_called_once()
    call_args = update.effective_message.reply_text.call_args
    text = call_args[0][0] if call_args[0] else call_args[1].get("text", "")
    assert "problem" in text.lower() or "#1" in text


async def test_opportunities_handles_empty_db(monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    update = _make_update()
    ctx = _make_ctx()

    async with factory() as session:
        with patch("app.bot.v4_handlers.get_session") as mock_gs:
            mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.bot.v4_handlers import cmd_opportunities
            await cmd_opportunities(update, ctx)

    reply_text = update.effective_message.reply_text.call_args[0][0]
    assert "No opportunities" in reply_text or "pipeline" in reply_text.lower()

    await engine.dispose()


async def test_opportunities_excludes_archived(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_candidate(session, score=99.0, is_archived=True)
    await _seed_candidate(session, score=50.0)

    update = _make_update()
    ctx = _make_ctx()

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_opportunities
        await cmd_opportunities(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "s\\=99" not in text


async def test_opportunity_unknown_id_returns_friendly_error(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    update = _make_update()
    ctx = _make_ctx(["9999"])

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_opportunity
        await cmd_opportunity(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "not found" in text.lower() or "Candidate" in text


async def test_opportunity_below_gate_shows_warning(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    c = await _seed_candidate(session, score=40.0, specificity=2)

    update = _make_update()
    ctx = _make_ctx([str(c.id)])

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_opportunity
        await cmd_opportunity(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "specificity threshold" in text.lower() or "below" in text.lower()


async def test_categories_command_lists_all(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    cat = Category(slug="devtools", name="Devtools")
    session.add(cat)
    await session.flush()

    await _seed_candidate(session, score=80.0, category_id=cat.id)
    await _seed_candidate(session, score=60.0, category_id=cat.id, lifecycle_state="hot")

    update = _make_update("/categories")
    ctx = _make_ctx()

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_categories
        await cmd_categories(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "Devtools" in text
    assert "2" in text  # 2 active


async def test_emerging_filters_by_state(session: AsyncSession, monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    await _seed_candidate(session, score=80.0, lifecycle_state="emerging")
    await _seed_candidate(session, score=70.0, lifecycle_state="hot")

    update = _make_update("/emerging")
    ctx = _make_ctx()

    with patch("app.bot.v4_handlers.get_session") as mock_gs:
        mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.bot.v4_handlers import cmd_emerging
        await cmd_emerging(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    # MarkdownV2 escapes '=' to '\=' and '.' to '\.' in the output
    assert "s\\=80" in text
    assert "s\\=70" not in text


async def test_emerging_empty_returns_friendly_message(monkeypatch) -> None:
    monkeypatch.setenv("SPECIFICITY_GATE", "2")
    from app.config import get_settings
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    update = _make_update()
    ctx = _make_ctx()

    async with factory() as session:
        with patch("app.bot.v4_handlers.get_session") as mock_gs:
            mock_gs.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_gs.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.bot.v4_handlers import cmd_emerging
            await cmd_emerging(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "No emerging" in text or "emerging" in text.lower()

    await engine.dispose()

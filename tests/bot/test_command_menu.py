"""Tests for v4 command menu registration."""
from unittest.mock import MagicMock

from telegram.ext import Application, CommandHandler

from app.bot.handlers import _HELP_TEXT, register_command_handlers


def test_set_my_commands_includes_v4_set() -> None:
    app = MagicMock(spec=Application)
    register_command_handlers(app)

    registered = [
        cmd
        for call in app.add_handler.call_args_list
        if isinstance(call.args[0], CommandHandler)
        for cmd in call.args[0].commands
    ]

    v4_commands = ["opportunities", "opportunity", "categories", "category", "emerging"]
    for cmd in v4_commands:
        assert cmd in registered, f"{cmd!r} not registered"

    # Old v3 commands must not be present
    for cmd in ("briefing", "niches", "niche", "trending"):
        assert cmd not in registered, f"v3 command {cmd!r} should not be registered"


def test_help_text_lists_v4_commands() -> None:
    assert "/opportunities" in _HELP_TEXT
    assert "/opportunity" in _HELP_TEXT
    assert "/categories" in _HELP_TEXT
    assert "/category" in _HELP_TEXT
    assert "/emerging" in _HELP_TEXT
    assert "coming soon" not in _HELP_TEXT.lower()

"""Regression guards for bot handler registration.

The 'view:' callback handler was missing originally — that bug silently
broke every 📄 details button. This test makes sure it stays wired.
"""
from __future__ import annotations

from unittest.mock import patch

from telegram.ext import Application, CallbackQueryHandler

from app.bot.handlers import register_command_handlers


def _matches(handler: CallbackQueryHandler, sample: str) -> bool:
    pattern = handler.pattern
    if pattern is None:
        return False
    return pattern.match(sample) is not None


def test_view_callback_handler_is_registered() -> None:
    # Application.builder().token(...).build() requires a non-empty token
    # but does not contact Telegram, so a dummy string is fine.
    app = Application.builder().token("0:dummy").build()
    register_command_handlers(app)

    callback_handlers = [
        h
        for group in app.handlers.values()
        for h in group
        if isinstance(h, CallbackQueryHandler)
    ]

    assert any(_matches(h, "view:42:none") for h in callback_handlers), (
        "Expected a CallbackQueryHandler whose pattern matches 'view:...'"
    )
    # Sanity: existing feedback handler must remain registered too.
    assert any(_matches(h, "fb:up:1:none") for h in callback_handlers)

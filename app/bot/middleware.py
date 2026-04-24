import structlog

from telegram import Update
from telegram.ext import Application, ApplicationHandlerStop, ContextTypes, TypeHandler

from app.config import get_settings

_log = structlog.get_logger("middleware")


async def _allowlist_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not update.effective_chat:
        raise ApplicationHandlerStop
    _log.debug(
        "allowlist_check",
        chat_id=update.effective_chat.id,
        allowed=settings.telegram_allowed_chat_ids,
    )
    if update.effective_chat.id not in settings.telegram_allowed_chat_ids:
        if update.effective_message:
            await update.effective_message.reply_text(
                "This bot is private. Access is restricted."
            )
        raise ApplicationHandlerStop


def register_allowlist(application: Application) -> None:
    # group=-1 fires before all command handlers (registered at group 0)
    application.add_handler(TypeHandler(Update, _allowlist_check), group=-1)

from telegram.ext import Application

from app.bot.handlers import register_command_handlers
from app.bot.middleware import register_allowlist
from app.config import get_settings


def build_application() -> Application:
    settings = get_settings()
    application = Application.builder().token(settings.telegram_bot_token).build()
    register_allowlist(application)
    register_command_handlers(application)
    return application

import re

_SPECIAL = r'\\_*[]()~`>#+-=|{}.!'


def md_escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    return re.sub(r'([\\\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])', r'\\\1', str(text))

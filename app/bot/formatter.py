from __future__ import annotations

import re

_TREND_ARROWS = {"Rising": "↑", "Stable": "→", "Declining": "↓"}
_MD_SPECIAL = re.compile(r'([\\\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])')


def md_escape(text: str) -> str:
    return _MD_SPECIAL.sub(r'\\\1', str(text))


def bold(text: str) -> str:
    return f"*{md_escape(text)}*"


def trend_arrow(label: str) -> str:
    return _TREND_ARROWS.get(label, "→")


def source_badge(source_type: str) -> str:
    return md_escape(f"[{source_type}]")


def format_score(score: float) -> str:
    return str(int(score + 0.5))


def truncate(text: str, max_len: int, footer: str = "…") -> str:
    if len(text) <= max_len:
        return text
    cut = max_len - len(footer)
    if cut < 0:
        return footer[:max_len]
    return text[:cut] + footer

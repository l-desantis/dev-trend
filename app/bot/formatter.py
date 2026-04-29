from __future__ import annotations

import re

_TREND_ARROWS = {"Rising": "↑", "Stable": "→", "Declining": "↓"}
_MD_SPECIAL = re.compile(r'([\\\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])')

_LIFECYCLE_LABELS = {
    "emerging": "🌱 Emerging",
    "hot": "🔥 Hot",
    "saturated": "🛑 Saturated",
    "dormant": "💤 Dormant",
}


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


def lifecycle_arrow(state: str | None) -> str:
    return _LIFECYCLE_LABELS.get(state or "", "")


def score_breakdown_block(breakdown: dict) -> str:
    """MarkdownV2-safe score breakdown inside a code fence."""
    if not breakdown:
        return "```\nNo breakdown available.\n```"

    def _row(label: str, raw: object, score: object) -> str:
        raw_str = f"{raw}" if raw is not None else "—"
        score_str = f"{int(float(score) + 0.5):3d}/100" if score is not None else "—/100"
        return f"{label:<18} {raw_str:>8} · {score_str}"

    freq = breakdown.get("frequency", {})
    mom = breakdown.get("momentum", {})
    div = breakdown.get("source_diversity", {})
    val = breakdown.get("validation")
    spec = breakdown.get("specificity")
    total = sum(
        (breakdown.get(k, {}).get("score", 0) if isinstance(breakdown.get(k), dict) else (breakdown.get(k) or 0)) * w
        for k, w in (breakdown.get("weights") or {}).items()
    ) if breakdown.get("weights") else None

    rows = [
        _row("Frequency", freq.get("raw"), freq.get("score")),
        _row("Momentum", mom.get("raw"), mom.get("score")),
        _row("Diversity", div.get("raw"), div.get("score")),
        _row("Validation", None, val),
        _row("Specificity", None, spec),
        "─" * 28,
        _row("Total", None, int(total + 0.5) if total is not None else None),
    ]
    return "```\n" + "\n".join(rows) + "\n```"

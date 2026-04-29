from app.bot.formatter import (
    bold,
    format_score,
    lifecycle_arrow,
    md_escape,
    score_breakdown_block,
    source_badge,
    trend_arrow,
    truncate,
)


def test_md_escape_handles_all_specials():
    assert md_escape("hello.world!") == r"hello\.world\!"


def test_bold_escapes_inner_text():
    assert bold("a.b") == r"*a\.b*"


def test_trend_arrow_known_labels():
    assert trend_arrow("Rising") == "↑"
    assert trend_arrow("Stable") == "→"
    assert trend_arrow("Declining") == "↓"


def test_trend_arrow_unknown_label_falls_back():
    assert trend_arrow("Unknown") == "→"


def test_source_badge_returns_escaped_tag():
    # `[` and `]` must be escaped under MarkdownV2
    assert source_badge("github") == r"\[github\]"


def test_format_score_rounds_and_pads():
    assert format_score(84.49) == "84"
    assert format_score(84.50) == "85"


def test_truncate_keeps_text_under_limit():
    assert truncate("abc", 10) == "abc"


def test_truncate_appends_footer_when_too_long():
    out = truncate("a" * 50, 20)
    assert len(out) <= 20
    assert out.endswith("…")


def test_truncate_supports_custom_footer():
    out = truncate("a" * 50, 20, footer="… more")
    assert out.endswith("… more")
    assert len(out) <= 20


def test_lifecycle_arrow_known_states():
    assert lifecycle_arrow("emerging") == "🌱 Emerging"
    assert lifecycle_arrow("hot") == "🔥 Hot"
    assert lifecycle_arrow("saturated") == "🛑 Saturated"
    assert lifecycle_arrow("dormant") == "💤 Dormant"


def test_lifecycle_arrow_unknown_returns_empty():
    assert lifecycle_arrow(None) == ""
    assert lifecycle_arrow("unknown") == ""


def test_score_breakdown_block_format():
    breakdown = {
        "frequency": {"raw": 12, "score": 65},
        "momentum": {"raw": 0.4, "score": 80},
        "source_diversity": {"raw": 4, "score": 70},
        "validation": 90,
        "specificity": 80,
        "weights": {"frequency": 0.25, "momentum": 0.30, "source_diversity": 0.15, "validation": 0.20, "specificity": 0.10},
    }
    block = score_breakdown_block(breakdown)
    assert "```" in block
    assert "Frequency" in block
    assert "Momentum" in block
    assert "65" in block

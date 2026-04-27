# Milestone 5 — Full Telegram Bot — Implementation Plan

> **Date:** 2026-04-27
> **Milestone:** M5 (Full Telegram Bot)
> **Executor note:** This plan is intended to be executed outside the planning session. Each task is TDD-structured (failing test → implementation → passing test → commit). Follow steps in order.

---

## Context

M4 closed the agent loop: a daily 03:00 UTC `_brief_job` runs the LangGraph (fetcher → retriever → forecaster → reporter → reviewer) for every niche and persists `OpportunityBrief` rows with denormalised evidence + score breakdowns. The scoring job at 02:15 UTC populates `NicheScoreHistory` daily.

M5 lights the **user-facing surface**: it implements the four real bot commands (`/briefing`, `/niches`, `/niche <slug>`, `/trending`), builds the MarkdownV2 formatter helpers they share, and wires two scheduler-driven push flows — the **daily digest** (08:00 UTC) and the **spike alert** (chained after the scoring job).

Today, `app/bot/handlers.py:25-105` contains seven handlers. Six (everything except `/sources`) are stubbed with `_COMING_SOON`. `app/bot/notifications.py` and `app/bot/scheduler_hooks.py` are empty. `app/bot/formatter.py` has only `md_escape()`.

**Design decisions (locked in this plan):**

1. **Digest + spike alerts target every chat in `telegram_allowed_chat_ids`.** Not just the legacy `telegram_chat_id`. KANBAN M5-06 says "pushed to allowed chat IDs"; the allowlist is the bot's identity boundary. If the list is empty, no push fires (logged at WARNING).
2. **Replace `daily_digest_time` string with explicit `digest_cron_hour` / `digest_cron_minute` int settings** to match the existing pattern (`scoring_cron_hour`, `brief_cron_hour`). The string version was declared but never parsed; removing it now is safer than building a parser.
3. **Spike alert is chained inside `_scoring_job`, not a separate cron.** A separate cron at "scoring + 15 min" is fragile: if scoring overruns, the alert reads stale data. The single coroutine reads what it just wrote — race-free.
4. **`/trending` ranks niches by 24h-window mention-count delta.** Count `SourceItem` rows per `niche_id` in `[now-24h, now)` minus count in `[now-48h, now-24h)`; sort by delta DESC; take top N. Per-niche aggregation matches the project's vocabulary; per-source-item ranking would be noisy and requires no additional joins. Niches with zero current-window count are excluded.
5. **Trend label sourced from `OpportunityBrief.forecast_label`** (set by the forecaster node from rolling-slope sign). For `/niches` rows where no brief exists yet (cold start), default to `"Stable"`. Avoids re-deriving slope per command.
6. **Programmatic headlines, deterministic formatting.** All bot output is built from DB rows + fixed templates — no LLM calls in handlers or push paths. The only LLM-generated text is `OpportunityBrief.summary`, written by the M4 reporter node and rendered verbatim (escaped).
7. **Bot reference passed into `build_scheduler()`.** The scheduler needs to send messages; we add a `bot: telegram.Bot | None` parameter and `app/main.py` passes `bot_app.bot`. If bot is None (no token configured), digest + spike-alert jobs are skipped at registration time.
8. **Truncate at 4096 chars with a `…` footer.** All push and command output go through `formatter.truncate(text, 4096)`. The `/niche <slug>` command appends `…\n_(use /briefing for the full brief)_` if truncated.

**Already done:**
- `app/bot/middleware.py:11-30` — allowlist enforcement at group=-1.
- `app/bot/handlers.py:55-94` — `/sources` (DB query + registry pattern, our template).
- `app/bot/formatter.py:1-8` — `md_escape()` (foundation).
- `app/main.py:64-76` — bot/scheduler lifecycle inside FastAPI lifespan.
- `app/ingestion/scheduler.py:56-119` — `_scoring_job`, `_brief_job` patterns to extend.
- `app/agents/graph.py:_persist_brief` — daily-deduped brief persistence.
- M4 produces `OpportunityBrief.forecast_label`, `score_total`, `score_breakdown_json`, `evidence_json` — everything M5 needs to render.

---

## File Structure

**Create:**
- `tests/test_formatter.py`
- `tests/test_notifications.py`
- `tests/test_scheduler_hooks.py`

**Modify:**
- `app/config.py` — add digest cron hour/minute, top-N + window settings; remove `daily_digest_time` string.
- `app/bot/formatter.py` — add `bold`, `trend_arrow`, `source_badge`, `truncate`, `format_score`.
- `app/bot/handlers.py` — implement `briefing_handler`, `niches_handler`, `niche_handler`, `trending_handler`.
- `app/bot/notifications.py` — implement `build_daily_digest()`, `build_spike_alert()`.
- `app/bot/scheduler_hooks.py` — implement `push_daily_digest()`, `push_spike_alerts()`.
- `app/ingestion/scheduler.py` — accept `bot`; chain spike alert into `_scoring_job`; register `_digest_job`.
- `app/main.py` — pass `bot_app.bot` into `build_scheduler`.
- `tests/test_bot_handlers.py` — extend with tests for the four new handlers.
- `KANBAN.md` — flip M5-01 … M5-07 to Done.
- `docs/decisions.md` — append ADR-006 (digest model + spike-alert chaining).

**Untouched (referenced):**
- `app/bot/bot.py:1-14` — `build_application()`. No change.
- `app/bot/middleware.py:11-30` — allowlist. No change.
- `app/models.py:13-107` — Niche, NicheScoreHistory, OpportunityBrief, NicheSignal, SourceItem.
- `app/agents/graph.py:run_brief_for_niche` — not called from M5.
- `app/forecasting/scoring.py:score_all_niches` — called by `_scoring_job` (existing).

---

## Implementation Idioms (follow existing patterns)

- **DB session in handlers:** `async with get_session() as session: ...`. Mirror `sources_handler` at `app/bot/handlers.py:55`.
- **Reply formatting:** always escape user-visible strings with `formatter.md_escape()` before assembling MarkdownV2; reply with `parse_mode="MarkdownV2"`.
- **Tests:** `_make_update(chat_id)` + `mock_context` fixture pattern in `tests/test_bot_handlers.py`. Use `AsyncMock()` for `reply_text` assertions; `await init_db()` for any test that touches DB.
- **Structured logging:** `log = structlog.get_logger(__name__)`; emit `log.info("event", component="bot|notifications|scheduler_hooks", …)`.
- **Settings:** `from app.config import get_settings; settings = get_settings()`.
- **No LLM in M5.** Handlers and push builders read DB + render templates only.

---

## Task 1 — Config settings for digest, spike alert, and command output

**Files:**
- Modify: `app/config.py` (replace `daily_digest_time` block; extend with M5 cron + sizing settings)

- [x] **Step 1: Edit settings**

In `app/config.py`, locate `daily_digest_time: str = "08:00"` (around line 78) and `spike_alert_threshold: float = 15.0` (line 79). Replace `daily_digest_time` and add adjacent M5 settings:

```python
    # Daily digest push
    digest_cron_hour: int = 8
    digest_cron_minute: int = 0
    digest_top_n: int = 3

    # Spike alerts
    spike_alert_threshold: float = 15.0

    # /trending command
    trending_top_n: int = 5
    trending_window_hours: int = 24

    # /briefing command
    briefing_top_n: int = 3

    # Telegram message limit
    telegram_max_message_chars: int = 4096
```

Remove the existing `daily_digest_time: str = "08:00"` line.

- [x] **Step 2: Sanity check**

```bash
python -c "from app.config import get_settings; s = get_settings(); print(s.digest_cron_hour, s.digest_top_n, s.trending_window_hours)"
```
Expected: `8 3 24`

- [x] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat(config): add M5 digest/spike/trending settings"
```

---

## Task 2 — Formatter helpers (TDD)

The four new commands and two push flows all share the same MarkdownV2 building blocks. Build them once, test them once.

**Files:**
- Create: `tests/test_formatter.py`
- Modify: `app/bot/formatter.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_formatter.py`:

```python
from app.bot.formatter import (
    bold,
    format_score,
    md_escape,
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
```

- [x] **Step 2: Run — confirm failure**

```bash
pytest tests/test_formatter.py -v
```
Expected: ImportErrors for `bold`, `format_score`, `source_badge`, `trend_arrow`, `truncate`.

- [x] **Step 3: Implement**

Replace `app/bot/formatter.py` with:

```python
"""MarkdownV2 helpers for Telegram messages.

All user-visible strings must be escaped before assembly. Helpers
that emit MarkdownV2 syntax (e.g. `bold`) escape their inputs first
so callers can pass raw strings.
"""
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
    return f"{round(score):d}"


def truncate(text: str, max_len: int, footer: str = "…") -> str:
    if len(text) <= max_len:
        return text
    cut = max_len - len(footer)
    if cut < 0:
        return footer[:max_len]
    return text[:cut] + footer
```

- [x] **Step 4: Run — confirm pass**

```bash
pytest tests/test_formatter.py -v
```
Expected: 9 passed.

- [x] **Step 5: Commit**

```bash
git add app/bot/formatter.py tests/test_formatter.py
git commit -m "feat(bot): MarkdownV2 helpers (bold, trend_arrow, source_badge, truncate)"
```

---

## Task 3 — `/briefing` handler (TDD)

Top N (default 3) latest `OpportunityBrief` rows, ranked by `score_total DESC`, formatted in MarkdownV2 with niche name, score, trend arrow, and a 1-line summary excerpt.

**Files:**
- Modify: `app/bot/handlers.py`
- Modify: `tests/test_bot_handlers.py`

- [x] **Step 1: Write failing test**

Append to `tests/test_bot_handlers.py`:

```python
class TestBriefingHandler:
    async def test_briefing_returns_top_n_briefs(self, mock_context):
        from app.bot.handlers import briefing_handler
        from app.db import get_session, init_db
        from app.models import Niche, OpportunityBrief
        from datetime import datetime, timezone

        await init_db()
        async with get_session() as s:
            niche = Niche(name="X", slug="x", category="c", keywords_json=[])
            s.add(niche); await s.flush()
            s.add(OpportunityBrief(
                niche_id=niche.id,
                headline="X — Score 84",
                summary="Strong momentum.",
                score_total=84.0,
                score_breakdown_json={"growth": 90, "demand": 80, "novelty": 70},
                evidence_json=[],
                forecast_label="Rising",
                has_issues=False,
                generated_at=datetime.now(timezone.utc),
                model_name="qwen2.5",
            ))
            await s.commit()

        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await briefing_handler(update, mock_context)

        text = update.effective_message.reply_text.call_args.args[0]
        assert "84" in text
        assert "↑" in text
        assert "X" in text

    async def test_briefing_handles_no_briefs(self, mock_context):
        from app.bot.handlers import briefing_handler
        from app.db import init_db

        await init_db()
        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await briefing_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "no briefs" in text.lower() or "not yet" in text.lower()
```

- [x] **Step 2: Run — confirm failure**

```bash
pytest tests/test_bot_handlers.py::TestBriefingHandler -v
```
Expected: handler still returns `_COMING_SOON`; assertions fail.

- [x] **Step 3: Implement**

In `app/bot/handlers.py`, replace `briefing_handler` with:

```python
async def briefing_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Return top-N latest opportunity briefs, ranked by score_total."""
    from sqlalchemy import select
    from app.db import get_session
    from app.models import Niche, OpportunityBrief
    from app.bot.formatter import (
        bold, format_score, md_escape, trend_arrow, truncate,
    )

    settings = get_settings()
    async with get_session() as session:
        result = await session.execute(
            select(OpportunityBrief, Niche)
            .join(Niche, Niche.id == OpportunityBrief.niche_id)
            .order_by(OpportunityBrief.score_total.desc())
            .limit(settings.briefing_top_n)
        )
        rows = result.all()

    if not rows:
        await update.effective_message.reply_text(
            md_escape("No briefs yet — the agent will run at 03:00 UTC."),
            parse_mode="MarkdownV2",
        )
        return

    lines = [bold("DevTrend Briefing")]
    for i, (brief, niche) in enumerate(rows, start=1):
        arrow = trend_arrow(brief.forecast_label)
        lines.append(
            f"\n{i}\\. {bold(niche.name)} "
            f"\\| {bold(format_score(brief.score_total))} {arrow}\n"
            f"{md_escape(brief.summary)}"
        )

    text = truncate("\n".join(lines), settings.telegram_max_message_chars)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")
```

- [x] **Step 4: Run — confirm pass**

```bash
pytest tests/test_bot_handlers.py::TestBriefingHandler -v
```

- [x] **Step 5: Commit**

```bash
git add app/bot/handlers.py tests/test_bot_handlers.py
git commit -m "feat(bot): /briefing — top-N briefs in MarkdownV2"
```

---

## Task 4 — `/niches` handler (TDD)

List every tracked niche with its latest `score_total` and a trend label sourced from the latest `OpportunityBrief.forecast_label` (or `"Stable"` if no brief). Sort by score DESC.

**Files:**
- Modify: `app/bot/handlers.py`
- Modify: `tests/test_bot_handlers.py`

- [x] **Step 1: Write failing test**

Append to `tests/test_bot_handlers.py`:

```python
class TestNichesHandler:
    async def test_niches_lists_all_with_scores(self, mock_context):
        from app.bot.handlers import niches_handler
        from app.db import get_session, init_db
        from app.models import Niche, NicheScoreHistory
        from datetime import datetime, timezone

        await init_db()
        async with get_session() as s:
            n1 = Niche(name="Alpha", slug="alpha", category="c", keywords_json=[])
            n2 = Niche(name="Beta", slug="beta", category="c", keywords_json=[])
            s.add_all([n1, n2]); await s.flush()
            s.add_all([
                NicheScoreHistory(niche_id=n1.id, score_total=70.0,
                                  score_breakdown_json={}, scored_at=datetime.now(timezone.utc)),
                NicheScoreHistory(niche_id=n2.id, score_total=85.0,
                                  score_breakdown_json={}, scored_at=datetime.now(timezone.utc)),
            ])
            await s.commit()

        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await niches_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "Alpha" in text and "Beta" in text
        # Beta listed first (higher score)
        assert text.index("Beta") < text.index("Alpha")
```

- [x] **Step 2: Run — confirm failure**

```bash
pytest tests/test_bot_handlers.py::TestNichesHandler -v
```

- [x] **Step 3: Implement**

Replace `niches_handler` with:

```python
async def niches_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    from sqlalchemy import select, func
    from app.db import get_session
    from app.models import Niche, NicheScoreHistory, OpportunityBrief
    from app.bot.formatter import (
        bold, format_score, md_escape, trend_arrow, truncate,
    )

    settings = get_settings()

    async with get_session() as session:
        # Latest score per niche via subquery
        latest_score = (
            select(
                NicheScoreHistory.niche_id,
                func.max(NicheScoreHistory.scored_at).label("max_at"),
            )
            .group_by(NicheScoreHistory.niche_id)
            .subquery()
        )
        score_rows = await session.execute(
            select(NicheScoreHistory)
            .join(
                latest_score,
                (NicheScoreHistory.niche_id == latest_score.c.niche_id)
                & (NicheScoreHistory.scored_at == latest_score.c.max_at),
            )
        )
        scores = {r[0].niche_id: r[0].score_total for r in score_rows.all()}

        # Latest brief per niche → forecast_label
        latest_brief = (
            select(
                OpportunityBrief.niche_id,
                func.max(OpportunityBrief.generated_at).label("max_at"),
            )
            .group_by(OpportunityBrief.niche_id)
            .subquery()
        )
        brief_rows = await session.execute(
            select(OpportunityBrief)
            .join(
                latest_brief,
                (OpportunityBrief.niche_id == latest_brief.c.niche_id)
                & (OpportunityBrief.generated_at == latest_brief.c.max_at),
            )
        )
        labels = {r[0].niche_id: r[0].forecast_label for r in brief_rows.all()}

        niches = (await session.execute(select(Niche))).scalars().all()

    ranked = sorted(
        niches,
        key=lambda n: scores.get(n.id, 0.0),
        reverse=True,
    )

    if not ranked:
        await update.effective_message.reply_text(
            md_escape("No niches loaded."), parse_mode="MarkdownV2"
        )
        return

    lines = [bold("Tracked Niches")]
    for n in ranked:
        score = scores.get(n.id, 0.0)
        arrow = trend_arrow(labels.get(n.id, "Stable"))
        lines.append(
            f"{arrow} {bold(n.name)} \\| {bold(format_score(score))} "
            f"\\(`/niche {md_escape(n.slug)}`\\)"
        )

    text = truncate("\n".join(lines), settings.telegram_max_message_chars)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")
```

- [x] **Step 4: Run — confirm pass**

```bash
pytest tests/test_bot_handlers.py::TestNichesHandler -v
```

- [x] **Step 5: Commit**

```bash
git add app/bot/handlers.py tests/test_bot_handlers.py
git commit -m "feat(bot): /niches — ranked niche list with trend"
```

---

## Task 5 — `/niche <slug>` handler (TDD)

Full scorecard for one niche: latest `OpportunityBrief` (headline, summary, evidence) plus per-dimension breakdown from the brief's `score_breakdown_json`. Truncate to 4096 with custom footer.

**Files:**
- Modify: `app/bot/handlers.py`
- Modify: `tests/test_bot_handlers.py`

- [x] **Step 1: Write failing test**

Append to `tests/test_bot_handlers.py`:

```python
class TestNicheHandler:
    async def test_niche_returns_full_scorecard(self, mock_context):
        from app.bot.handlers import niche_handler
        from app.db import get_session, init_db
        from app.models import Niche, OpportunityBrief
        from datetime import datetime, timezone

        await init_db()
        async with get_session() as s:
            n = Niche(name="Alpha", slug="alpha", category="c",
                      keywords_json=[], summary="An alpha niche.")
            s.add(n); await s.flush()
            s.add(OpportunityBrief(
                niche_id=n.id, headline="Alpha — Score 80",
                summary="Strong week.", score_total=80.0,
                score_breakdown_json={"growth": 85, "demand": 78, "novelty": 75},
                evidence_json=[{"source_type": "github", "title": "repo-x",
                                "url": "https://x", "excerpt": "a repo"}],
                forecast_label="Rising", has_issues=False,
                generated_at=datetime.now(timezone.utc), model_name="qwen2.5",
            ))
            await s.commit()

        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        mock_context.args = ["alpha"]
        await niche_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "Alpha" in text
        assert "80" in text
        assert "Growth" in text or "growth" in text
        assert "repo-x" in text

    async def test_niche_unknown_slug(self, mock_context):
        from app.bot.handlers import niche_handler
        from app.db import init_db

        await init_db()
        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        mock_context.args = ["does-not-exist"]
        await niche_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0].lower()
        assert "not found" in text or "unknown" in text

    async def test_niche_no_args(self, mock_context):
        from app.bot.handlers import niche_handler
        from app.db import init_db

        await init_db()
        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        mock_context.args = []
        await niche_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0].lower()
        assert "usage" in text or "/niche" in text
```

- [x] **Step 2: Run — confirm failure**

```bash
pytest tests/test_bot_handlers.py::TestNicheHandler -v
```

- [x] **Step 3: Implement**

Replace `niche_handler` with:

```python
async def niche_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    from sqlalchemy import select
    from app.db import get_session
    from app.models import Niche, OpportunityBrief
    from app.bot.formatter import (
        bold, format_score, md_escape, trend_arrow, truncate,
    )

    settings = get_settings()
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            md_escape("Usage: /niche <slug>  (try /niches for the list)"),
            parse_mode="MarkdownV2",
        )
        return

    slug = args[0].strip().lower()

    async with get_session() as session:
        niche = (await session.execute(
            select(Niche).where(Niche.slug == slug)
        )).scalar_one_or_none()

        if niche is None:
            await update.effective_message.reply_text(
                md_escape(f"Niche '{slug}' not found."),
                parse_mode="MarkdownV2",
            )
            return

        brief = (await session.execute(
            select(OpportunityBrief)
            .where(OpportunityBrief.niche_id == niche.id)
            .order_by(OpportunityBrief.generated_at.desc())
            .limit(1)
        )).scalar_one_or_none()

    if brief is None:
        await update.effective_message.reply_text(
            f"{bold(niche.name)}\n{md_escape('No brief yet.')}",
            parse_mode="MarkdownV2",
        )
        return

    breakdown = brief.score_breakdown_json or {}
    arrow = trend_arrow(brief.forecast_label)
    lines = [
        f"{bold(niche.name)} {arrow}",
        f"{bold('Score')}: {bold(format_score(brief.score_total))}",
        f"  Growth: {format_score(breakdown.get('growth', 0))}",
        f"  Demand: {format_score(breakdown.get('demand', 0))}",
        f"  Novelty: {format_score(breakdown.get('novelty', 0))}",
        "",
        md_escape(brief.summary),
    ]

    evidence = brief.evidence_json or []
    if evidence:
        lines.append("")
        lines.append(bold("Evidence"))
        for e in evidence[:5]:
            title = md_escape(e.get("title", "(untitled)"))
            url = md_escape(e.get("url", ""))
            stype = md_escape(e.get("source_type", "?"))
            lines.append(f"\\- \\[{stype}\\] {title} {url}")

    text = truncate(
        "\n".join(lines),
        settings.telegram_max_message_chars,
        footer="…\n_\\(truncated\\)_",
    )
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")
```

- [x] **Step 4: Run — confirm pass**

```bash
pytest tests/test_bot_handlers.py::TestNicheHandler -v
```

- [x] **Step 5: Commit**

```bash
git add app/bot/handlers.py tests/test_bot_handlers.py
git commit -m "feat(bot): /niche <slug> — full scorecard + evidence"
```

---

## Task 6 — `/trending` handler (TDD)

Top N niches by 24h-window mention-count delta. Window = last 24h; baseline = preceding 24h. Excludes niches with zero current-window count.

**Files:**
- Modify: `app/bot/handlers.py`
- Modify: `tests/test_bot_handlers.py`

- [x] **Step 1: Write failing test**

Append to `tests/test_bot_handlers.py`:

```python
class TestTrendingHandler:
    async def test_trending_ranks_by_delta(self, mock_context):
        from app.bot.handlers import trending_handler
        from app.db import get_session, init_db
        from app.models import Niche, SourceItem
        from datetime import datetime, timedelta, timezone

        await init_db()
        now = datetime.now(timezone.utc)
        async with get_session() as s:
            n1 = Niche(name="Hot", slug="hot", category="c", keywords_json=[])
            n2 = Niche(name="Cold", slug="cold", category="c", keywords_json=[])
            s.add_all([n1, n2]); await s.flush()

            def make_item(niche_id, when, ext):
                return SourceItem(
                    source_type="hn", external_id=ext, title=f"t-{ext}",
                    body="", url="https://x", created_at=when,
                    ingested_at=when, niche_id=niche_id, metadata_json={},
                )

            # Hot: 5 in last 24h, 1 prior 24h → delta +4
            for i in range(5):
                s.add(make_item(n1.id, now - timedelta(hours=2), f"h{i}"))
            s.add(make_item(n1.id, now - timedelta(hours=30), "h-prev"))
            # Cold: 1 in last 24h, 3 prior 24h → delta -2
            s.add(make_item(n2.id, now - timedelta(hours=3), "c1"))
            for i in range(3):
                s.add(make_item(n2.id, now - timedelta(hours=30), f"c-prev-{i}"))
            await s.commit()

        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await trending_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0]
        assert "Hot" in text
        # Hot listed before Cold (higher delta)
        if "Cold" in text:
            assert text.index("Hot") < text.index("Cold")

    async def test_trending_no_signals(self, mock_context):
        from app.bot.handlers import trending_handler
        from app.db import init_db

        await init_db()
        update = _make_update(chat_id=42)
        update.effective_message.reply_text = AsyncMock()
        await trending_handler(update, mock_context)
        text = update.effective_message.reply_text.call_args.args[0].lower()
        assert "no" in text and ("trending" in text or "signal" in text)
```

- [x] **Step 2: Run — confirm failure**

```bash
pytest tests/test_bot_handlers.py::TestTrendingHandler -v
```

- [x] **Step 3: Implement**

Replace `trending_handler` with:

```python
async def trending_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, func
    from app.db import get_session
    from app.models import Niche, SourceItem
    from app.bot.formatter import bold, md_escape, truncate

    settings = get_settings()
    now = datetime.now(timezone.utc)
    window = timedelta(hours=settings.trending_window_hours)
    cur_start, prior_start = now - window, now - 2 * window

    async with get_session() as session:
        cur = dict((await session.execute(
            select(SourceItem.niche_id, func.count(SourceItem.id))
            .where(SourceItem.ingested_at >= cur_start)
            .where(SourceItem.niche_id.is_not(None))
            .group_by(SourceItem.niche_id)
        )).all())
        prior = dict((await session.execute(
            select(SourceItem.niche_id, func.count(SourceItem.id))
            .where(SourceItem.ingested_at >= prior_start)
            .where(SourceItem.ingested_at < cur_start)
            .where(SourceItem.niche_id.is_not(None))
            .group_by(SourceItem.niche_id)
        )).all())
        niches = {n.id: n for n in
                  (await session.execute(select(Niche))).scalars().all()}

    ranked = []
    for nid, count in cur.items():
        if count == 0 or nid not in niches:
            continue
        delta = count - prior.get(nid, 0)
        ranked.append((delta, count, niches[nid]))
    ranked.sort(key=lambda r: r[0], reverse=True)
    ranked = ranked[: settings.trending_top_n]

    if not ranked:
        await update.effective_message.reply_text(
            md_escape("No trending signals in the last 24h."),
            parse_mode="MarkdownV2",
        )
        return

    lines = [bold(f"Trending — last {settings.trending_window_hours}h")]
    for delta, count, niche in ranked:
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"{bold(niche.name)} \\| "
            f"{md_escape(str(count))} mentions "
            f"\\({md_escape(sign + str(delta))} vs prior\\)"
        )

    text = truncate("\n".join(lines), settings.telegram_max_message_chars)
    await update.effective_message.reply_text(text, parse_mode="MarkdownV2")
```

- [x] **Step 4: Run — confirm pass**

```bash
pytest tests/test_bot_handlers.py::TestTrendingHandler -v
```

- [x] **Step 5: Commit**

```bash
git add app/bot/handlers.py tests/test_bot_handlers.py
git commit -m "feat(bot): /trending — top niches by 24h count delta"
```

---

## Task 7 — Notification builders (TDD)

Pure functions: read DB rows → return MarkdownV2 strings. No bot, no scheduler. Easier to test than the push pipeline itself.

**Files:**
- Create: `tests/test_notifications.py`
- Modify: `app/bot/notifications.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_notifications.py`:

```python
import pytest
from datetime import datetime, timedelta, timezone

from app.db import get_session, init_db


@pytest.fixture
async def setup_db():
    await init_db()


class TestDailyDigest:
    async def test_digest_includes_top_n_briefs(self, setup_db):
        from app.bot.notifications import build_daily_digest
        from app.models import Niche, OpportunityBrief

        async with get_session() as s:
            n = Niche(name="Habits", slug="habits", category="c", keywords_json=[])
            s.add(n); await s.flush()
            s.add(OpportunityBrief(
                niche_id=n.id, headline="Habits — 84",
                summary="Up.", score_total=84.0,
                score_breakdown_json={}, evidence_json=[],
                forecast_label="Rising", has_issues=False,
                generated_at=datetime.now(timezone.utc), model_name="qwen2.5",
            ))
            await s.commit()

        text = await build_daily_digest()
        assert text is not None
        assert "Habits" in text
        assert "84" in text
        assert "↑" in text

    async def test_digest_returns_none_when_empty(self, setup_db):
        from app.bot.notifications import build_daily_digest
        text = await build_daily_digest()
        assert text is None


class TestSpikeAlert:
    async def test_spike_alert_message_includes_delta(self, setup_db):
        from app.bot.notifications import build_spike_alert
        from app.models import Niche

        async with get_session() as s:
            n = Niche(name="Boom", slug="boom", category="c", keywords_json=[])
            s.add(n); await s.commit()
            await s.refresh(n)
            text = build_spike_alert(
                niche=n, today_score=80.0, prior_score=60.0,
            )
        assert "Boom" in text
        assert "80" in text
        assert "20" in text  # delta
```

- [x] **Step 2: Run — confirm failure**

```bash
pytest tests/test_notifications.py -v
```
Expected: ImportError — `notifications.py` is empty.

- [x] **Step 3: Implement**

Write `app/bot/notifications.py`:

```python
"""Push-notification builders for Telegram digests and spike alerts.

Pure functions: read from DB, return MarkdownV2 strings. Sending is
done by `app.bot.scheduler_hooks`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select

from app.config import get_settings
from app.bot.formatter import (
    bold, format_score, md_escape, trend_arrow, truncate,
)
from app.db import get_session
from app.models import Niche, OpportunityBrief


async def build_daily_digest() -> str | None:
    """Return MarkdownV2-formatted digest, or None if no briefs exist."""
    settings = get_settings()
    async with get_session() as session:
        rows = (await session.execute(
            select(OpportunityBrief, Niche)
            .join(Niche, Niche.id == OpportunityBrief.niche_id)
            .order_by(OpportunityBrief.score_total.desc())
            .limit(settings.digest_top_n)
        )).all()

    if not rows:
        return None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"🚀 {bold(f'DevTrend Daily Brief — {today}')}",
    ]
    for i, (brief, niche) in enumerate(rows, start=1):
        arrow = trend_arrow(brief.forecast_label)
        lines.append(
            f"\n{bold(f'#{i} — {niche.name}')} {arrow} "
            f"\\| {bold(format_score(brief.score_total))}\n"
            f"{md_escape(brief.summary)}"
        )

    return truncate("\n".join(lines), settings.telegram_max_message_chars)


def build_spike_alert(
    niche: Niche, today_score: float, prior_score: float
) -> str:
    delta = today_score - prior_score
    return "\n".join([
        f"⚡ {bold('Spike Alert')}",
        f"{bold(niche.name)}",
        f"Score: {bold(format_score(today_score))} "
        f"\\(was {format_score(prior_score)}, "
        f"\\+{format_score(delta)}\\)",
    ])
```

- [x] **Step 4: Run — confirm pass**

```bash
pytest tests/test_notifications.py -v
```

- [x] **Step 5: Commit**

```bash
git add app/bot/notifications.py tests/test_notifications.py
git commit -m "feat(bot): notification builders for digest + spike alert"
```

---

## Task 8 — Scheduler hooks (TDD)

Wraps notification builders with bot dispatch. Reads `telegram_allowed_chat_ids`; sends to each. On per-chat send failure, logs and continues (don't abort the whole job for one bad chat).

**Files:**
- Create: `tests/test_scheduler_hooks.py`
- Modify: `app/bot/scheduler_hooks.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_scheduler_hooks.py`:

```python
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone
import pytest

from app.db import get_session, init_db


@pytest.fixture
async def setup_db():
    await init_db()


class TestPushDailyDigest:
    async def test_pushes_to_all_allowed_chats(self, setup_db):
        from app.bot.scheduler_hooks import push_daily_digest
        from app.models import Niche, OpportunityBrief

        async with get_session() as s:
            n = Niche(name="X", slug="x", category="c", keywords_json=[])
            s.add(n); await s.flush()
            s.add(OpportunityBrief(
                niche_id=n.id, headline="h", summary="s", score_total=80.0,
                score_breakdown_json={}, evidence_json=[],
                forecast_label="Rising", has_issues=False,
                generated_at=datetime.now(timezone.utc), model_name="qwen2.5",
            ))
            await s.commit()

        bot = AsyncMock()
        with patch("app.bot.scheduler_hooks.get_settings") as mock_s:
            mock_s.return_value.telegram_allowed_chat_ids = [1, 2, 3]
            mock_s.return_value.digest_top_n = 3
            mock_s.return_value.telegram_max_message_chars = 4096
            await push_daily_digest(bot)
        assert bot.send_message.await_count == 3

    async def test_skips_when_no_briefs(self, setup_db):
        from app.bot.scheduler_hooks import push_daily_digest
        bot = AsyncMock()
        with patch("app.bot.scheduler_hooks.get_settings") as mock_s:
            mock_s.return_value.telegram_allowed_chat_ids = [1]
            mock_s.return_value.digest_top_n = 3
            mock_s.return_value.telegram_max_message_chars = 4096
            await push_daily_digest(bot)
        bot.send_message.assert_not_awaited()


class TestPushSpikeAlerts:
    async def test_pushes_when_delta_above_threshold(self, setup_db):
        from app.bot.scheduler_hooks import push_spike_alerts
        from app.models import Niche, NicheScoreHistory

        now = datetime.now(timezone.utc)
        async with get_session() as s:
            n = Niche(name="Spike", slug="spike", category="c", keywords_json=[])
            s.add(n); await s.flush()
            s.add_all([
                NicheScoreHistory(niche_id=n.id, score_total=50.0,
                                  score_breakdown_json={},
                                  scored_at=now - timedelta(days=1)),
                NicheScoreHistory(niche_id=n.id, score_total=80.0,
                                  score_breakdown_json={}, scored_at=now),
            ])
            await s.commit()

        bot = AsyncMock()
        with patch("app.bot.scheduler_hooks.get_settings") as mock_s:
            mock_s.return_value.telegram_allowed_chat_ids = [1]
            mock_s.return_value.spike_alert_threshold = 15.0
            mock_s.return_value.telegram_max_message_chars = 4096
            await push_spike_alerts(bot, as_of=now)
        bot.send_message.assert_awaited_once()

    async def test_skips_when_below_threshold(self, setup_db):
        from app.bot.scheduler_hooks import push_spike_alerts
        from app.models import Niche, NicheScoreHistory

        now = datetime.now(timezone.utc)
        async with get_session() as s:
            n = Niche(name="Tiny", slug="tiny", category="c", keywords_json=[])
            s.add(n); await s.flush()
            s.add_all([
                NicheScoreHistory(niche_id=n.id, score_total=70.0,
                                  score_breakdown_json={},
                                  scored_at=now - timedelta(days=1)),
                NicheScoreHistory(niche_id=n.id, score_total=72.0,
                                  score_breakdown_json={}, scored_at=now),
            ])
            await s.commit()

        bot = AsyncMock()
        with patch("app.bot.scheduler_hooks.get_settings") as mock_s:
            mock_s.return_value.telegram_allowed_chat_ids = [1]
            mock_s.return_value.spike_alert_threshold = 15.0
            mock_s.return_value.telegram_max_message_chars = 4096
            await push_spike_alerts(bot, as_of=now)
        bot.send_message.assert_not_awaited()

    async def test_no_op_when_bot_is_none(self, setup_db):
        from app.bot.scheduler_hooks import push_spike_alerts
        # Should not raise
        await push_spike_alerts(None)
```

- [x] **Step 2: Run — confirm failure**

```bash
pytest tests/test_scheduler_hooks.py -v
```

- [x] **Step 3: Implement**

Write `app/bot/scheduler_hooks.py`:

```python
"""Bridge: scheduler jobs → Telegram bot dispatch.

These functions are awaited by the scheduler. They:
  - read what the notification builders need from the DB
  - call builders to render MarkdownV2 strings
  - send to every chat in `telegram_allowed_chat_ids`
  - log per-chat failures but never abort the whole job
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from app.bot.notifications import build_daily_digest, build_spike_alert
from app.config import get_settings
from app.db import get_session
from app.models import Niche, NicheScoreHistory

log = structlog.get_logger(__name__)


async def push_daily_digest(bot) -> None:
    if bot is None:
        log.warning("digest_skipped", reason="no_bot")
        return
    settings = get_settings()
    chats = settings.telegram_allowed_chat_ids or []
    if not chats:
        log.warning("digest_skipped", reason="no_allowed_chats")
        return

    text = await build_daily_digest()
    if text is None:
        log.info("digest_skipped", reason="no_briefs")
        return

    for chat_id in chats:
        try:
            await bot.send_message(
                chat_id=chat_id, text=text, parse_mode="MarkdownV2"
            )
            log.info("digest_pushed", chat_id=chat_id, length=len(text))
        except Exception as exc:
            log.error("digest_push_failed", chat_id=chat_id, error=str(exc))


async def push_spike_alerts(bot, as_of: datetime | None = None) -> None:
    if bot is None:
        log.warning("spike_skipped", reason="no_bot")
        return
    settings = get_settings()
    chats = settings.telegram_allowed_chat_ids or []
    if not chats:
        log.warning("spike_skipped", reason="no_allowed_chats")
        return

    when = as_of or datetime.now(timezone.utc)
    today_start = when.replace(hour=0, minute=0, second=0, microsecond=0)

    alerts: list[str] = []
    async with get_session() as session:
        niches = (await session.execute(select(Niche))).scalars().all()
        for niche in niches:
            today = (await session.execute(
                select(NicheScoreHistory)
                .where(NicheScoreHistory.niche_id == niche.id)
                .where(NicheScoreHistory.scored_at >= today_start)
                .order_by(NicheScoreHistory.scored_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if today is None:
                continue
            prior = (await session.execute(
                select(NicheScoreHistory)
                .where(NicheScoreHistory.niche_id == niche.id)
                .where(NicheScoreHistory.scored_at < today_start)
                .order_by(NicheScoreHistory.scored_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if prior is None:
                continue
            delta = today.score_total - prior.score_total
            if delta >= settings.spike_alert_threshold:
                alerts.append(build_spike_alert(
                    niche=niche,
                    today_score=today.score_total,
                    prior_score=prior.score_total,
                ))

    if not alerts:
        log.info("spike_skipped", reason="no_alerts")
        return

    for chat_id in chats:
        for text in alerts:
            try:
                await bot.send_message(
                    chat_id=chat_id, text=text, parse_mode="MarkdownV2"
                )
                log.info("spike_pushed", chat_id=chat_id)
            except Exception as exc:
                log.error("spike_push_failed",
                          chat_id=chat_id, error=str(exc))
```

- [x] **Step 4: Run — confirm pass**

```bash
pytest tests/test_scheduler_hooks.py -v
```

- [x] **Step 5: Commit**

```bash
git add app/bot/scheduler_hooks.py tests/test_scheduler_hooks.py
git commit -m "feat(bot): scheduler hooks — push digest + spike alerts"
```

---

## Task 9 — Wire scheduler jobs

Pass `bot` into `build_scheduler`. Chain `push_spike_alerts` inside `_scoring_job` (after `score_all_niches`). Register the new daily digest cron job. Pass the bot from `app/main.py`.

**Files:**
- Modify: `app/ingestion/scheduler.py`
- Modify: `app/main.py`

- [x] **Step 1: Edit `app/ingestion/scheduler.py`**

  1. Update `build_scheduler` signature:
     ```python
     def build_scheduler(
         connectors, registry, settings, *, bot=None
     ) -> AsyncIOScheduler:
     ```
  2. Inside `_scoring_job`, after `await score_all_niches(now)` and its log line, add:
     ```python
     from app.bot.scheduler_hooks import push_spike_alerts
     await push_spike_alerts(bot, as_of=now)
     ```
  3. Add a new `_digest_job` near `_brief_job`:
     ```python
     async def _digest_job() -> None:
         from app.bot.scheduler_hooks import push_daily_digest
         log.info("digest_job_start", component="scheduler")
         await push_daily_digest(bot)
         log.info("digest_job_done", component="scheduler")
     ```
  4. Register it after the brief job:
     ```python
     scheduler.add_job(
         _digest_job,
         CronTrigger(
             hour=settings.digest_cron_hour,
             minute=settings.digest_cron_minute,
         ),
         id="daily_digest",
         max_instances=1,
         replace_existing=True,
     )
     ```

- [x] **Step 2: Edit `app/main.py`**

  At line 76, replace
  ```python
  scheduler = build_scheduler(connectors, registry, settings)
  ```
  with
  ```python
  scheduler = build_scheduler(
      connectors, registry, settings,
      bot=(bot_app.bot if bot_app else None),
  )
  ```

- [x] **Step 3: Smoke check**

```bash
python -c "from app.ingestion.scheduler import build_scheduler; print('ok')"
pytest tests/ -v -k "test_scheduler or test_notifications or test_scheduler_hooks"
```

- [x] **Step 4: Commit**

```bash
git add app/ingestion/scheduler.py app/main.py
git commit -m "feat(scheduler): wire digest + spike-alert push jobs"
```

---

## Task 10 — ADR-006 + KANBAN flip

**Files:**
- Modify: `docs/decisions.md`
- Modify: `KANBAN.md`

- [x] **Step 1: Append ADR-006 to `docs/decisions.md`**

```markdown
## ADR-006: Daily digest delivery & spike-alert chaining

**Status:** Accepted (2026-04-27)

**Context.** M5 introduces two scheduled push flows: a daily digest at
08:00 UTC, and a spike alert that must fire only when today's score
truly differs from yesterday's persisted score. We considered (a) two
independent crons (scoring, then a separate spike-alert cron 15 min
later) and (b) chaining the alert inside `_scoring_job`.

**Decision.**
- Both digests and spike alerts target every chat in
  `telegram_allowed_chat_ids`.
- The spike alert is awaited inside `_scoring_job` immediately after
  `score_all_niches`, before that coroutine returns.
- The daily digest runs as its own cron (`digest_cron_hour/minute`).
- Per-chat send failures are logged and skipped; one bad chat must
  not abort the whole job.
- The bot reference is passed into `build_scheduler` so jobs can
  dispatch without a global.

**Consequences.**
- Spike alerts cannot race scoring — the same coroutine that wrote
  `NicheScoreHistory` reads it.
- If the bot is unconfigured, jobs no-op cleanly (log a WARNING).
- Future per-user subscriptions (Phase 2) replace the allowlist
  fan-out without touching the scheduler logic.
```

- [x] **Step 2: Flip M5 rows in `KANBAN.md`**

Change the `Status` column for M5-01 … M5-07 from `To Do` to `Done`.

- [x] **Step 3: Commit**

```bash
git add docs/decisions.md KANBAN.md
git commit -m "docs(m5): mark M5 tasks done; add ADR-006 (digest + spike chaining)"
```

---

## Verification

End-to-end checks before declaring M5 complete:

1. **Unit suites green**
   ```bash
   pytest tests/test_formatter.py tests/test_bot_handlers.py \
          tests/test_notifications.py tests/test_scheduler_hooks.py -v
   ```
   Expected: all passing.

2. **Full suite still green**
   ```bash
   pytest -q
   ```

3. **Type check**
   ```bash
   mypy app/
   ```

4. **Local bot smoke (manual, requires `.env` with bot token)**
   - Run the app: `uvicorn app.main:app`
   - From a chat in `TELEGRAM_ALLOWED_CHAT_IDS`, send each command:
     - `/start`, `/help` — still respond.
     - `/briefing` — top 3 briefs (after at least one M4 brief run).
     - `/niches` — full list, sorted by score.
     - `/niche <slug>` — full scorecard for a known slug; `/niche bogus` shows "not found".
     - `/trending` — last-24h delta ranking.
     - `/sources` — still works.

5. **Scheduled push smoke**
   - Temporarily set `digest_cron_minute` to `(now.minute + 2) % 60` in `.env`.
   - Restart and wait — confirm the digest arrives in every allowed chat.
   - Trigger scoring manually: `python -m scripts.run_forecasts` (or whichever entrypoint exists). Confirm a spike alert fires when an artificial `NicheScoreHistory` row crosses threshold.

6. **Length cap holds**
   - Force a huge `OpportunityBrief.summary` (e.g. 10,000 chars in DB) and call `/niche <slug>` — verify the message is sent (no Telegram 400) and ends with the truncated footer.

7. **DoD review (project doc §18, M5 rows):**
   - All slash commands return correct responses ✓
   - Daily digest pushes automatically every morning ✓
   - Spike alerts fire correctly once daily when threshold crossed ✓
   - All push events emit structured JSON logs ✓

---

*End of M5 implementation plan.*

from datetime import UTC, datetime, timedelta

from app.agents.nodes import fetcher_node
from app.db import get_session, init_db
from app.models import Niche, SourceItem


async def _mk_niche(slug: str = "alpha", name: str = "Alpha", category: str = "devtools") -> int:
    async with get_session() as session:
        n = Niche(slug=slug, name=name, category=category, summary="s",
                  keywords_json=[slug])
        session.add(n)
        await session.commit()
        await session.refresh(n)
        return n.id


async def _mk_item(niche_id: int, source_type: str, external_id: str,
                   ingested_at: datetime, **md) -> None:
    async with get_session() as session:
        session.add(SourceItem(
            source_type=source_type, external_id=external_id,
            title=f"title-{external_id}", body="body", url="u",
            created_at=ingested_at, ingested_at=ingested_at,
            niche_id=niche_id, metadata_json=md,
        ))
        await session.commit()


async def test_fetcher_loads_niche_and_recent_items():
    await init_db()
    nid = await _mk_niche()
    now = datetime.now(UTC)
    await _mk_item(nid, "github", "g1", now, stars=10)
    await _mk_item(nid, "hn", "h1", now - timedelta(days=1), points=20)

    state = await fetcher_node({"niche": {"id": nid}, "errors": []})

    assert state["niche"]["slug"] == "alpha"
    assert state["niche"]["category"] == "devtools"
    assert len(state["source_items"]) == 2
    # Newest first
    assert state["source_items"][0]["external_id"] == "g1"


async def test_fetcher_records_error_when_niche_missing():
    await init_db()

    state = await fetcher_node({"niche": {"id": 9999}, "errors": []})

    assert state["niche"].get("slug") in (None, "")
    assert state["source_items"] == []
    assert state["errors"]
    assert state["errors"][0]["component"] == "fetcher_node"


async def test_fetcher_caps_items_at_window():
    await init_db()
    nid = await _mk_niche("beta", "Beta")
    now = datetime.now(UTC)
    # Spread 40 items across days 0..39; the 30-day cutoff should drop the oldest 10.
    for i in range(40):
        await _mk_item(nid, "github", f"g{i}", now - timedelta(days=i))

    state = await fetcher_node({"niche": {"id": nid}, "errors": []})

    # 30-day window keeps days 0..29 → at most 30 items
    assert len(state["source_items"]) <= 30
    assert len(state["source_items"]) >= 28  # tolerate clock drift around midnight


from app.agents.nodes import retriever_node
from app.features.signal_aggregator import aggregate_daily_signals


async def test_retriever_loads_signals_for_niche():
    await init_db()
    nid = await _mk_niche()
    now = datetime.now(UTC)
    await _mk_item(nid, "github", "g1", now, stars=100)
    await aggregate_daily_signals(now)

    state = await retriever_node({"niche": {"id": nid}, "errors": []})

    assert state["signals"]
    metrics = {s["metric_name"] for s in state["signals"]}
    assert "mention_count" in metrics
    assert "github_stars_total" in metrics


async def test_retriever_returns_empty_when_no_signals():
    await init_db()
    nid = await _mk_niche()

    state = await retriever_node({"niche": {"id": nid}, "errors": []})

    assert state["signals"] == []
    assert not state.get("errors")


from app.agents.nodes import forecaster_node


async def test_forecaster_reads_existing_score():
    await init_db()
    nid = await _mk_niche()
    now = datetime.now(UTC)
    await _mk_item(nid, "github", "g1", now, stars=100)
    await aggregate_daily_signals(now)

    # Pre-compute a score
    from app.forecasting.scoring import score_niche
    await score_niche(nid, now)

    state = await forecaster_node({"niche": {"id": nid}, "errors": []}, as_of=now)

    assert "scorecard" in state
    assert state["scorecard"]["score_total"] >= 0.0
    assert "growth" in state["scorecard"]["breakdown"]
    assert state["forecast"]["label"] in ("Rising", "Stable", "Declining")


async def test_forecaster_computes_when_no_score_exists():
    await init_db()
    nid = await _mk_niche()
    now = datetime.now(UTC)
    await _mk_item(nid, "github", "g1", now, stars=100)
    await aggregate_daily_signals(now)
    # Note: do NOT call score_niche — forecaster should cold-start it

    state = await forecaster_node({"niche": {"id": nid}, "errors": []}, as_of=now)

    assert state["scorecard"]["score_total"] >= 0.0


async def test_forecaster_label_reflects_growth_sign():
    """A niche with strictly increasing daily mentions yields Rising."""
    await init_db()
    nid = await _mk_niche("rising", "Rising")
    base = datetime.now(UTC)
    # Create progressively more items per day for 7 days → positive slope
    for d in range(7):
        for i in range(d + 1):
            await _mk_item(
                nid, "github", f"g-{d}-{i}",
                base - timedelta(days=6 - d),
                stars=10,
            )
        await aggregate_daily_signals(base - timedelta(days=6 - d))

    state = await forecaster_node({"niche": {"id": nid}, "errors": []}, as_of=base)

    assert state["forecast"]["label"] == "Rising"
    assert state["forecast"]["slope"] > 0.0


from app.agents.nodes import reporter_node
from app.llm.mock_adapter import MockLLMAdapter
from app.agents.state import OpportunityState


async def test_reporter_builds_brief_from_state():
    state: OpportunityState = {
        "niche": {"id": 1, "slug": "x", "name": "X", "category": "c", "summary": "s"},
        "source_items": [
            {"source_type": "github", "external_id": "g1", "title": "t1",
             "url": "u1", "body": "b1",
             "created_at": "2026-04-27T00:00:00+00:00",
             "ingested_at": "2026-04-27T00:00:00+00:00", "metadata": {}},
        ],
        "signals": [],
        "scorecard": {"score_total": 70.0, "breakdown": {
            "growth": {"raw": 0.5, "normalized": 80.0},
            "demand": {"raw": 10.0, "normalized": 60.0},
            "novelty": {"raw": 0.9, "normalized": 90.0},
        }},
        "forecast": {"label": "Rising", "slope": 0.5},
        "errors": [],
    }
    out = await reporter_node(state, adapter=MockLLMAdapter())

    assert "brief" in out
    brief = out["brief"]
    assert brief["headline"]
    assert "X" in brief["headline"]
    assert "70" in brief["headline"]
    assert brief["summary"]
    assert brief["forecast_label"] == "Rising"
    assert brief["model_name"] == "MockLLMAdapter"
    assert len(brief["evidence"]) == 1
    assert brief["evidence"][0]["source_type"] == "github"


async def test_reporter_handles_timeout():
    """A slow adapter should yield an empty-summary brief, not crash."""
    import asyncio as _asyncio

    class SlowAdapter(MockLLMAdapter):
        async def generate_brief(self, context):  # type: ignore[override]
            await _asyncio.sleep(5.0)
            return "should not arrive"

    state: OpportunityState = {
        "niche": {"id": 1, "slug": "x", "name": "X", "category": "c", "summary": ""},
        "source_items": [],
        "signals": [],
        "scorecard": {"score_total": 0.0, "breakdown": {
            "growth": {"raw": 0, "normalized": 0},
            "demand": {"raw": 0, "normalized": 0},
            "novelty": {"raw": 0, "normalized": 0},
        }},
        "forecast": {"label": "Stable", "slope": 0.0},
        "errors": [],
    }
    out = await reporter_node(state, adapter=SlowAdapter(), timeout=0.05)

    assert out["brief"]["summary"] == ""
    assert any(e["component"] == "reporter_node" for e in out["errors"])


from app.agents.nodes import reviewer_node


def _brief_state(summary: str, evidence: list | None = None) -> dict:
    return {
        "niche": {"id": 1, "slug": "x", "name": "X"},
        "brief": {
            "headline": "X — 70",
            "summary": summary,
            "evidence": evidence if evidence is not None else [{"source_type": "github"}],
            "forecast_label": "Rising",
            "has_issues": False,
            "model_name": "MockLLMAdapter",
        },
        "errors": [],
    }


async def test_reviewer_passes_complete_brief():
    state = _brief_state("This is a long enough summary explaining the niche clearly with detail.")
    out = await reviewer_node(state)
    assert out["brief"]["has_issues"] is False


async def test_reviewer_flags_short_summary():
    state = _brief_state("too short")
    out = await reviewer_node(state)
    assert out["brief"]["has_issues"] is True
    assert any("summary" in g.lower() for g in out["brief"]["gaps"])


async def test_reviewer_flags_placeholder_markers():
    state = _brief_state(
        "This brief is long enough to pass length but still includes a [INSERT TEXT] marker which is a placeholder."
    )
    out = await reviewer_node(state)
    assert out["brief"]["has_issues"] is True


async def test_reviewer_flags_missing_evidence():
    state = _brief_state(
        "This is a long enough summary explaining the niche clearly with detail.",
        evidence=[],
    )
    out = await reviewer_node(state)
    assert out["brief"]["has_issues"] is True
    assert any("evidence" in g.lower() for g in out["brief"]["gaps"])

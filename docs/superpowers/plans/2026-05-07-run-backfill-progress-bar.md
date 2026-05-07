# Plan — Progress bar for `scripts/run_backfill.py`

## Context

`run_backfill.py` is a long-running CLI (often 5–30 min, dominated by LLM extraction and labelling) that today only emits structlog lines and section dividers. There is no at-a-glance signal of how far along the run is, which stage is active, or how many items remain. We want a live progress UI that shows both ingestion and pipeline progress so the operator can monitor a backfill without tailing logs.

The chosen UX is a **rich multi-bar live display** — a `Connectors` row + 5 pipeline-stage rows (Extraction, Embedding, Identity, Clustering, Labelling), each with totals where known and trailing fields like `pp=145`, `cands=23`, `items=412`.

Constraint: per `CLAUDE.md`, Claude Code cannot run `uv` here — the user runs install/test commands.

## Approach

Drive the UI from a **custom structlog processor** that watches existing log events and updates a shared `rich.progress.Progress` instance hosted under a `rich.live.Live` block. This keeps progress logic isolated to the script — `app/ingestion/backfill.py`, `app/pipeline/orchestrator.py`, and the stage modules stay almost untouched (one tiny `log.debug` addition in `labelling.py` so its bar animates per-candidate).

Why a structlog processor and not a callback parameter:
- Pipeline stages already emit structured events with the totals we need (`extraction_start total_rows=…`, `extraction_checkpoint`, `*_complete` events with reports). Hooking those is non-invasive.
- A `progress_callback` argument would touch `bulk_backfill`, `run_pipeline`, and every stage signature for one CLI script's benefit — over-engineered.

The processor is registered **before** `ConsoleRenderer` and always returns the event unchanged, so existing log output is unaffected.

### Events the processor consumes

| Event name (existing) | Source | What the bar does |
|---|---|---|
| `backfill_window_done` | `app/ingestion/backfill.py:89` | Increments per-source items + window counter for GitHub/HN |
| `backfill_connector_done` | `app/ingestion/backfill.py:102` | Marks a connector slot complete (Reddit + non-windowed paths) |
| `extraction_start` | `app/pipeline/extract.py:51` | Sets Extraction bar total to `total_rows` |
| `extraction_checkpoint` | `app/pipeline/extract.py:107` | Advances Extraction bar to `processed`, updates `pp=` field from `painpoints_created` |
| `extraction_complete` | `app/pipeline/extract.py:116` | Completes Extraction bar |
| `embedding_complete` | `app/pipeline/embed.py:54` | Sets+completes Embedding bar (`processed`/`processed`) |
| `clustering_complete` | `app/pipeline/clustering.py` | Marks Clustering bar done, shows `cands=` |
| `labelling_start` (NEW) | `app/pipeline/labelling.py` after line 38 | Sets Labelling bar total to `unlabelled_found` |
| `labelling_progress` (NEW, debug) | `app/pipeline/labelling.py` end of loop body | Advances Labelling bar by 1 |
| `labelling_complete` | `app/pipeline/labelling.py:113` | Completes Labelling bar |

Identity Resolution is fast and silent — surface as a single "Identity ✓ attached=N" row that flips on its completion event, or fall back to a "running…/done" indicator if no event exists. Verify the exact event name in `app/pipeline/identity_resolution.py` during implementation.

### Connector totals

Item totals per connector are unknown up-front. The Connectors row tracks `len(connectors)` slots (3) and shows a running `items=` count. For GitHub/HN the script already runs `_weekly_windows()` (`backfill.py:80`) — we know `len(windows)` per source, so a sub-row `└ github  window k/N` is feasible by also hooking `backfill_window_done`.

### Coexistence with structlog console output

Use `rich.logging.RichHandler` (or `Console(stderr=True)` + `Live(transient=False)`) so log lines scroll above the persistent bar block. Reconfigure `_setup_logging()` in `run_backfill.py` to route the root logger through a `RichHandler` writing to `Console(stderr=True)` while the `Live` display owns the bottom of the terminal. Structlog continues to format records via `ConsoleRenderer` — `RichHandler` just hosts them.

## Files to modify

- `pyproject.toml` — add `rich = ">=13"` to runtime deps. Then the user runs `uv sync`.
- `scripts/run_backfill.py` — new module-level `_BackfillProgress` helper class encapsulating the `Progress`, `Live`, and structlog processor; wire it into `_setup_logging()` and wrap phases 4–5 of `_run()` with `live.start()/stop()`. The existing `_section()` calls are kept (printed above the live region).
- `app/pipeline/labelling.py` — add `log.info("labelling_start", unlabelled_found=report.unlabelled_found)` after line 38 and `log.debug("labelling_progress", processed=report.labelled + report.failed)` near the end of the loop body (after line 109). No behavior change.
- (Optional) `app/pipeline/embed.py` — if we want the Embedding bar to animate instead of jumping to 100%, emit `embedding_progress` per batch inside its loop. Skip on first pass; embedding is fast.

## Implementation steps

1. Add `rich>=13` to `pyproject.toml` and ask the user to run `uv sync`.
2. In `scripts/run_backfill.py`, build a `_BackfillProgress` class:
   - Holds a `rich.progress.Progress` configured with `SpinnerColumn`, `TextColumn("{task.description}")`, `BarColumn`, `MofNCompleteColumn`, `TimeElapsedColumn`, `TimeRemainingColumn`, plus a custom `TextColumn` for trailing fields (`pp=…`, `cands=…`, `items=…`).
   - Pre-creates tasks: `Connectors`, optional per-source `└ github`/`└ hn` window sub-tasks (added lazily on first `backfill_window_done` for that source), `Extraction`, `Embedding`, `Identity`, `Clustering`, `Labelling`. Pipeline tasks start with `total=None` (indeterminate) and get totals set on the corresponding `*_start` event.
   - Exposes a `processor(logger, method_name, event_dict)` method returning `event_dict` unchanged that mutates progress state based on the `event` key.
3. Update `_setup_logging()` to:
   - Build the `_BackfillProgress` instance.
   - Insert its `processor` at the head of the structlog processor chain, before `ConsoleRenderer`.
   - Replace the `logging.basicConfig(stream=sys.stderr)` with a `RichHandler(console=progress.console, show_time=False, show_path=False)` so plain stdlib log lines render above the live region.
   - Return the `_BackfillProgress` instance so `_run()` can manage its `Live` lifecycle.
4. In `_run()`, start `progress.live.start()` just before the "4/6 Connector setup" section and stop it after "5/6 Bulk backfill" returns (in a `try/finally` that pairs with the existing `client.aclose()` block). The "6/6 Summary" section runs after Live stops so the final JSON dump goes cleanly to stdout.
5. Add the two new log events in `app/pipeline/labelling.py`. Verify the existing `clustering_complete` and `embedding_complete` event names by reading `app/pipeline/clustering.py` and `app/pipeline/embed.py` during implementation; adjust the processor mapping to match.
6. Confirm identity-resolution event name (`identity_resolution_complete` or similar) and wire it; if it doesn't emit a completion event, fall back to a "running…/✓" indicator driven by the orchestrator's stage transition log.

## Reused utilities (do not reinvent)

- `_weekly_windows()` in `app/ingestion/backfill.py:27` — already gives us per-source window counts for GitHub/HN.
- Existing structlog event names listed in the table above — no new logging contract is invented except `labelling_start` / `labelling_progress`.
- `BackfillReport.to_dict()` for the final JSON summary — unchanged.

## Verification

1. **Unit-ish check** — invoke the processor directly with hand-constructed event dicts (`extraction_start total_rows=10`, `extraction_checkpoint processed=5 painpoints_created=2`, `extraction_complete`) and assert the `Progress` task fields update. Add one small test in `tests/scripts/test_run_backfill_progress.py` that exercises the processor without spinning up `Live` (use `progress.disable=True` or operate on the underlying `Task` objects).
2. **Mock end-to-end** — user runs:
   ```
   uv run python -m scripts.run_backfill --history-days 3 --llm-provider mock --embedding-provider mock --db-url sqlite:///./tmp_progress.db
   ```
   Expected: section dividers print, then a multi-bar block appears at the bottom of stderr; bars advance through Connectors → Extraction → Embedding → Identity → Clustering → Labelling; on completion the live block is replaced by a final summary log line and the `{"backfill_report": …}` JSON is the only thing on stdout.
3. **Real provider smoke** — user runs with `--llm-provider ollama --history-days 7 --max-extraction-items 5`. Confirm Extraction bar animates per checkpoint and Labelling bar advances per candidate.
4. **Logs still parseable** — pipe stderr through `grep extraction_complete`; the event line must still be present (proves the processor doesn't swallow events).
5. **No-TTY fallback** — user runs with stderr redirected to a file (`2> run.log`). Rich auto-detects no TTY and degrades to plain text; assert the script still exits 0 and `run.log` contains all the structured events.

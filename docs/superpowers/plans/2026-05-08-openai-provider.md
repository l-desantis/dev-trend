# Plan — Add OpenAI (GPT-4.1 nano) as a backend LLM provider

## Context

DevTrend already supports three LLM backends via a clean adapter pattern (`app/llm/base.py`, `app/llm/factory.py`): `ollama` (local dev/backfill), `nim` (NVIDIA NIM cloud), and `mock` (tests). Embeddings follow the same pattern. The user wants to add **OpenAI** as a fourth provider, primarily so GPT-4.1 nano can be selected via `LLM_PROVIDER=openai`. Embeddings will also be supported (`text-embedding-3-small`) so a fully OpenAI-hosted pipeline is possible.

This slots into the existing pattern with no architectural changes — two new adapter files, a few config keys, factory wiring, tests. Per ADR-011, embeddings are isolated per provider (different `model_name` → separate cache buckets), so adding OpenAI's 1536-dim embeddings creates a new isolated bucket alongside Ollama-768 and NIM-1024 with no cross-provider identity-resolution work needed.

User decisions captured during brainstorming:
- **Goal**: add OpenAI as an interchangeable provider (not A/B testing, not per-stage mixing).
- **Scope**: chat **and** embeddings.
- **HTTP client**: official `openai` Python SDK (`AsyncOpenAI`), not raw httpx — gives native Pydantic structured outputs and built-in retries.

## Design

### New files

**`app/llm/openai_adapter.py`** — `OpenAIAdapter(LLMAdapter)`
- Holds an `AsyncOpenAI` client (api_key, base_url, `max_retries=3`, `timeout=60s`).
- `model_name` returns `f"openai:{self._model}"` — critical, this is the extraction-cache key per ADR-011, keeping OpenAI extractions in their own bucket.
- For `extract_pain_point` and `label_cluster`: use `client.beta.chat.completions.parse(response_format=PainPointDraft | ClusterLabel)` for guaranteed structured output — replaces the NIM adapter's manual `json_object` + `model_validate_json` + ValidationError fallback dance with one SDK call.
- `extract_pain_point` failure path mirrors NIM: on parse / validation / length / content-filter errors, log a warning and return `PainPointDraft(has_unmet_need=False)` so the pipeline doesn't break on a single bad row.
- `label_cluster` lets exceptions propagate (matches NIM behaviour).
- `generate_brief` / `summarize_evidence` use plain `client.chat.completions.create` (free-text).
- `review_brief` reuses the same length-check shortcut NIM uses.
- `aclose()` calls `await self._client.close()`.

**`app/llm/openai_embedding_adapter.py`** — `OpenAIEmbeddingAdapter(EmbeddingAdapter)`
- Same `AsyncOpenAI` client style.
- `dim` returns `1536` (text-embedding-3-small).
- `model_name` returns `f"openai:{self._model}"`.
- `embed` calls `client.embeddings.create(model=..., input=texts)` and returns `[d.embedding for d in resp.data]`.

### Modified files

**`app/config.py`**
- Extend the two `Literal[...]` types: `llm_provider: Literal["ollama", "nim", "mock", "openai"]` and same for `embedding_provider`.
- Add four settings keys, mirroring the NIM block:
  - `openai_api_key: str = ""`
  - `openai_base_url: str = "https://api.openai.com/v1"`
  - `openai_llm_model: str = "gpt-4.1-nano"`
  - `openai_embedding_model: str = "text-embedding-3-small"`

**`app/llm/factory.py`**
- Add `case "openai":` branches to both `make_llm_adapter` and `make_embedding_adapter`, each guarding on `settings.openai_api_key` (raise `ValueError` if missing) — mirrors the NIM branches at lines 14–22 and 35–43.

**`pyproject.toml`** — add `openai` to dependencies.

**`.env.example`** — add the four new keys (empty defaults).

**`README.md`** — short addition under "Cloud deployment" describing `LLM_PROVIDER=openai` / `EMBEDDING_PROVIDER=openai`.

### Tests

- `tests/llm/test_openai_adapter.py` — mirror `tests/llm/test_nim_adapter.py`. Mock `AsyncOpenAI` (or use `respx` if existing tests do). Cover:
  - `extract_pain_point` happy path (returns parsed `PainPointDraft`)
  - `extract_pain_point` validation-failure → returns `has_unmet_need=False`
  - `label_cluster` happy path
  - `generate_brief` returns a non-empty string
  - `model_name` uses the `openai:` prefix
- `tests/llm/test_openai_embedding_adapter.py` — mirror NIM embedding tests. Cover `embed` returns vectors, `dim == 1536`, `model_name` prefix.
- Extend `tests/llm/test_factory.py` (or wherever the factory is tested) to cover the new `case "openai":` branches in both factories, including the missing-API-key error.

### Critical files to read while implementing

- `app/llm/base.py:1-39` — `LLMAdapter` ABC contract.
- `app/llm/embedding_base.py:1-15` — `EmbeddingAdapter` ABC contract.
- `app/llm/nim_adapter.py:1-144` — pattern to mirror; note the retry / JSON-fallback discipline.
- `app/llm/nim_embedding_adapter.py:1-44` — pattern for the embedding adapter.
- `app/llm/factory.py:1-46` — exact extension points (the two `match` blocks).
- `app/llm/schemas.py:1-23` — the Pydantic schemas the SDK's `response_format=` will use directly.
- `app/llm/prompts.py` — prompts are reused across all adapters; do not duplicate.
- `app/config.py:106-118` — NIM settings block to mirror.

## Verification (run by user — see CLAUDE.md)

1. `! uv add openai && uv sync` — install dep.
2. `! uv run mypy app/` — types still clean.
3. `! uv run ruff check app/ tests/` — lint clean.
4. `! uv run pytest tests/llm/` — all adapter unit tests pass.
5. **Live smoke** — set in `.env`:
   ```
   LLM_PROVIDER=openai
   EMBEDDING_PROVIDER=openai
   OPENAI_API_KEY=sk-...
   ```
   then `! uv run python scripts/run_backfill.py --history-days 7 --max-extraction-items 20 --dry-run` (uses the new estimator, no API calls), followed by the same command without `--dry-run` to verify a real pipeline pass extracts pain-points and clusters.
6. **Mixed-provider sanity check** — set `LLM_PROVIDER=openai` with `EMBEDDING_PROVIDER=nim`; confirm the pipeline still runs and the extraction-cache buckets stay isolated (`pain_point.embedding_model` rows show both `openai:...` and `nim:...` if the DB had prior runs).

## Out of scope (explicit)

- Per-stage mixing (different providers for extract vs label vs brief) — keep one provider per role for now.
- Cost / quality A-B harness — separate feature, not needed for this change.
- Re-embedding old pain-points across providers — README already calls this out as a deferred follow-up.
- Token-usage logging from `response.usage` — interesting, but the recently-merged char-based estimator is what `--dry-run` uses; surfacing actuals is a separate task.

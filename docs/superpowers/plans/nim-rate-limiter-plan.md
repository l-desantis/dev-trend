# NVIDIA NIM client-side rate limiter

## Context

The NVIDIA NIM free tier caps the API key at **40 RPM**. The project currently has no client-side throttling for NIM — both `NvidiaNimAdapter` (chat) and `NvidiaNimEmbeddingAdapter` (embeddings) fire requests as fast as the pipeline issues them. During backfill or weekly reclustering, this exceeds 40 RPM and the calls fail (HTTP 429), wasting work.

Goal: gate every outbound NIM request behind a process-wide rate limiter, capped at **N RPM** (default 40), with a **single shared budget across both NIM adapters** since they use the same API key. Behavior is controlled by env vars and applies **only** to the NIM provider — Ollama, OpenAI, and Mock adapters are untouched.

User decisions:
- **Default**: ON. `NIM_RATE_LIMIT_ENABLED` defaults to `true`.
- **Configurable RPM** via `NIM_RATE_LIMIT_RPM` (default `40`).
- **Shared limiter** for LLM + embedding adapters (one 40 RPM budget total).

## Files to modify

| File | Change |
|---|---|
| `app/config.py` (~line 118, NIM block) | Add `nim_rate_limit_enabled: bool = True` and `nim_rate_limit_rpm: int = 40` |
| `app/llm/rate_limiter.py` *(new)* | `AsyncRateLimiter` sliding-window limiter |
| `app/llm/nim_adapter.py` | Accept optional `rate_limiter` arg; `await` on it before each `_client.post()` in `_chat()` |
| `app/llm/nim_embedding_adapter.py` | Same — accept `rate_limiter`; `await` on it before the `_client.post()` in `embed()` |
| `app/llm/factory.py` | Build one shared `AsyncRateLimiter` per `Settings` instance when `nim_rate_limit_enabled` and `llm_provider == "nim"` or `embedding_provider == "nim"`. Inject into both NIM adapter constructors. |
| `tests/llm/test_nim_adapter.py` | Add tests that verify the limiter is awaited and that absence of a limiter is a no-op |
| `tests/llm/test_rate_limiter.py` *(new)* | Unit tests for `AsyncRateLimiter` (allows N requests then blocks; releases tokens after window) |
| `.env.example` *(if present)* | Document the two new env vars |

## Design

### `AsyncRateLimiter` (new, `app/llm/rate_limiter.py`)

Small sliding-window limiter, no new dependency. ~30 lines:

```python
import asyncio, time
from collections import deque

class AsyncRateLimiter:
    """Allow at most `max_requests` per `window_seconds`. Sliding window."""

    def __init__(self, max_requests: int, window_seconds: float = 60.0) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be > 0")
        self._max = max_requests
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self._window:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._max:
                    self._timestamps.append(now)
                    return
                wait_for = self._window - (now - self._timestamps[0])
                await asyncio.sleep(wait_for)
```

Why a custom class (not `aiolimiter` or `tenacity`): project has no rate-limit libs, and 30 lines of stdlib is simpler than a new dependency. Holds an `asyncio.Lock` so concurrent coroutines in the pipeline (e.g., parallel extraction tasks) serialize correctly.

### Adapter wiring

Both `NvidiaNimAdapter.__init__` and `NvidiaNimEmbeddingAdapter.__init__` get a new optional kwarg:

```python
def __init__(self, api_key, model=..., base_url=..., rate_limiter: "AsyncRateLimiter | None" = None):
    ...
    self._limiter = rate_limiter
```

Each request site (in `_chat`'s retry loop in `nim_adapter.py:62` and in `embed` in `nim_embedding_adapter.py:36`) prepends:

```python
if self._limiter is not None:
    await self._limiter.acquire()
response = await self._client.post(...)
```

Placing `acquire()` *inside* the retry loop of `_chat()` (rather than outside) is intentional: a retried request is a separate HTTP call against the quota, so it should also be gated.

### Factory wiring (`app/llm/factory.py`)

The limiter must be **shared** between the LLM and embedding adapters when both are NIM. Both `make_llm_adapter` and `make_embedding_adapter` receive the same `Settings` object during app startup (`app/main.py:95-97`) and scheduler jobs. The cleanest place to keep one limiter per `Settings` is module-level caching keyed by `settings.nim_api_key` (so test isolation with different settings works).

```python
from functools import lru_cache
from app.llm.rate_limiter import AsyncRateLimiter

@lru_cache(maxsize=8)
def _nim_limiter_for(api_key: str, rpm: int) -> AsyncRateLimiter:
    return AsyncRateLimiter(max_requests=rpm, window_seconds=60.0)

def _maybe_nim_limiter(settings: Settings) -> AsyncRateLimiter | None:
    if not settings.nim_rate_limit_enabled:
        return None
    return _nim_limiter_for(settings.nim_api_key, settings.nim_rate_limit_rpm)
```

Then in both `case "nim":` branches, pass `rate_limiter=_maybe_nim_limiter(settings)` to the adapter constructor.

Using `lru_cache` keyed on `(api_key, rpm)` ensures one process-wide limiter instance per API key, which is what enforces the *shared* budget across both adapters. Different API keys (rare, but e.g. tests) get their own limiter.

### Config (`app/config.py`)

In the `# NIM (NVIDIA)` block (~line 114-118), add:

```python
nim_rate_limit_enabled: bool = True
nim_rate_limit_rpm: int = 40
```

Pydantic-settings handles the env var mapping automatically (`NIM_RATE_LIMIT_ENABLED`, `NIM_RATE_LIMIT_RPM`).

### What is NOT changed

- Ollama, OpenAI, Mock adapters and their embedding counterparts — untouched.
- The existing `_MAX_RETRIES` 5xx retry logic in `nim_adapter.py:60-83` — kept as-is.
- The `request_with_retry` utility in `app/ingestion/http_utils.py` — kept as-is (handles 429s for ingestion HTTP, unrelated path).

## Tests

### `tests/llm/test_rate_limiter.py` (new)

1. `test_allows_burst_up_to_max` — fire 5 `acquire()` calls with `max=5, window=60` instantly; all return immediately, no sleep.
2. `test_blocks_when_full` — with `max=2, window=0.2`, fire 3 acquires; assert the third sleeps ≥ ~0.2s using `time.monotonic()` measurement.
3. `test_releases_after_window` — fire `max` acquires, sleep past window, fire another; should not block.
4. `test_concurrent_acquires_serialize` — gather 10 acquires with `max=3, window=0.1`; total elapsed ≥ ~0.3s (3 batches).

Use real `asyncio.sleep` with tiny windows (≤0.2s) rather than mocking time — keeps tests deterministic and avoids monkeypatching.

### `tests/llm/test_nim_adapter.py` (additions)

1. `test_chat_calls_rate_limiter` — inject a stub limiter (object with `AsyncMock` `acquire`), call `extract_pain_point` once, assert `acquire` was awaited once before `_client.post`.
2. `test_chat_no_limiter_works` — construct adapter without `rate_limiter`; existing behavior unchanged (existing tests already cover this implicitly; add an explicit "no limiter passed" smoke test).
3. `test_chat_acquires_on_each_retry` — set up a 503 then 200; assert limiter `acquire` was awaited twice.

### `tests/llm/test_nim_embedding_adapter.py` (additions, or add to nim_adapter test file)

1. `test_embed_calls_rate_limiter` — same pattern as chat: stub limiter, single call, one `acquire` await.

### `tests/llm/test_factory.py` (additions, if file exists; otherwise create)

1. `test_nim_adapters_share_limiter` — build settings with `llm_provider="nim"`, `embedding_provider="nim"`, `nim_api_key="k"`; call both factory functions; assert both returned adapters have `_limiter is the same instance`.
2. `test_disabled_means_no_limiter` — same settings but `nim_rate_limit_enabled=False`; both adapters have `_limiter is None`.
3. `test_non_nim_provider_has_no_limiter` — OpenAI adapter has no `_limiter` attribute (or it's None); confirms the throttle is NIM-only.

## Verification

End-to-end sanity check (asking the user to run, per CLAUDE.md):

```bash
! uv run pytest tests/llm/test_rate_limiter.py tests/llm/test_nim_adapter.py tests/llm/test_nim_embedding_adapter.py tests/llm/test_factory.py -v
```

Manual smoke (optional, if `.env` is set to use NIM):

```bash
! NIM_RATE_LIMIT_ENABLED=true NIM_RATE_LIMIT_RPM=5 uv run python -c "
import asyncio, time
from app.config import get_settings
from app.llm.factory import make_llm_adapter

async def main():
    a = make_llm_adapter(get_settings())
    t = time.monotonic()
    # 7 quick calls with cap=5 should take >60s for the last 2
    for i in range(7):
        await a.summarize_evidence([{'source_type':'test','title':f'hi {i}'}])
        print(f'{i}: elapsed={time.monotonic()-t:.1f}s')
    await a.aclose()

asyncio.run(main())
"
```

Expected: first 5 calls complete promptly; call 6 and 7 each delayed so total elapsed crosses 60s.

## Out of scope

- Server-side 429 handling for NIM — not added. The client-side limit should prevent 429s entirely under normal pipeline use; if NVIDIA returns 429 anyway (e.g., other clients sharing the key, clock skew), the existing 5xx retry won't catch it, but the user can adjust `NIM_RATE_LIMIT_RPM` downward. Adding 429 retry is a follow-up if it becomes an issue in practice.
- Cross-process coordination — limiter is per-process. The app runs as a single FastAPI process so this is fine.

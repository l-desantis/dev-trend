"""Unit tests for AsyncRateLimiter."""
import asyncio
import time

import pytest

from app.llm.rate_limiter import AsyncRateLimiter


@pytest.mark.asyncio
async def test_allows_burst_up_to_max():
    limiter = AsyncRateLimiter(max_requests=5, window_seconds=60.0)
    t0 = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"Burst of 5 should complete immediately, took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_blocks_when_full():
    limiter = AsyncRateLimiter(max_requests=2, window_seconds=0.2)
    await limiter.acquire()
    await limiter.acquire()
    t0 = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.15, f"Third acquire should sleep ~0.2s, only waited {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_releases_after_window():
    limiter = AsyncRateLimiter(max_requests=2, window_seconds=0.1)
    await limiter.acquire()
    await limiter.acquire()
    await asyncio.sleep(0.15)
    t0 = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1, f"Should not block after window expired, took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_concurrent_acquires_serialize():
    limiter = AsyncRateLimiter(max_requests=3, window_seconds=0.1)
    t0 = time.monotonic()
    await asyncio.gather(*[limiter.acquire() for _ in range(10)])
    elapsed = time.monotonic() - t0
    # 10 requests at 3 per 0.1s = ceil(10/3)-1 = 2 waits of ~0.1s each
    assert elapsed >= 0.18, f"10 concurrent acquires at 3/0.1s should take ≥0.2s, took {elapsed:.3f}s"

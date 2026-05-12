"""Async sliding-window rate limiter for NIM API calls."""
import asyncio
import time
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

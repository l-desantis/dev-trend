# pipeline-level test configuration — session fixture provided by root conftest
import pytest

import app.pipeline.validation as _validation_module
from app.llm.rate_limiter import AsyncRateLimiter


@pytest.fixture(autouse=True)
def _unlimited_github_rate_limiters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace module-level GitHub rate limiters with high-capacity ones.

    The real limiters (8 req/60 s unauthenticated) trigger asyncio.sleep() and
    make the suite slow. Tests mock the HTTP layer anyway so no throttle is needed.
    """
    monkeypatch.setattr(_validation_module, "_github_limiter_auth", AsyncRateLimiter(max_requests=10_000))
    monkeypatch.setattr(_validation_module, "_github_limiter_unauth", AsyncRateLimiter(max_requests=10_000))

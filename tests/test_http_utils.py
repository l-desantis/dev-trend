import httpx
import pytest

from app.ingestion.http_utils import request_with_retry


@pytest.mark.asyncio
async def test_returns_no_retry_status_verbatim() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await request_with_retry(
            client, "GET", "https://example.com",
            no_retry_statuses={429},
        )

    assert resp.status_code == 429
    assert calls["n"] == 1  # no retry was attempted


@pytest.mark.asyncio
async def test_429_still_retries_when_not_opted_out() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Return 429 twice then 200 to confirm retry loop still works.
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await request_with_retry(client, "GET", "https://example.com")

    assert resp.status_code == 200
    assert calls["n"] == 3

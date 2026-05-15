import asyncio

import httpx


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    no_retry_statuses: set[int] | None = None,
    **kwargs,
) -> httpx.Response:
    no_retry = no_retry_statuses or set()
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code in no_retry:
                return resp
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(min(retry_after, 30))
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(min(2 ** attempt, 30))
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            await asyncio.sleep(min(2 ** attempt, 30))
    raise last_exc or RuntimeError(f"Failed after 3 attempts: {url}")

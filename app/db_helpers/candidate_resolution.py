"""Helper for traversing OpportunityCandidate merge chains."""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OpportunityCandidate

log = structlog.get_logger(__name__)


async def resolve_candidate_root(session: AsyncSession, candidate_id: int) -> int:
    """Return the surviving (non-archived) candidate at the end of a merge chain.

    Raises RuntimeError if a cycle is detected.
    """
    seen: set[int] = set()
    cur = candidate_id
    while True:
        if cur in seen:
            raise RuntimeError(f"merge cycle detected at candidate_id={cur}")
        seen.add(cur)
        c = await session.get(OpportunityCandidate, cur)
        if c is None or not c.is_archived or c.merged_into_id is None:
            return cur
        cur = c.merged_into_id

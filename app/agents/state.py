import operator
from typing import Annotated, TypedDict


class OpportunityState(TypedDict, total=False):
    niche: dict
    source_items: list
    signals: list
    forecast: dict
    scorecard: dict
    brief: dict
    # Annotated reducer: each node appends its own errors; LangGraph accumulates them.
    errors: Annotated[list, operator.add]
    triggered_by: str

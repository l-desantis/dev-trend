from typing import TypedDict


class OpportunityState(TypedDict, total=False):
    niche: dict
    source_items: list
    signals: list
    forecast: dict
    scorecard: dict
    brief: dict
    errors: list
    triggered_by: str

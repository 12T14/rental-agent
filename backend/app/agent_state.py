"""用于崩溃后对账的应用自有图状态通道。"""

from typing import Any, NotRequired

from langchain.agents.middleware.types import AgentState


class RentalAgentState(AgentState):
    rental_request: str
    rental_base_listings: NotRequired[list[dict[str, Any]]]
    rental_listing_baseline: NotRequired[int]
    rental_criteria: NotRequired[dict[str, Any]]

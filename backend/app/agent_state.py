"""用于崩溃后对账的应用自有图状态通道。"""

from langchain.agents.middleware.types import AgentState


class RentalAgentState(AgentState):
    rental_request: str

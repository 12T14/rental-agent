"""Record the agent's choices against this conversation's actual candidate pool."""

import json

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field


class RentalRecommendation(BaseModel):
    listing_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)
    caveat: str = Field(default="", max_length=500)


@tool
def publish_rental_recommendations(
    recommendations: list[RentalRecommendation], runtime: ToolRuntime
) -> str:
    """登记本轮推荐，不搜索、不隐藏其他候选，也不把读过详情等同于推荐。

    listing_id 必须原样复制搜索工具的 listing_id；reason 写推荐依据，caveat 写缺点及
    待核验项。数量由实际质量决定，不必凑十条；没有合适房源时传空列表。
    最终聊天只引用返回的固定 display_number（房源 #N），不得自行重新编号。
    """
    # Deferred imports avoid a circular dependency during graph construction.
    from .agent_runtime import _extract_listing_events, _validated_recommendations
    try:
        from ..agent_core.ranking import SearchCriteria, rank_listings
    except ImportError:
        from agent_core.ranking import SearchCriteria, rank_listings

    state = runtime.state
    messages = state.get("messages", [])
    baseline = state.get("rental_listing_baseline", 0)
    criteria = SearchCriteria(**{
        key: value for key, value in state.get("rental_criteria", {}).items()
        if key in SearchCriteria.__dataclass_fields__
    })
    _, listings, _, _ = _extract_listing_events(
        messages[baseline:], state.get("rental_base_listings", []), criteria=criteria,
    )
    listings = rank_listings(listings, criteria)
    raw = [item.model_dump() for item in recommendations]
    accepted = _validated_recommendations(raw, listings)
    accepted_ids = {item["listing_id"] for item in accepted}
    rejected = [
        {
            "listing_id": item["listing_id"],
            "reason": (
                "最多只能登记 5 条 Agent 精选"
                if index >= 5
                else "必须通过硬条件初筛、成功读取详情，且不能存在城市冲突"
            ),
        }
        for index, item in enumerate(raw)
        if item["listing_id"] not in accepted_ids
    ]
    return json.dumps({
        "status": "ok",
        "recommendations": accepted,
        "rejected_ids": [item["listing_id"] for item in raw if item["listing_id"] not in accepted_ids],
        "rejected": rejected,
        "candidate_count": len(listings),
        "recommendation_limit": 5,
        "message": "推荐已登记；只引用这些固定编号。地图仍保留全部候选，详情未读取的只能作为待核验备选。",
    }, ensure_ascii=False)

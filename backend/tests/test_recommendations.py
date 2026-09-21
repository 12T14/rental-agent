"""Recommendation identity contract; no external services or real model calls."""

import json
import unittest

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.graph import START, END, StateGraph

from agent_core.candidate_search import _number_candidates
from app.agent_runtime import (
    _extract_listing_events,
    _safe_enrichment_payload,
    _safe_search_payload,
    _validated_recommendations,
)
from app.recommendation_tool import publish_rental_recommendations
from app.agent_state import RentalAgentState
from app.agent_runtime import AgentRuntime
from app.checkpoint_store import CheckpointStore, StorageSettings
from agent_core.map_service import MapService, FakeMapProvider
from agent_core.ranking import SearchCriteria


def candidates(count=12):
    return _number_candidates([{
        "platform": "58同城", "title": f"候选 {index}", "price": 1000 + index,
        "detail_url": f"https://nj.58.com/zufang/{index}.shtml",
    } for index in range(count)])


def search_message(items):
    return ToolMessage(
        name="search_rental_candidates", tool_call_id="search-1",
        content=json.dumps({"status": "ok", "mode": "live", "listings": items}),
    )


def recommendation_graph():
    builder = StateGraph(RentalAgentState)
    builder.add_node("tools", ToolNode([publish_rental_recommendations]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile()


class RecommendationTests(unittest.TestCase):
    def test_identity_stays_stable_and_search_number_matches_model_projection(self):
        items = candidates()
        projected = _safe_search_payload({"listings": items})["listings"]
        self.assertEqual([item["displayNumber"] for item in projected], list(range(1, 13)))
        self.assertEqual([item["id"] for item in projected], [item["listing_id"] for item in items])
        self.assertEqual(_number_candidates(list(reversed(items)))[0]["listing_id"], items[-1]["listing_id"])
        enriched = _safe_enrichment_payload({"listings": list(reversed(projected))})
        self.assertEqual([item["displayNumber"] for item in enriched["listings"]], list(range(12, 0, -1)))

    def test_projection_keeps_tool_numbers_even_if_unusable_entries_are_removed(self):
        items = candidates(3)
        items[1] = None
        projected = _safe_search_payload({"listings": items})["listings"]
        self.assertEqual([item["displayNumber"] for item in projected], [1, 3])
        legacy = _safe_search_payload({"listings": [
            {"id": "legacy"}, {"id": "known", "display_number": 1},
            {"id": "duplicate-number", "display_number": 1},
        ]})["listings"]
        self.assertEqual([item["displayNumber"] for item in legacy], [2, 1, 3])

    def test_details_do_not_imply_recommendation_and_new_search_clears_highlights(self):
        items = candidates()
        detail = ToolMessage(name="batch_fetch_listing_details", tool_call_id="detail-1", content=json.dumps({
            "status": "complete", "results": [
                {"url": items[0]["detail_url"], "status": "ok", "facts": {}},
                {"url": items[7]["detail_url"], "status": "ok", "facts": {}},
            ],
        }))
        publish = ToolMessage(name="publish_rental_recommendations", tool_call_id="recommend-1", content=json.dumps({
            "status": "ok", "recommendations": [{
                "listing_id": items[7]["listing_id"], "reason": "租金合适", "display_number": 999,
            }],
        }))
        events, listings, _, _ = _extract_listing_events(
            [search_message(items), detail, publish], criteria=SearchCriteria()
        )
        self.assertIsNone(listings[0]["recommendation"])
        self.assertEqual(listings[7]["displayNumber"], 8)
        self.assertEqual(listings[7]["recommendation"]["reason"], "租金合适")
        self.assertEqual(events[-1][0], "listing_recommendations")
        # Retrying a detail does not overwrite the recommendation membership.
        _, retried, _, _ = _extract_listing_events([detail], listings, criteria=SearchCriteria())
        self.assertEqual(retried[7]["recommendation"], listings[7]["recommendation"])
        _, fresh, _, _ = _extract_listing_events([search_message(items)], retried)
        self.assertTrue(all(item["recommendation"] is None for item in fresh))

    def test_unknown_duplicate_and_excluded_ids_never_become_recommendations(self):
        pool = _safe_search_payload({"listings": candidates()})["listings"]
        pool[0]["filterStatus"] = "passed"
        pool[0]["detailStatus"] = "ok"
        pool[1]["filterStatus"] = "excluded"
        raw = [
            {"listing_id": pool[0]["id"], "reason": "适合", "caveat": "费用待核验", "display_number": 60},
            {"listing_id": pool[0]["id"], "reason": "重复"},
            {"listing_id": pool[1]["id"], "reason": "不该推荐"},
            {"listing_id": "another-session-id", "reason": "非法"},
        ]
        selected = _validated_recommendations(raw, pool)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["display_number"], 1)
        self.assertEqual(selected[0]["detail_status"], "ok")
        self.assertEqual(_validated_recommendations([], pool), [])

    def test_recommendations_are_limited_to_five_verified_candidates(self):
        pool = _safe_search_payload({"listings": candidates()})["listings"]
        for item in pool:
            item["filterStatus"] = "passed"
            item["detailStatus"] = "ok"
        selected = _validated_recommendations(
            [{"listing_id": item["id"], "reason": "值得比较"} for item in pool], pool
        )
        self.assertEqual(len(selected), 5)

    def test_real_toolnode_injects_current_conversation_and_validates_budget(self):
        items = candidates()
        items[0]["detail_status"] = "ok"
        call = AIMessage(content="", tool_calls=[{
            "id": "publish-1", "name": "publish_rental_recommendations", "type": "tool_call",
            "args": {"recommendations": [
                {"listing_id": items[0]["listing_id"], "reason": "预算内"},
                {"listing_id": items[11]["listing_id"], "reason": "超预算"},
                {"listing_id": "foreign", "reason": "另一会话"},
            ]},
        }])
        node = recommendation_graph()
        result = node.invoke({
            "messages": [search_message(items), call],
            "rental_listing_baseline": 0, "rental_base_listings": [],
            "rental_criteria": {"budget_max": 1005},
        })
        response = json.loads(result["messages"][-1].content)
        self.assertEqual([item["display_number"] for item in response["recommendations"]], [1])
        self.assertEqual(len(response["rejected_ids"]), 2)
        self.assertNotIn("runtime", publish_rental_recommendations.tool_call_schema.model_fields)

    def test_followup_uses_saved_pool_not_old_search_and_empty_selection_clears_all(self):
        pool = _safe_search_payload({"listings": candidates()})["listings"]
        pool[0]["recommendation"] = {"reason": "之前推荐", "caveat": ""}
        call = AIMessage(content="", tool_calls=[{
            "id": "publish-2", "name": "publish_rental_recommendations", "type": "tool_call",
            "args": {"recommendations": []},
        }])
        result = recommendation_graph().invoke({
            "messages": [search_message(candidates(1)), call],
            "rental_listing_baseline": 1, "rental_base_listings": pool, "rental_criteria": {},
        })
        content = json.loads(result["messages"][-1].content)
        self.assertEqual(content["candidate_count"], 12)
        _, cleared, _, _ = _extract_listing_events(result["messages"][-1:], pool)
        self.assertEqual(len(cleared), 12)
        self.assertTrue(all(item["recommendation"] is None for item in cleared))

    def test_relaxed_budget_can_recommend_previously_excluded_house_without_losing_number(self):
        pool = _safe_search_payload({"listings": candidates(1)})["listings"]
        pool[0]["detailStatus"] = "ok"
        pool[0]["filterStatus"] = "excluded"
        pool[0]["filterReasons"] = ["超过旧预算"]
        call = AIMessage(content="", tool_calls=[{
            "id": "relaxed", "name": "publish_rental_recommendations", "type": "tool_call",
            "args": {"recommendations": [{"listing_id": pool[0]["id"], "reason": "符合新预算"}]},
        }])
        result = recommendation_graph().invoke({
            "messages": [call], "rental_listing_baseline": 0, "rental_base_listings": pool,
            "rental_criteria": {"budget_max": 2000},
        })
        response = json.loads(result["messages"][-1].content)
        self.assertEqual([entry["display_number"] for entry in response["recommendations"]], [1])
        events, updated, _, _ = _extract_listing_events(
            result["messages"][-1:], pool, criteria=SearchCriteria(budget_max=2000),
        )
        self.assertEqual(updated[0]["filterStatus"], "passed")
        self.assertEqual(updated[0]["displayNumber"], 1)
        self.assertEqual(updated[0]["recommendation"]["reason"], "符合新预算")
        self.assertEqual(events[-1][1]["recommendation_count"], 1)


class RecommendationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepagent_recommendation_stream_history_and_replay_use_same_ids(self):
        from deepagents import create_deep_agent
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.outputs import ChatResult, ChatGeneration
        from langchain_core.tools import tool

        items = candidates(25)
        items[7]["detail_status"] = "ok"
        items[24]["detail_status"] = "ok"
        calls = []

        @tool
        def search_rental_candidates() -> str:
            """Return the deterministic offline search pool."""
            calls.append("search")
            return json.dumps({"status": "ok", "mode": "offline_fixture", "listings": items})

        class ScriptedModel(BaseChatModel):
            @property
            def _llm_type(self):
                return "offline-recommendations-test"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                last = messages[-1]
                if isinstance(last, ToolMessage) and last.name == "publish_rental_recommendations":
                    saved = json.loads(last.content)
                    content = "推荐 " + "、".join(f"房源 #{item['display_number']}" for item in saved["recommendations"])
                    answer = AIMessage(content=content)
                elif isinstance(last, ToolMessage) and last.name == "search_rental_candidates":
                    answer = AIMessage(content="", tool_calls=[{
                        "id": "rec-call", "name": "publish_rental_recommendations",
                        "args": {"recommendations": [
                            {"listing_id": items[7]["listing_id"], "reason": "预算内", "caveat": "详情未读取"},
                            {"listing_id": items[24]["listing_id"], "reason": "可以比较", "caveat": "费用待核验"},
                        ]},
                    }])
                else:
                    answer = AIMessage(content="", tool_calls=[{
                        "id": "search-call", "name": "search_rental_candidates", "args": {},
                    }])
                return ChatResult(generations=[ChatGeneration(message=answer)])

        runtime = AgentRuntime(
            lambda saver: create_deep_agent(
                model=ScriptedModel(), tools=[search_rental_candidates, publish_rental_recommendations],
                state_schema=RentalAgentState, checkpointer=saver,
            ), CheckpointStore(StorageSettings()),
            map_service_factory=lambda: MapService(FakeMapProvider()),
        )
        try:
            events = [event async for event in runtime.stream("recommendations", "预算2000元", "r1")]
            selected = next(event.data for event in events if event.event == "listing_recommendations")
            self.assertEqual(len(selected["listings"]), 25)
            self.assertEqual([item["displayNumber"] for item in selected["listings"] if item["recommendation"]], [8, 25])
            history = await runtime.get_session("recommendations")
            self.assertEqual(len(history["listings"]), 25)
            self.assertEqual(sorted(item["displayNumber"] for item in history["listings"] if item["recommendation"]), [8, 25])
            self.assertIn("房源 #8", history["messages"][-1]["text"])
            replay = [event async for event in runtime.stream("recommendations", "预算2000元", "r1")]
            self.assertEqual(replay[0].data["status"], "replayed")
            self.assertEqual(calls, ["search"])
        finally:
            await runtime.close()


if __name__ == "__main__":
    unittest.main()

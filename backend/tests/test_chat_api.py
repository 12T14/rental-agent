from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch
from typing import Any
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.agent_runtime import (
    AgentRuntime,
    AgentStreamEvent,
    _listing_projection,
    _extract_listing_events,
    _safe_detail_batch_payload,
    _safe_listing_search_payload,
    _merge_detail_into_listings,
    _safe_enrichment_payload,
)
from app.main import create_app
from app.checkpoint_store import CheckpointStore, StorageSettings
from app.agent_state import RentalAgentState
from agent_core.map_service import FakeMapProvider, MapService


def _location_message() -> dict[str, Any]:
    return {
        "role": "tool",
        "name": "resolve_target_place",
        "content": json.dumps(
            {
                "mode": "offline_fixture",
                "provider": "fake",
                "status": "needs_city_confirmation",
                "query": "示例学院",
                "city_hint": "",
                "requires_user_confirmation": True,
                "message": "请确认城市和地点",
                "candidates": [
                    {
                        "candidate_ref": "pc_test",
                        "name": "示例学院",
                        "formatted_address": "江苏省示例市示例区示例路 155 号",
                        "city": "示例市",
                        "district": "示例区",
                        "adcode": "321282",
                        "lng": 120.28,
                        "lat": 31.98,
                        "confidence": "fixture",
                        "provider": "fake",
                        "data_quality": "fixture_only",
                    }
                ],
                "internal_path": "must-not-be-forwarded",
            },
            ensure_ascii=False,
        ),
    }


class SnapshotAgent:
    def __init__(self):
        self.values = {}

    async def aget_state(self, config):
        return SimpleNamespace(values=self.values, next=(), interrupts=())


class DetailCarryForwardTests(unittest.TestCase):
    def test_repeated_search_preserves_successful_details_for_same_url(self) -> None:
        url = "https://example.58.com/zufang/reused.html"
        previous = [{
            "id": "old-id", "detailUrl": url, "sourceUrl": url,
            "title": "详情标题", "rent": 1800, "detailStatus": "ok",
            "detailFacts": {"payment_rule": "押一付一"},
            "detailLocation": {"address": "中山北路283号", "community": "鲁迅园小区", "city": "南京"},
        }]
        message = {
            "role": "tool", "name": "search_rental_candidates",
            "content": json.dumps({
                "mode": "live", "status": "completed", "listings": [{
                    "id": "new-id", "platform": "58同城", "title": "列表标题",
                    "rent": 1800, "detail_url": url,
                    "community": "平台列表未提供小区",
                    "address": "平台列表未提供具体地址",
                }],
            }, ensure_ascii=False),
        }

        events, listings, _, _ = _extract_listing_events([message], previous)

        self.assertEqual(listings[0]["detailStatus"], "ok")
        self.assertEqual(listings[0]["address"], "中山北路283号")
        self.assertEqual(listings[0]["community"], "鲁迅园小区")
        self.assertEqual(listings[0]["detailFacts"]["payment_rule"], "押一付一")
        listing_event = next(data for event, data in events if event == "listings")
        self.assertEqual(listing_event["listings"][0]["detailStatus"], "ok")


class FakeAgent(SnapshotAgent):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.completed = False

    async def astream(self, payload: dict[str, Any], **kwargs: Any):
        if self.fail:
            raise RuntimeError("internal fake failure")
        self.calls.append(payload)
        self.stream_kwargs = kwargs
        call_id = "raw-call-id-must-not-be-forwarded"
        yield {
            "type": "messages",
            "data": (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_call_chunks": [
                        {
                            "id": call_id,
                            "name": "resolve_target_place",
                            "args": "secret raw arguments must-not-be-forwarded",
                        }
                    ],
                },
                {},
            ),
        }
        yield {
            "type": "messages",
            "data": ({**_location_message(), "tool_call_id": call_id}, {}),
        }
        yield {
            "type": "messages",
            "ns": ("internal-agent",),
            "data": ({"role": "ai", "content": "内部子图文本"}, {}),
        }
        yield {
            "type": "values",
            "ns": ("internal-agent",),
            "data": {"messages": [{"role": "assistant", "content": "内部子图最终文本"}]},
        }
        yield {"type": "messages", "data": ({"role": "ai", "content": "请确认"}, {})}
        yield {"type": "messages", "data": ({"role": "assistant", "content": "你要去的城市和地点。"}, {})}
        yield {
            "type": "values",
            "ns": ("internal-subgraph",),
            "data": {"messages": [{"role": "assistant", "content": "内部子图文本不能成为最终回复"}]},
        }
        messages = [*self.values.get("messages", []), *payload["messages"]]
        messages.append(_location_message())
        messages.append({"role": "assistant", "content": "请确认你要去的城市和地点。"})
        self.completed = True
        self.values = {**payload, "messages": messages}
        yield {"type": "values", "data": {"messages": messages}, "interrupts": ()}


class ListingFixtureAgent(SnapshotAgent):
    """发出搜索和详情工具消息，模拟离线房源链路。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls = []

    async def astream(self, payload: dict[str, Any], **kwargs: Any):
        del kwargs
        self.calls.append(payload)
        search_call_id = "provider-search-id"
        detail_call_id = "provider-detail-id"
        candidates = {
            "mode": "offline_fixture",
            "status": "ok",
            "listings": [{
                "listing_id": "fixture-1",
                "platform": "58同城",
                "title": "示例房源",
                "price": 1200,
                "room": "1室1厅",
                "listing_type": "整租",
                "area_sqm": 45,
                "community": "示例小区",
                "address": "江苏省示例市示例区示例路 168 号",
                "city": "cz",
                "detail_url": "https://example.58.com/zufang/fixture-1.html",
                "source_page": "https://example.58.com/zufang/",
                "tags": ["独立卫浴"],
            }],
            "internal": "do-not-forward-search",
        }
        details = {
            "mode": "offline_fixture",
            "status": "complete",
            "results": [{
                "url": "https://example.58.com/zufang/fixture-1.html",
                "status": "ok",
                "title": "示例房源详情",
                "facts": {
                    "monthly_rent_cny": 1250,
                    "payment_rule": "押一付一",
                    "agency_fee": "未收服务费",
                },
                "location": {
                    "community": "示例小区",
                    "address": "江苏省示例市示例区示例路 168 号",
                    "confidence": "detail",
                },
                "evidence": ["<html>secret raw page</html>"],
                "artifact_path": "D:/private/artifact.json",
            }],
        }
        yield {"type": "messages", "data": ({
            "role": "assistant", "content": "", "tool_call_chunks": [
                {"id": search_call_id, "name": "search_rental_candidates", "args": "secret-search-args"}
            ]
        }, {})}
        search_message = {
            "role": "tool", "name": "search_rental_candidates", "tool_call_id": search_call_id,
            "content": json.dumps(candidates, ensure_ascii=False),
        }
        yield {"type": "messages", "data": (search_message, {})}
        yield {"type": "messages", "data": ({
            "role": "assistant", "content": "", "tool_call_chunks": [
                {"id": detail_call_id, "name": "batch_fetch_listing_details", "args": "secret-detail-args"}
            ]
        }, {})}
        detail_message = {
            "role": "tool", "name": "batch_fetch_listing_details", "tool_call_id": detail_call_id,
            "content": json.dumps(details, ensure_ascii=False),
        }
        yield {"type": "messages", "data": (detail_message, {})}
        final = {"role": "assistant", "content": "我整理好了示例房源。"}
        messages = [*self.values.get("messages", []), *payload["messages"], search_message, detail_message, final]
        self.values = {**payload, "messages": messages}
        yield {"type": "messages", "data": (final, {})}
        yield {"type": "values", "data": {"messages": messages}, "interrupts": ()}


class RealOfflineToolAgent(SnapshotAgent):
    """用项目实际离线工具返回值驱动 runtime，覆盖完整夹具链路。"""

    async def astream(self, payload: dict[str, Any], **kwargs: Any):
        del kwargs
        from agent_core.batch_detail_tool import fetch_detail_batch
        from agent_core.candidate_search import search_rental_candidates

        candidates = json.loads(search_rental_candidates.invoke({
            "city": "cz", "keyword": "示例园区", "area": "wujin", "max_results": 10,
        }))
        details = fetch_detail_batch(candidates["detail_urls"], live=False, max_urls=10)
        search_id, detail_id = "offline-search", "offline-detail"
        search_message = {
            "role": "tool", "name": "search_rental_candidates", "tool_call_id": search_id,
            "content": json.dumps(candidates, ensure_ascii=False),
        }
        detail_message = {
            "role": "tool", "name": "batch_fetch_listing_details", "tool_call_id": detail_id,
            "content": json.dumps(details, ensure_ascii=False),
        }
        yield {"type": "messages", "data": ({
            "role": "assistant", "content": "", "tool_call_chunks": [
                {"id": search_id, "name": "search_rental_candidates", "args": "{}"}
            ]
        }, {})}
        yield {"type": "messages", "data": (search_message, {})}
        yield {"type": "messages", "data": ({
            "role": "assistant", "content": "", "tool_call_chunks": [
                {"id": detail_id, "name": "batch_fetch_listing_details", "args": "{}"}
            ]
        }, {})}
        yield {"type": "messages", "data": (detail_message, {})}
        final = {"role": "assistant", "content": "离线夹具搜索和详情读取完成。"}
        messages = [*payload["messages"], search_message, detail_message, final]
        self.values = {**payload, "messages": messages}
        yield {"type": "messages", "data": (final, {})}
        yield {"type": "values", "data": {"messages": messages}, "interrupts": ()}


def make_client(fake_agent: FakeAgent | None = None) -> tuple[TestClient, FakeAgent]:
    fake_agent = fake_agent or FakeAgent()
    runtime = AgentRuntime(agent_factory=lambda saver: fake_agent, store=CheckpointStore(StorageSettings()))
    return TestClient(create_app(runtime)), fake_agent


class TerminalThenFailureRuntime:
    """模拟 Agent 已持久化完成，但传输生成器随后关闭失败。"""

    def stream(self, *args, **kwargs):
        del args, kwargs

        async def events():
            yield AgentStreamEvent("turn_status", {"status": "completed"})
            raise RuntimeError("sensitive-close-failure")

        return events()


class ChatApiTests(unittest.TestCase):
    def test_region_scope_survives_details_enrichment_and_history_projection(self) -> None:
        listing = _listing_projection({
            "listing_id": "fang-region", "platform": "房天下", "title": "区域房源",
            "detail_url": "https://zu.fang.com/cz/chuzu/3_1_1.htm",
            "region_scope": "administrative_region", "region_scope_name": "武进",
            "region_evidence": "official_filter_page", "region_url": "https://zu.fang.com/cz/house-a0342/",
        })
        self.assertEqual(listing["regionScope"], "administrative_region")
        self.assertEqual(listing["regionScopeName"], "武进")
        self.assertEqual(listing["regionEvidence"], "official_filter_page")
        self.assertEqual(listing["locationStatus"], "unverified")
        self.assertIsNone(listing["distance"])
        self.assertNotIn("region_url", listing)
        merged = _merge_detail_into_listings([listing], {"details": [{
            "url": listing["detailUrl"], "status": "ok", "facts": {},
            "location": {"community": "测试小区", "address": "测试路8号"},
        }]})
        enriched = _safe_enrichment_payload({"status": "partial", "listings": merged})
        restored = _listing_projection(enriched["listings"][0])
        self.assertEqual(restored["address"], "测试路8号")
        self.assertEqual(restored["regionScope"], "administrative_region")
        self.assertEqual(restored["regionScopeName"], "武进")
        self.assertEqual(restored["regionEvidence"], "official_filter_page")
        self.assertEqual(restored["locationStatus"], "unverified")

    def test_region_scope_projection_rejects_unknown_enums_and_bounds_text(self) -> None:
        listing = _listing_projection({
            "title": "测试", "regionScope": "verified_nearby", "regionScopeName": "x" * 150,
            "regionEvidence": {"raw_html": "private"},
        })
        self.assertEqual(listing["regionScope"], "")
        self.assertEqual(listing["regionEvidence"], "")
        self.assertEqual(listing["regionScopeName"], "x" * 100)

    def test_listing_projection_keeps_sixty_candidates(self) -> None:
        projected = _safe_listing_search_payload({
            "mode": "live_skill",
            "status": "ok",
            "listings": [{
                "listing_id": f"listing-{index}",
                "platform": "58同城",
                "title": f"候选 {index}",
                "price": 1000 + index,
                "detail_url": f"https://example.58.com/zufang/{index}.shtml",
            } for index in range(65)],
        })

        self.assertEqual(len(projected["listings"]), 60)
        self.assertEqual(projected["listing_count"], 60)

    def test_detail_projection_keeps_list_pages_out_of_detail_results(self) -> None:
        projected = _safe_detail_batch_payload(json.dumps({
            "mode": "offline_fixture",
            "status": "complete",
            "results": [{
                "url": "https://example.zu.fang.com/house/wujin",
                "status": "ok",
                "title": "平台列表入口",
                "facts": {"monthly_rent_cny": 999, "payment_rule": "押一付一"},
            }],
        }, ensure_ascii=False))

        self.assertEqual(projected["details"][0]["status"], "source_list")
        self.assertEqual(projected["details"][0]["facts"], {})

    def test_detail_projection_preserves_skipped_after_platform_block(self) -> None:
        projected = _safe_detail_batch_payload({
            "mode": "live",
            "status": "partial",
            "results": [{
                "url": "https://example.58.com/zufang/skipped.shtml",
                "status": "skipped_after_block",
                "facts": {},
            }],
        })

        self.assertEqual(projected["details"][0]["status"], "skipped_after_block")
        self.assertEqual(projected["status"], "partial")

    def test_cross_city_platform_results_are_shown_as_failed(self) -> None:
        projected = _safe_listing_search_payload({
            "mode": "live_skill",
            "status": "partial",
            "platforms": {"58同城": "city_mismatch (0 条)"},
            "listings": [],
        })

        fifty_eight = next(item for item in projected["platforms"] if item["key"] == "58")
        self.assertEqual(fifty_eight["status"], "failed")
        self.assertEqual(fifty_eight["count"], 0)

    def test_listing_projection_marks_platform_entry_urls_as_source_lists(self) -> None:
        for url in (
            "https://cz.zf.58.com/",
            "https://cz.zu.anjuke.com/fangyuan/wujin/",
            "https://cz.zu.fang.com/house/wujin/a21/",
        ):
            with self.subTest(url=url):
                listing = _listing_projection({
                    "listing_id": "entry",
                    "platform": "房源平台",
                    "title": "入口候选",
                    "detail_url": url,
                })
                self.assertEqual(listing["urlType"], "source_list")
                self.assertIsNone(listing["detailUrl"])
                self.assertEqual(listing["sourceUrl"], url)

    def test_actual_offline_tools_flow_through_search_detail_events(self) -> None:
        from agent_core import candidate_search

        client, _ = make_client(RealOfflineToolAgent())
        with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "offline", "MAP_PROVIDER": "fake"}), patch.object(
            candidate_search, "_load_fixture", wraps=candidate_search._load_fixture
        ) as fixture_loader:
            response = client.post(
                "/api/v1/sessions/offline-chain/chat/stream",
                json={"message": "搜索示例园区附近房源", "client_request_id": "offline-chain"},
            )
        fixture_loader.assert_called_once()

        self.assertEqual(response.status_code, 200)
        events = [
            json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        listing_events = [item for item in events if "listings" in item and "platforms" not in item]
        detail_events = [item for item in events if "details" in item]
        self.assertEqual(len(listing_events), 3)  # 搜索、详情合并和地图增强快照
        self.assertEqual(len(detail_events), 1)
        self.assertEqual(len(listing_events[0]["listings"]), 3)
        self.assertEqual(listing_events[-1]["listings"][0]["detailStatus"], "ok")
        self.assertEqual(listing_events[-1]["listings"][0]["detailFacts"]["payment_rule"], "押一付一")
        self.assertEqual(listing_events[-1]["listings"][0]["commuteStatus"], "target_pending")
        state = client.get("/api/v1/sessions/offline-chain").json()
        self.assertEqual(len(state["listings"]), 3)
        self.assertTrue(all(item["offline"] for item in state["listings"]))

    def test_listing_search_and_details_are_safe_structured_events_and_persisted(self) -> None:
        fake_agent = ListingFixtureAgent()
        client, _ = make_client(fake_agent)
        response = client.post(
            "/api/v1/sessions/listing-events/chat/stream",
            json={"message": "找示例房源", "client_request_id": "listing-req"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: platform_status", response.text)
        self.assertIn("event: listings", response.text)
        self.assertIn("event: listing_details", response.text)
        self.assertIn("event: listing_update", response.text)
        self.assertEqual(response.text.count("event: platform_status"), 1)
        self.assertEqual(response.text.count("event: listings"), 1)
        self.assertEqual(response.text.count("event: listing_details"), 1)
        self.assertEqual(response.text.count("event: listing_update"), 1)
        self.assertNotIn("secret-search-args", response.text)
        self.assertNotIn("secret-detail-args", response.text)
        self.assertNotIn("secret raw page", response.text)
        self.assertNotIn("artifact_path", response.text)
        self.assertIn('"rent":1250', response.text)
        self.assertIn('"payment_rule":"押一付一"', response.text)

        state = client.get("/api/v1/sessions/listing-events").json()
        self.assertEqual(state["listings"][0]["rent"], 1250)
        self.assertEqual(state["listings"][0]["detailFacts"]["payment_rule"], "押一付一")
        self.assertEqual(state["platforms"][0]["key"], "58")
        self.assertEqual(state["search"]["detail_status"], "completed")
        self.assertNotIn("secret raw page", json.dumps(state, ensure_ascii=False))
        self.assertNotIn("artifact_path", json.dumps(state, ensure_ascii=False))

        replay = client.post(
            "/api/v1/sessions/listing-events/chat/stream",
            json={"message": "找示例房源", "client_request_id": "listing-req"},
        )
        self.assertIn('"status":"replayed"', replay.text)
        self.assertEqual(replay.text.count("event: listings"), 1)

    def test_listing_enrichment_is_emitted_and_persisted_with_confirmed_target(self) -> None:
        fake_agent = ListingFixtureAgent()
        runtime = AgentRuntime(
            agent_factory=lambda saver: fake_agent,
            store=CheckpointStore(StorageSettings()),
            map_service_factory=lambda: MapService(
                FakeMapProvider(), provider_name="fake", max_route_candidates=2
            ),
        )
        client = TestClient(create_app(runtime))
        response = client.post(
            "/api/v1/sessions/enriched-listings/chat/stream",
            json={
                "message": "预算2500元，整租一室，公交45分钟以内",
                "client_request_id": "enriched-req",
                "search_context": {
                    "confirmed_target": {
                        "candidate_ref": "target-1",
                        "name": "示例学院",
                        "formatted_address": "江苏省示例市示例区示例路 155 号",
                        "city": "示例市",
                        "district": "示例区",
                        "adcode": "000101",
                        "lng": 120.281,
                        "lat": 31.981,
                    }
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count("event: listing_enrichment"), 1)
        enrichment_block = next(
            block for block in response.text.split("\n\n")
            if any(line == "event: listing_enrichment" for line in block.splitlines())
        )
        enrichment_data = json.loads(next(
            line[6:] for line in enrichment_block.splitlines() if line.startswith("data: ")
        ))
        self.assertEqual(enrichment_data["target"]["lng"], 120.281)
        self.assertEqual(enrichment_data["listings"][0]["locationStatus"], "verified")
        self.assertEqual(enrichment_data["listings"][0]["detailLocation"]["confidence"], "detail")
        self.assertEqual(enrichment_data["listings"][0]["listingType"], "整租")
        self.assertEqual(enrichment_data["listings"][0]["filterStatus"], "passed")

        state = client.get("/api/v1/sessions/enriched-listings").json()
        self.assertEqual(state["map"]["target"]["lng"], 120.281)
        self.assertEqual(state["listings"][0]["locationStatus"], "verified")
        self.assertEqual(state["listings"][0]["detailLocation"]["address"], "江苏省示例市示例区示例路 168 号")
        self.assertEqual(state["search"]["enrichment_status"], "ok")
        self.assertEqual(state["search"]["criteria"]["layout"], "whole")

    def test_health_is_lazy_and_does_not_build_agent(self) -> None:
        created = 0

        def factory(saver) -> FakeAgent:
            nonlocal created
            created += 1
            return FakeAgent()

        client = TestClient(create_app(AgentRuntime(agent_factory=factory, store=CheckpointStore(StorageSettings()))))
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "runtime": "checkpoint_lazy",
                                          "storage": {"mode": "memory", "persistent": False}})
        self.assertEqual(created, 0)

    def test_delete_session_removes_history_and_rejects_missing_session(self) -> None:
        client, _ = make_client()
        created = client.post(
            "/api/v1/sessions/delete-me/chat/stream",
            json={"message": "测试删除", "client_request_id": "delete-request"},
        )
        self.assertEqual(created.status_code, 200)

        deleted = client.delete("/api/v1/sessions/delete-me")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted"], True)
        missing = client.get("/api/v1/sessions/delete-me")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "session_not_found")

        delete_missing = client.delete("/api/v1/sessions/delete-me")
        self.assertEqual(delete_missing.status_code, 404)
        invalid = client.delete("/api/v1/sessions/bad%24id")
        self.assertEqual(invalid.status_code, 422)

    def test_chat_stream_contains_location_and_assistant_events(self) -> None:
        client, fake_agent = make_client()
        response = client.post(
            "/api/v1/sessions/s-1/chat/stream",
            json={"message": "帮我找示例学院附近的房子", "client_request_id": "req-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("event: status", response.text)
        self.assertIn("event: token", response.text)
        self.assertIn("event: tool_start", response.text)
        self.assertIn("event: tool_end", response.text)
        self.assertIn("event: location_candidates", response.text)
        self.assertIn("event: assistant", response.text)
        self.assertIn("event: done", response.text)
        self.assertNotIn("must-not-be-forwarded", response.text)
        self.assertNotIn("raw-call-id", response.text)
        self.assertNotIn("内部子图文本", response.text)
        self.assertLess(response.text.index("event: tool_start"), response.text.index("event: tool_end"))
        self.assertLess(response.text.index("event: token"), response.text.index("event: assistant"))
        event_ids = [line[4:].strip() for line in response.text.splitlines() if line.startswith("id:")]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.assertGreaterEqual(len(event_ids), 8)
        self.assertEqual(len(fake_agent.calls), 1)
        self.assertEqual(fake_agent.calls[0]["messages"][-1]["content"], "帮我找示例学院附近的房子")
        self.assertEqual(fake_agent.stream_kwargs["stream_mode"], ["messages", "values"])
        self.assertTrue(fake_agent.stream_kwargs["subgraphs"])
        self.assertEqual(fake_agent.stream_kwargs["version"], "v2")

    def test_replying_with_candidate_number_binds_the_saved_target(self) -> None:
        client, fake_agent = make_client()
        first = client.post(
            "/api/v1/sessions/location-selection/chat/stream",
            json={
                "message": "帮我找示例学院附近的房子",
                "client_request_id": "location-1",
            },
        )
        second = client.post(
            "/api/v1/sessions/location-selection/chat/stream",
            json={
                "message": "1",
                "client_request_id": "location-2",
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertIn("event: location_candidates", first.text)
        self.assertEqual(second.status_code, 200)
        self.assertIn('"status":"resolved"', second.text)
        self.assertEqual(len(fake_agent.calls), 2)
        submitted = fake_agent.calls[1]["messages"][-1]["content"]
        self.assertTrue(submitted.startswith("1\n\n【应用提供的已确认找房上下文】"))
        self.assertIn('"candidate_ref":"pc_test"', submitted)

        state = client.get("/api/v1/sessions/location-selection").json()
        self.assertEqual(state["location"]["status"], "resolved")
        self.assertEqual(state["location"]["candidates"][0]["candidate_ref"], "pc_test")

    def test_session_history_is_retained_and_duplicate_request_is_replayed(self) -> None:
        client, fake_agent = make_client()
        first = client.post(
            "/api/v1/sessions/s-2/chat/stream",
            json={"message": "第一句", "client_request_id": "req-2"},
        )
        second = client.post(
            "/api/v1/sessions/s-2/chat/stream",
            json={"message": "第二句", "client_request_id": "req-3"},
        )
        replay = client.post(
            "/api/v1/sessions/s-2/chat/stream",
            json={"message": "第二句", "client_request_id": "req-3"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(len(fake_agent.calls), 2)
        self.assertEqual(len(fake_agent.calls[1]["messages"]), 1)
        self.assertGreaterEqual(len(fake_agent.values["messages"]), 6)
        self.assertEqual(fake_agent.stream_kwargs["config"]["configurable"]["thread_id"], "s-2")
        self.assertEqual(second.text.count("event: location_candidates"), 1)
        self.assertIn("status\":\"replayed", replay.text)

    def test_confirmed_target_is_added_as_structured_agent_context(self) -> None:
        client, fake_agent = make_client()
        response = client.post(
            "/api/v1/sessions/s-context/chat/stream",
            json={
                "message": "预算 2500 元，整租一室，通勤 45 分钟以内",
                "client_request_id": "req-context",
                "search_context": {
                    "confirmed_target": {
                        "candidate_ref": "pc_selected",
                        "name": "示例园区东区",
                        "formatted_address": "江苏省示例市示例区示例大道 163 号",
                        "city": "示例市",
                        "district": "示例区",
                        "adcode": "000000",
                        "lng": 118.959,
                        "lat": 32.116,
                    }
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        agent_content = fake_agent.calls[0]["messages"][-1]["content"]
        self.assertTrue(agent_content.startswith("预算 2500 元，整租一室，通勤 45 分钟以内"))
        self.assertIn("应用提供的已确认找房上下文", agent_content)
        self.assertIn("示例园区东区", agent_content)
        self.assertIn('"candidate_ref":"pc_selected"', agent_content)

    def test_confirmed_target_rejects_unknown_fields_and_unpaired_coordinates(self) -> None:
        client, _ = make_client()
        unknown_field = client.post(
            "/api/v1/sessions/s-context-invalid/chat/stream",
            json={
                "message": "继续",
                "search_context": {
                    "confirmed_target": {
                        "candidate_ref": "pc_selected",
                        "name": "测试地点",
                        "raw_provider_payload": "must-not-enter-agent",
                    }
                },
            },
        )
        unpaired_coordinates = client.post(
            "/api/v1/sessions/s-context-invalid/chat/stream",
            json={
                "message": "继续",
                "search_context": {
                    "confirmed_target": {
                        "candidate_ref": "pc_selected",
                        "name": "测试地点",
                        "lng": 118.9,
                    }
                },
            },
        )

        self.assertEqual(unknown_field.status_code, 422)
        self.assertEqual(unpaired_coordinates.status_code, 422)
        self.assertEqual(
            unknown_field.json(),
            {"error": {"code": "validation_error", "message": "请求参数不合法"}},
        )

    def test_duplicate_request_id_includes_confirmed_target_in_identity(self) -> None:
        client, fake_agent = make_client()
        base = {
            "message": "预算 2500 元",
            "client_request_id": "req-same-message",
            "search_context": {
                "confirmed_target": {
                    "candidate_ref": "pc_a",
                    "name": "地点 A",
                }
            },
        }
        first = client.post("/api/v1/sessions/s-context-cache/chat/stream", json=base)
        changed = {
            **base,
            "search_context": {
                "confirmed_target": {
                    "candidate_ref": "pc_test",
                    "name": "示例学院",
                }
            },
        }
        second = client.post("/api/v1/sessions/s-context-cache/chat/stream", json=changed)

        self.assertEqual(first.status_code, 200)
        self.assertIn("event: error", second.text)
        self.assertIn("duplicate_request_id", second.text)
        self.assertEqual(len(fake_agent.calls), 1)

    def test_invalid_message_uses_uniform_error_shape(self) -> None:
        client, _ = make_client()
        response = client.post("/api/v1/sessions/s-3/chat/stream", json={"message": "  "})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"error": {"code": "validation_error", "message": "请求参数不合法"}})

    def test_agent_failure_is_safe_sse_error(self) -> None:
        client, _ = make_client(FakeAgent(fail=True))
        response = client.post(
            "/api/v1/sessions/s-4/chat/stream",
            json={"message": "测试错误"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: error", response.text)
        self.assertIn("Agent 执行失败，请稍后重试", response.text)
        self.assertNotIn("internal fake failure", response.text)
        self.assertIn("event: done", response.text)

    def test_terminal_status_wins_over_late_stream_failure(self) -> None:
        client = TestClient(create_app(TerminalThenFailureRuntime()))
        response = client.post(
            "/api/v1/sessions/s-terminal/chat/stream",
            json={"message": "测试完成后关闭失败"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("event: error", response.text)
        self.assertIn('"status":"completed"', response.text)
        self.assertNotIn("sensitive-close-failure", response.text)

    def test_model_timeout_has_specific_safe_sse_error(self) -> None:
        client, _ = make_client(TimeoutAgent())
        response = client.post(
            "/api/v1/sessions/s-timeout/chat/stream",
            json={"message": "测试超时"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"code":"model_timeout"', response.text)
        self.assertIn("模型响应超时，请稍后重试。", response.text)
        self.assertNotIn("sensitive-provider", response.text)
        self.assertIn('"status":"error"', response.text)

    def test_invalid_session_id_uses_uniform_error_shape(self) -> None:
        client, _ = make_client()
        response = client.post(
            "/api/v1/sessions/bad%24id/chat/stream",
            json={"message": "测试"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"error": {"code": "invalid_session_id", "message": "session_id 格式无效"}})


class CancellableAgent(SnapshotAgent):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def astream(self, payload: dict[str, Any], **kwargs: Any):
        del payload, kwargs
        try:
            yield {"type": "messages", "data": ({"role": "ai", "content": "第一段"}, {})}
            await asyncio.Event().wait()
        finally:
            self.closed = True


class TimeoutAgent(SnapshotAgent):
    async def astream(self, payload: dict[str, Any], **kwargs: Any):
        del payload, kwargs
        if False:
            yield None
        raise TimeoutError("sensitive-provider-url-and-token")


class AgentRuntimeStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_tool_location_survives_without_root_tool_message(self) -> None:
        class ChildToolAgent(SnapshotAgent):
            async def astream(self, payload, **kwargs):
                yield {"type": "messages", "ns": ("child",), "data": (_location_message(), {})}
                self.values = {**payload, "messages": [
                    *payload["messages"], {"role": "assistant", "content": "子工具处理完毕"}
                ]}
        runtime = AgentRuntime(
            lambda saver: ChildToolAgent(), store=CheckpointStore(StorageSettings())
        )
        events = [event async for event in runtime.stream("child", "测试", "r1")]
        state = await runtime.get_session("child")
        self.assertEqual(state["location"]["candidates"][0]["candidate_ref"], "pc_test")
        self.assertEqual([event.event for event in events].count("location_candidates"), 1)
        self.assertNotIn("must-not-be-forwarded", json.dumps(state))

    async def test_langchain_tool_chunks_emit_safe_lifecycle_events(self) -> None:
        from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

        class ToolAgent(SnapshotAgent):
            async def astream(self, payload: dict[str, Any], **kwargs: Any):
                del kwargs
                call_id = "provider-internal-call-id"
                yield {
                    "type": "messages",
                    "ns": (),
                    "data": (
                        AIMessageChunk(
                            content="",
                            tool_call_chunks=[
                                {
                                    "id": call_id,
                                    "name": "resolve_target_place",
                                    "args": '{"query":"敏感原始参数"}',
                                    "index": 0,
                                }
                            ],
                        ),
                        {},
                    ),
                }
                tool_message = ToolMessage(
                    content=_location_message()["content"],
                    tool_call_id=call_id,
                    name="resolve_target_place",
                )
                yield {"type": "messages", "ns": (), "data": (tool_message, {})}
                messages = [*payload["messages"], tool_message, AIMessage(content="地点解析完成")]
                self.values = {**payload, "messages": messages}
                yield {"type": "values", "ns": (), "data": {"messages": messages}}

        runtime = AgentRuntime(agent_factory=lambda saver: ToolAgent(), store=CheckpointStore(StorageSettings()))
        events = [event async for event in runtime.stream("s-tool-shape", "测试工具流")]

        self.assertEqual(
            [event.event for event in events],
            ["tool_start", "tool_end", "location_candidates", "assistant", "turn_status"],
        )
        self.assertRegex(events[0].data["tool_call_id"], r"^tool_[0-9a-f]{12}$")
        self.assertEqual(events[0].data["tool_call_id"], events[1].data["tool_call_id"])
        serialized = json.dumps([event.data for event in events], ensure_ascii=False)
        self.assertNotIn("provider-internal-call-id", serialized)
        self.assertNotIn("敏感原始参数", serialized)

    async def test_real_langgraph_v2_message_shape_is_supported(self) -> None:
        from langchain_core.messages import AIMessage
        from langgraph.graph import END, START, MessagesState, StateGraph

        async def answer(state: MessagesState) -> dict[str, Any]:
            del state
            return {"messages": [AIMessage(content="真实 LangGraph 流事件")]}

        graph_builder = StateGraph(RentalAgentState)
        graph_builder.add_node("answer", answer)
        graph_builder.add_edge(START, "answer")
        graph_builder.add_edge("answer", END)
        runtime = AgentRuntime(
            agent_factory=lambda saver: graph_builder.compile(checkpointer=saver),
            store=CheckpointStore(StorageSettings()),
        )

        events = [event async for event in runtime.stream("s-real-graph", "测试")]

        self.assertEqual([event.event for event in events], ["token", "assistant", "turn_status"])
        self.assertEqual(events[0].data, {"text": "真实 LangGraph 流事件"})
        self.assertEqual(events[1].data, {"text": "真实 LangGraph 流事件"})

    async def test_token_arrives_before_agent_completion(self) -> None:
        fake_agent = FakeAgent()
        runtime = AgentRuntime(agent_factory=lambda saver: fake_agent, store=CheckpointStore(StorageSettings()))
        events = []

        async for event in runtime.stream("s-live", "测试真实流", "req-live"):
            events.append(event)
            if event.event == "token":
                self.assertFalse(fake_agent.completed)

        names = [event.event for event in events]
        self.assertIn("tool_start", names)
        self.assertIn("tool_end", names)
        self.assertIn("location_candidates", names)
        self.assertEqual(names[-2:], ["assistant", "turn_status"])
        self.assertTrue(fake_agent.completed)

    async def test_closing_client_stream_closes_agent_without_committing_turn(self) -> None:
        fake_agent = CancellableAgent()
        runtime = AgentRuntime(agent_factory=lambda saver: fake_agent, store=CheckpointStore(StorageSettings()))
        events = runtime.stream("s-cancel", "取消测试", "req-cancel")

        first = await events.__anext__()
        self.assertEqual(first.event, "token")
        await events.aclose()
        await asyncio.sleep(0)

        self.assertTrue(fake_agent.closed)
        document = runtime.store.get("s-cancel")
        self.assertEqual(document["status"], "running")
        self.assertEqual(document["requests"][-1]["status"], "running")
        self.assertNotIn("events", document["requests"][-1])

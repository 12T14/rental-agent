from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.agent_runtime import AgentRuntime
from app.main import create_app
from app.checkpoint_store import CheckpointStore, StorageSettings
from app.agent_state import RentalAgentState


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


def make_client(fake_agent: FakeAgent | None = None) -> tuple[TestClient, FakeAgent]:
    fake_agent = fake_agent or FakeAgent()
    runtime = AgentRuntime(agent_factory=lambda saver: fake_agent, store=CheckpointStore(StorageSettings()))
    return TestClient(create_app(runtime)), fake_agent


class ChatApiTests(unittest.TestCase):
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
                    "candidate_ref": "pc_b",
                    "name": "地点 B",
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
        self.assertIn("Agent 运行失败，请检查后端模型配置。", response.text)
        self.assertNotIn("internal fake failure", response.text)
        self.assertIn("event: done", response.text)

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

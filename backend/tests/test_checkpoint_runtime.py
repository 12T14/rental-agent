"""不依赖模型、浏览器、地图或数据库服务的真实 LangGraph 测试。"""

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent_runtime import AgentRuntime, SessionStateError, DuplicateRequestError
from app.agent_state import RentalAgentState
from app.checkpoint_store import CheckpointStore, StorageSettings, StorageUnavailable
from app.main import create_app


def graph_factory(calls, *, pause=True, fail_once=False, unknown=False):
    def build(saver):
        async def prepare(state):
            calls["prepare"] = calls.get("prepare", 0) + 1
            return {"messages": [AIMessage(content="地点已解析")]}

        async def ask(state):
            calls["ask"] = calls.get("ask", 0) + 1
            if fail_once and calls["ask"] == 1:
                raise TimeoutError("secret-provider-token")
            if pause:
                reply = interrupt(
                    {"type": "unknown", "secret": "hidden-cookie"} if unknown else
                    {"type": "rental_preferences", "missing_fields": ["budget", "commute"],
                     "raw_private": "hidden-cookie"}
                )
            else:
                reply = "条件齐全"
            return {"messages": [AIMessage(content=f"已收到：{reply}")]}

        builder = StateGraph(RentalAgentState)
        builder.add_node("prepare", prepare)
        builder.add_node("ask", ask)
        builder.add_edge(START, "prepare")
        builder.add_edge("prepare", "ask")
        builder.add_edge("ask", END)
        return builder.compile(checkpointer=saver)
    return build


def make_runtime(calls=None, **kwargs):
    return AgentRuntime(
        graph_factory(calls if calls is not None else {}, **kwargs),
        CheckpointStore(StorageSettings()),
    )


async def collect(runtime, *args, **kwargs):
    return [event async for event in runtime.stream(*args, **kwargs)]


class CheckpointRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_restart_resume_and_persistent_idempotency(self):
        calls = {}
        runtime = make_runtime(calls)
        first = await collect(runtime, "s1", "学校附近", "r1")
        self.assertEqual(first[-1].data["status"], "interrupted")
        self.assertNotIn("assistant", [event.event for event in first])
        pending = (await runtime.get_session("s1"))["pending"]
        self.assertNotIn("hidden-cookie", json.dumps(pending))

        # 新建运行时和新编译的图；共享 saver 只模拟运行时重建后存储仍在，
        # 不等同于真实进程持久化。
        resumed = AgentRuntime(graph_factory(calls), runtime.store)
        before = await resumed.get_session("s1")
        self.assertEqual(before["pending"], pending)
        second = await collect(
            resumed, "s1", "预算2000，公交30分钟", "r2", interrupt_id=pending["interrupt_id"]
        )
        self.assertEqual(second[-1].data["status"], "completed")
        self.assertIn("预算2000", second[-2].data["text"])
        self.assertEqual(calls, {"prepare": 1, "ask": 2})
        third = AgentRuntime(graph_factory(calls), runtime.store)
        replay = await collect(
            third, "s1", "预算2000，公交30分钟", "r2", interrupt_id=pending["interrupt_id"]
        )
        self.assertEqual(replay[0].data["status"], "replayed")
        self.assertEqual(calls, {"prepare": 1, "ask": 2})
        session = await third.get_session("s1")
        self.assertEqual([item["role"] for item in session["messages"]], ["user", "assistant", "user", "assistant"])
        self.assertNotIn("rental_request", json.dumps(session))
        self.assertNotIn("agent_text", json.dumps(session))
        self.assertIsNone(session["pending"])
        with self.assertRaises(DuplicateRequestError):
            await collect(third, "s1", "另一条回复", "r2", interrupt_id=pending["interrupt_id"])

    async def test_stale_cross_session_and_chat_during_pause_are_rejected(self):
        runtime = make_runtime()
        await collect(runtime, "s1", "学校", "r1")
        pending = (await runtime.get_session("s1"))["pending"]["interrupt_id"]
        for session_id, interrupt_id, expected in [
            ("s2", pending, "stale_interrupt"), ("s1", "fake-id", "stale_interrupt"),
            ("s1", None, "resume_required"),
        ]:
            with self.assertRaises(SessionStateError) as caught:
                await collect(runtime, session_id, "2000", "r2", interrupt_id=interrupt_id)
            self.assertEqual(caught.exception.code, expected)
        self.assertIsNone(runtime.store.get("s2"))

    async def test_failure_can_recover_without_repeating_completed_node(self):
        calls = {}
        runtime = make_runtime(calls, pause=False, fail_once=True)
        with self.assertRaises(TimeoutError):
            await collect(runtime, "fail", "条件齐全", "r1")
        view = await runtime.get_session("fail")
        self.assertEqual(view["status"], "recoverable")
        self.assertEqual(view["recovery_request_id"], "r1")
        with self.assertRaises(SessionStateError):
            await collect(runtime, "fail", "再发一次", "r2")
        result = await collect(runtime, "fail", "", "r1", recover=True)
        self.assertEqual(result[-1].data["status"], "completed")
        self.assertEqual(calls, {"prepare": 1, "ask": 2})
        self.assertEqual(len((await runtime.get_session("fail"))["messages"]), 2)

    async def test_crash_between_graph_finish_and_receipt_commit_is_reconciled(self):
        calls = {}
        runtime = make_runtime(calls, pause=False)
        original_save = runtime.store.save

        def fail_commit(document):
            if document["status"] == "completed":
                raise StorageUnavailable("simulated crash")
            original_save(document)

        with patch.object(runtime.store, "save", side_effect=fail_commit):
            with self.assertRaises(StorageUnavailable):
                await collect(runtime, "crash", "测试", "r1")
        recovered = AgentRuntime(graph_factory(calls, pause=False), runtime.store)
        result = await collect(recovered, "crash", "", "r1", recover=True)
        self.assertEqual(result[0].data["status"], "replayed")
        self.assertEqual(calls, {"prepare": 1, "ask": 1})
        self.assertEqual(len((await recovered.get_session("crash"))["messages"]), 2)

    async def test_reserved_but_not_started_request_is_recovered(self):
        calls = {}
        runtime = make_runtime(calls, pause=False)
        original = runtime._stream_graph

        async def fail_before_input(*args):
            raise TimeoutError("before graph starts")
            yield

        with patch.object(runtime, "_stream_graph", fail_before_input):
            with self.assertRaises(TimeoutError):
                await collect(runtime, "reserved", "第一句话", "r1")
        self.assertEqual(calls, {})
        await collect(runtime, "reserved", "", "r1", recover=True)
        self.assertEqual(calls, {"prepare": 1, "ask": 1})

    async def test_multiple_turns_pass_only_new_input_and_no_stale_interrupt_replay(self):
        calls = {}
        runtime = make_runtime(calls)
        await collect(runtime, "s1", "学校", "r1")
        pending = (await runtime.get_session("s1"))["pending"]["interrupt_id"]
        await collect(runtime, "s1", "不限", "r2", interrupt_id=pending)
        replay = await collect(runtime, "s1", "学校", "r1")
        self.assertNotIn("interrupt", [event.event for event in replay])
        self.assertEqual(replay[-1].data["status"], "completed")
        self.assertEqual(calls["prepare"], 1)

    async def test_unknown_interrupt_does_not_leak_payload(self):
        runtime = make_runtime(unknown=True)
        with self.assertRaises(SessionStateError) as caught:
            await collect(runtime, "s1", "学校", "r1")
        self.assertEqual(caught.exception.code, "unsupported_interrupt")
        self.assertNotIn("hidden-cookie", str(caught.exception))

    async def test_new_memory_store_does_not_claim_restart_persistence(self):
        runtime = make_runtime(pause=False)
        await collect(runtime, "s1", "你好", "r1")
        fresh = make_runtime(pause=False)
        self.assertEqual((await fresh.list_sessions())["sessions"], [])
        self.assertFalse(fresh.store.info["persistent"])

    async def test_legacy_session_without_listing_fields_remains_readable_and_writable(self):
        calls = {}
        runtime = make_runtime(calls, pause=False)
        runtime.store.save({
            "session_id": "legacy",
            "title": "旧会话",
            "status": "completed",
            "messages": [{"id": "old", "role": "assistant", "text": "旧回复"}],
        })

        view = await runtime.get_session("legacy")
        self.assertEqual(view["messages"][0]["text"], "旧回复")
        self.assertEqual(view["listings"], [])
        self.assertEqual(view["platforms"], [])
        self.assertIsNone(view["search"])
        self.assertIsNone(view["latest_request_id"])

        await collect(runtime, "legacy", "新要求", "r1")
        self.assertEqual((await runtime.get_session("legacy"))["status"], "completed")

    async def test_real_graph_cancellation_leaves_resumable_unfinished_node(self):
        started = asyncio.Event()
        calls = {"prepare": 0, "wait": 0}

        def factory(saver):
            async def prepare(state):
                calls["prepare"] += 1
                return {"messages": [AIMessage(content="已完成准备")]}

            async def wait(state):
                calls["wait"] += 1
                if calls["wait"] == 1:
                    started.set()
                    await asyncio.Event().wait()
                return {"messages": [AIMessage(content="恢复完成")]}

            graph = StateGraph(RentalAgentState)
            graph.add_node("prepare", prepare)
            graph.add_node("wait", wait)
            graph.add_edge(START, "prepare")
            graph.add_edge("prepare", "wait")
            graph.add_edge("wait", END)
            return graph.compile(checkpointer=saver)

        runtime = AgentRuntime(factory, CheckpointStore(StorageSettings()))
        task = asyncio.create_task(collect(runtime, "cancel", "测试", "r1"))
        await asyncio.wait_for(started.wait(), timeout=3)
        running = await asyncio.wait_for(runtime.get_session("cancel"), timeout=1)
        self.assertEqual(running["status"], "running")
        self.assertIsNone(running["recovery_request_id"])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((await runtime.get_session("cancel"))["status"], "recoverable")
        await collect(runtime, "cancel", "", "r1", recover=True)
        self.assertEqual(calls, {"prepare": 1, "wait": 2})

    async def test_real_deepagent_tool_interrupt_and_resume(self):
        from deepagents import create_deep_agent
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import ToolMessage
        from langchain_core.outputs import ChatResult, ChatGeneration
        from app.preference_tool import request_rental_preferences

        class ScriptedModel(BaseChatModel):
            @property
            def _llm_type(self):
                return "offline-script"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if isinstance(messages[-1], ToolMessage):
                    answer = AIMessage(content=f"继续处理：{messages[-1].content}")
                else:
                    answer = AIMessage(content="", tool_calls=[{
                        "id": "pref-call", "name": "request_rental_preferences",
                        "args": {"missing_fields": ["budget"]},
                    }])
                return ChatResult(generations=[ChatGeneration(message=answer)])

        runtime = AgentRuntime(
            lambda saver: create_deep_agent(
                model=ScriptedModel(), tools=[request_rental_preferences],
                state_schema=RentalAgentState, checkpointer=saver,
            ), CheckpointStore(StorageSettings()),
        )
        first = await collect(runtime, "deep", "学校附近", "r1")
        self.assertEqual(first[-1].data["status"], "interrupted")
        pending = (await runtime.get_session("deep"))["pending"]["interrupt_id"]
        result = await collect(runtime, "deep", "预算2000", "r2", interrupt_id=pending)
        self.assertEqual(result[-1].data["status"], "completed")
        self.assertIn("预算2000", result[-2].data["text"])

    async def test_duplicate_concurrent_requests_are_serialized(self):
        calls = {}
        runtime = make_runtime(calls, pause=False)
        results = await asyncio.gather(
            collect(runtime, "race", "你好", "r1"),
            collect(runtime, "race", "你好", "r1"),
        )
        self.assertEqual(calls, {"prepare": 1, "ask": 1})
        self.assertEqual(results[1][0].data["status"], "replayed")

    async def test_resume_second_pause_and_normal_chat_after_completion(self):
        calls = {}
        runtime = make_runtime(calls)
        for index in range(2):
            await collect(runtime, "multi", "换个地点", f"chat{index}")
            pending = (await runtime.get_session("multi"))["pending"]["interrupt_id"]
            await collect(runtime, "multi", "不限", f"resume{index}", interrupt_id=pending)
        self.assertEqual(calls["prepare"], 2)
        snapshot = await runtime._get_agent().aget_state(runtime._config("multi"))
        users = [message for message in snapshot.values["messages"] if message.type == "human"]
        self.assertEqual(len(users), 2)  # 恢复内容是工具回复，不应重复写入用户消息。

    async def test_location_projection_rejects_nested_raw_data_and_bounds_text(self):
        from app.agent_runtime import _safe_location_payload
        safe = _safe_location_payload(json.dumps({
            "message": {"cookie": "hidden"},
            "query": "x" * 5000,
            "candidates": [{"name": "school", "city": {"secret": "hidden"},
                            "formatted_address": "a" * 1000, "lng": float("inf"), "lat": True}],
        }))
        self.assertNotIn("hidden", json.dumps(safe))
        self.assertEqual(len(safe["query"]), 200)
        self.assertEqual(len(safe["candidates"][0]["formatted_address"]), 500)
        self.assertNotIn("lng", safe["candidates"][0])
        self.assertNotIn("lat", safe["candidates"][0])


class CheckpointApiTests(unittest.TestCase):
    def test_pause_read_resume_and_rename(self):
        runtime = make_runtime()
        with TestClient(create_app(runtime)) as client:
            reply = client.post("/api/v1/sessions/api1/chat/stream", json={
                "message": "学校附近", "client_request_id": "r1",
            })
            self.assertIn('event: interrupt', reply.text)
            self.assertIn('"status":"interrupted"', reply.text)
            self.assertNotIn('"status":"completed"', reply.text)
            state = client.get("/api/v1/sessions/api1").json()
            self.assertEqual(len(state["messages"]), 2)
            self.assertNotIn("hidden-cookie", json.dumps(state))
            response = client.post("/api/v1/sessions/api1/resume/stream", json={
                "message": "不限", "client_request_id": "r2",
                "interrupt_id": state["pending"]["interrupt_id"],
            })
            self.assertIn('"status":"completed"', response.text)
            client.patch("/api/v1/sessions/api1", json={"title": "我的找房"})
            self.assertEqual(client.get("/api/v1/sessions").json()["sessions"][0]["title"], "我的找房")
            for extra in ["checkpoint_id", "command", "update", "tool_args"]:
                bad = client.post("/api/v1/sessions/api1/resume/stream", json={
                    "message": "不限", "client_request_id": "r3", "interrupt_id": "i1",
                    extra: {"secret": "must-not-reflect"},
                })
                self.assertEqual(bad.status_code, 422)
                self.assertNotIn("must-not-reflect", bad.text)

    def test_mongo_failure_is_safe_and_does_not_fallback(self):
        store = CheckpointStore(StorageSettings("mongodb", "invalid-secret-uri", "rental_agent"))
        runtime = AgentRuntime(lambda saver: self.fail("must not build agent"), store)
        with TestClient(create_app(runtime)) as client:
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["storage"]["mode"], "mongodb")
            response = client.get("/api/v1/sessions")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("invalid-secret-uri", response.text)
            response = client.post("/api/v1/sessions/s1/chat/stream", json={"message": "测试"})
            self.assertIn("storage_unavailable", response.text)
            self.assertIsNone(store._saver)

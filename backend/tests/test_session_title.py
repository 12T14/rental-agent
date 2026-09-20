import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agent_runtime import AgentRuntime
from app.agent_state import RentalAgentState
from app.session_title import generate_session_title, normalize_session_title, sanitize_title_input


class SessionTitleTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_removes_wrappers_and_rejects_sensitive_output(self):
        self.assertEqual(normalize_session_title('标题："南京大学附近整租一室"。'), "南京大学附近整租一室")
        self.assertIsNone(normalize_session_title("无法生成标题"))
        self.assertEqual(normalize_session_title("南京大学附近 13800138000"), "南京大学附近")
        self.assertLessEqual(len(normalize_session_title("非常长的会话标题" * 20)), 32)

    def test_title_input_redacts_credentials_contact_details_and_exact_door_number(self):
        sanitized = sanitize_title_input(
            "南京大学汉口路22号附近找房，预算2500元，密码是secret-123，"
            "手机号 138 0013 8000，邮箱 user@example.com，API key=sk-live-secret"
        )

        self.assertIn("南京大学", sanitized)
        self.assertIn("预算2500元", sanitized)
        for secret in ("secret-123", "138 0013 8000", "user@example.com", "sk-live-secret"):
            self.assertNotIn(secret, sanitized)
        self.assertNotIn("22号", sanitized)

    async def test_model_title_is_short_and_safe(self):
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="标题：常州大学城 2000 元租房")))
        with patch("app.session_title.build_chat_model", return_value=model):
            title = await generate_session_title(
                "我想找常州大学城附近，预算 2000 元，整租或合租都可以，密码是secret-123"
            )

        self.assertEqual(title, "常州大学城 2000 元租房")
        model.ainvoke.assert_awaited_once()
        prompt = model.ainvoke.call_args.args[0][1]["content"]
        self.assertIn("常州大学城", prompt)
        self.assertNotIn("secret-123", prompt)

    async def test_model_failure_returns_none(self):
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("provider unavailable")))
        with patch("app.session_title.build_chat_model", return_value=model):
            self.assertIsNone(await generate_session_title("找房"))

    async def test_runtime_attempts_title_generation_only_once(self):
        calls = []

        async def generator(message):
            calls.append(message)
            return "示例地点租房"

        runtime = AgentRuntime(title_generator=generator)
        document = {"title": "首条需求兜底", "requests": [{"user_message": "找示例地点附近房源"}]}

        await runtime._maybe_generate_title(document)
        await runtime._maybe_generate_title(document)

        self.assertEqual(calls, ["找示例地点附近房源"])
        self.assertEqual(document["title"], "示例地点租房")
        self.assertEqual(document["title_source"], "model")

    async def test_failed_title_generation_is_marked_as_fallback_once(self):
        calls = 0

        async def generator(message):
            del message
            nonlocal calls
            calls += 1
            raise RuntimeError("temporary provider error")

        runtime = AgentRuntime(title_generator=generator)
        document = {"title": "首条需求兜底", "requests": [{"user_message": "找房"}]}

        await runtime._maybe_generate_title(document)
        await runtime._maybe_generate_title(document)

        self.assertEqual(calls, 1)
        self.assertEqual(document["title"], "首条需求兜底")
        self.assertEqual(document["title_source"], "fallback")

    async def test_background_title_does_not_delay_terminal_chat_receipt(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def generator(message):
            self.assertEqual(message, "常州大学城附近找房")
            started.set()
            await release.wait()
            return "常州大学城租房"

        def factory(saver):
            async def answer(state):
                del state
                return {"messages": [AIMessage(content="已完成")]}

            builder = StateGraph(RentalAgentState)
            builder.add_node("answer", answer)
            builder.add_edge(START, "answer")
            builder.add_edge("answer", END)
            return builder.compile(checkpointer=saver)

        runtime = AgentRuntime(agent_factory=factory, title_generator=generator)
        try:
            events = [event async for event in runtime.stream("async-title", "常州大学城附近找房", "r1")]
            self.assertEqual(events[-1].event, "turn_status")
            self.assertEqual(events[-1].data["status"], "completed")
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertNotEqual(runtime.store.get("async-title").get("title"), "常州大学城租房")
            release.set()
            for _ in range(20):
                if runtime.store.get("async-title").get("title_source") == "model":
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(runtime.store.get("async-title")["title"], "常州大学城租房")
        finally:
            release.set()
            await runtime.close()

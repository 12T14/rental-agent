"""仅在明确启用时运行；绝不读取应用真实环境或虚拟机的连接配置。"""

import os
import unittest
import uuid

from app.agent_runtime import AgentRuntime
from app.checkpoint_store import CheckpointStore, StorageSettings
from test_checkpoint_runtime import graph_factory, collect


@unittest.skipUnless(os.environ.get("RUN_MONGODB_TEST") == "1",
                     "MongoDB not installed/started by this task; opt in explicitly")
class MongoPersistenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_client_restores_pause_history_and_receipt(self):
        # 使用独立的固定测试数据库，清理范围仅限本次随机会话。
        settings = StorageSettings(
            "mongodb", os.environ.get("MONGODB_TEST_URI", "mongodb://127.0.0.1:27017"),
            "rental_agent_checkpoint_test",
        )
        session_id = f"test-{uuid.uuid4().hex}"
        calls = {}
        first_store = CheckpointStore(settings)
        second_store = CheckpointStore(settings)
        first = AgentRuntime(graph_factory(calls), first_store)
        second = AgentRuntime(graph_factory(calls), second_store)
        try:
            await collect(first, session_id, "学校", "r1")
            pending = (await first.get_session(session_id))["pending"]
            await first.close()
            state = await second.get_session(session_id)
            self.assertEqual(state["pending"], pending)
            self.assertEqual(len(state["messages"]), 2)
            result = await collect(second, session_id, "不限", "r2", interrupt_id=pending["interrupt_id"])
            self.assertEqual(result[-1].data["status"], "completed")
            await collect(second, session_id, "不限", "r2", interrupt_id=pending["interrupt_id"])
            self.assertEqual(calls, {"prepare": 1, "ask": 2})
        finally:
            # 不删除数据库或集合，也不触碰其他会话。
            if second_store._saver is not None:
                await second_store._saver.adelete_thread(session_id)
                second_store._collection.delete_one({"_id": session_id})
            first_store.close()
            second_store.close()

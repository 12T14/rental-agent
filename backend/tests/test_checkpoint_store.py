import copy
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from app.checkpoint_store import CheckpointStore, StorageSettings, StorageUnavailable


class MongoAdapterTests(unittest.TestCase):
    def test_lazy_initialization_options_journal_and_close(self):
        client = MagicMock()
        saver = MagicMock()
        driver = types.ModuleType("pymongo")
        driver.MongoClient = MagicMock(return_value=client)
        adapter = types.ModuleType("langgraph.checkpoint.mongodb")
        adapter.MongoDBSaver = MagicMock(return_value=saver)
        store = CheckpointStore(StorageSettings("mongodb", "mongodb://localhost:27017", "unit_test"))
        driver.MongoClient.assert_not_called()
        with patch.dict(sys.modules, {"pymongo": driver, "langgraph.checkpoint.mongodb": adapter}):
            self.assertIs(store.open(), saver)
            self.assertIs(store.open(), saver)
            driver.MongoClient.assert_called_once()
            client.admin.command.assert_called_once_with("ping")
            self.assertEqual(driver.MongoClient.call_args.kwargs["serverSelectionTimeoutMS"], 3000)
            adapter.MongoDBSaver.assert_called_once_with(
                client, db_name="unit_test", checkpoint_collection_name="agent_checkpoints",
                writes_collection_name="agent_checkpoint_writes",
            )
            collection = client["unit_test"]["agent_sessions"]
            document = {"session_id": "s1", "title": "test", "updated_at": 1, "status": "completed"}
            store.save(document)
            collection.replace_one.assert_called_once_with(
                {"_id": "s1"}, {"_id": "s1", **document}, upsert=True
            )
            collection.find_one.return_value = {"_id": "s1", **document}
            self.assertEqual(store.get("s1"), document)
            store.close()
            client.close.assert_called_once()

    def test_connection_error_is_bounded_safe_and_closes_client(self):
        client = MagicMock()
        client.admin.command.side_effect = RuntimeError("secret-uri")
        driver = types.ModuleType("pymongo")
        driver.MongoClient = MagicMock(return_value=client)
        adapter = types.ModuleType("langgraph.checkpoint.mongodb")
        adapter.MongoDBSaver = MagicMock()
        store = CheckpointStore(StorageSettings("mongodb", "mongodb://localhost:27017", "unit_test"))
        with patch.dict(sys.modules, {"pymongo": driver, "langgraph.checkpoint.mongodb": adapter}):
            with self.assertRaises(StorageUnavailable) as caught:
                store.open()
        self.assertNotIn("secret-uri", str(caught.exception))
        self.assertIsNone(store._saver)
        client.close.assert_called_once()
        adapter.MongoDBSaver.assert_not_called()

    def test_invalid_config_does_not_import_driver(self):
        for settings in [StorageSettings("typo"), StorageSettings("mongodb", "", "unit"),
                         StorageSettings("mongodb", "mongodb://localhost", "../private")]:
            with self.subTest(mode=settings.mode):
                with self.assertRaises(StorageUnavailable):
                    CheckpointStore(settings).open()

    def test_memory_roundtrip_is_copied_and_paginated(self):
        store = CheckpointStore(StorageSettings())
        document = {"session_id": "s1", "title": "one", "updated_at": 1,
                    "status": "completed", "private": "not-in-list"}
        store.save(document)
        document["title"] = "changed"
        self.assertEqual(store.get("s1")["title"], "one")
        loaded = store.get("s1")
        loaded["title"] = "changed-again"
        self.assertEqual(store.get("s1")["title"], "one")
        store.save({**document, "session_id": "s2", "updated_at": 2})
        self.assertEqual(store.list(1)[0]["session_id"], "s2")
        self.assertEqual(store.list(1, 1)[0]["session_id"], "s1")
        self.assertNotIn("private", store.list()[0])

"""单个本地服务进程使用的延迟检查点与轻量展示日志。

日志由应用维护。绝不要通过 HTTP 暴露原始图检查点、Mongo 文档、连接字符串、
工具参数或序列化器数据。
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver


class StorageUnavailable(RuntimeError):
    """安全错误边界；底层异常不得传到 HTTP。"""


@dataclass(frozen=True)
class StorageSettings:
    mode: str = "memory"
    uri: str = ""
    database: str = "rental_agent"

    @classmethod
    def from_env(cls) -> "StorageSettings":
        # 只加载存储相关键，不构造模型，也不读取并输出密钥。
        # 进程环境变量优先于现有 .env 文件。
        allowed = {"CHECKPOINT_BACKEND", "MONGODB_URI", "MONGODB_DATABASE"}
        values = {}
        path = Path(__file__).resolve().parents[1] / ".env"
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip() in allowed:
                    values[key.strip()] = value.strip().strip("'\"")
        values.update({key: os.environ[key] for key in allowed if key in os.environ})
        return cls(
            mode=values.get("CHECKPOINT_BACKEND", "memory").strip().lower(),
            uri=values.get("MONGODB_URI", "").strip(),
            database=values.get("MONGODB_DATABASE", "rental_agent").strip(),
        )


class CheckpointStore:
    def __init__(self, settings: StorageSettings | None = None) -> None:
        self.settings = settings or StorageSettings.from_env()
        self._saver = None
        self._client = None
        self._collection = None
        self._documents: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    @property
    def info(self) -> dict[str, Any]:
        return {
            "mode": self.settings.mode,
            "persistent": self.settings.mode == "mongodb",
        }

    def open(self):
        with self._lock:
            if self._saver is not None:
                return self._saver
            if self.settings.mode == "memory":
                self._saver = InMemorySaver()
                return self._saver
            if (
                self.settings.mode != "mongodb"
                or not self.settings.uri.startswith(("mongodb://", "mongodb+srv://"))
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,62}", self.settings.database)
            ):
                raise StorageUnavailable("checkpoint 存储配置无效")
            client = None
            try:
                from pymongo import MongoClient
                from langgraph.checkpoint.mongodb import MongoDBSaver

                client = MongoClient(
                    self.settings.uri,
                    serverSelectionTimeoutMS=3000,
                    connectTimeoutMS=3000,
                    socketTimeoutMS=5000,
                    timeoutMS=5000,
                    tz_aware=True,
                )
                client.admin.command("ping")
                saver = MongoDBSaver(
                    client,
                    db_name=self.settings.database,
                    checkpoint_collection_name="agent_checkpoints",
                    writes_collection_name="agent_checkpoint_writes",
                )
                collection = client[self.settings.database]["agent_sessions"]
                collection.create_index([("updated_at", -1), ("_id", 1)])
                self._client, self._saver, self._collection = client, saver, collection
            except Exception:
                if client is not None:
                    client.close()
                raise StorageUnavailable("MongoDB 不可用或 Python 存储依赖未安装") from None
            return self._saver

    def get(self, session_id: str) -> dict[str, Any] | None:
        self.open()
        try:
            if self._collection is not None:
                result = self._collection.find_one({"_id": session_id})
                if result:
                    result.pop("_id", None)
                return result
            with self._lock:
                return copy.deepcopy(self._documents.get(session_id))
        except Exception:
            raise StorageUnavailable("无法读取会话存储") from None

    def save(self, document: dict[str, Any]) -> None:
        self.open()
        try:
            session_id = document["session_id"]
            if self._collection is not None:
                self._collection.replace_one(
                    {"_id": session_id}, {"_id": session_id, **document}, upsert=True
                )
            else:
                with self._lock:
                    self._documents[session_id] = copy.deepcopy(document)
        except Exception:
            raise StorageUnavailable("无法保存会话存储") from None

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        self.open()
        try:
            if self._collection is not None:
                return list(
                    self._collection.find(
                        {}, {"_id": 0, "session_id": 1, "title": 1, "updated_at": 1, "status": 1}
                    ).sort([("updated_at", -1), ("_id", 1)]).skip(offset).limit(limit)
                )
            with self._lock:
                documents = sorted(
                    self._documents.values(), key=lambda item: (-item["updated_at"], item["session_id"])
                )
            return [
                {key: item[key] for key in ("session_id", "title", "updated_at", "status")}
                for item in documents[offset:offset + limit]
            ]
        except Exception:
            raise StorageUnavailable("无法读取会话列表") from None

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = self._saver = self._collection = None

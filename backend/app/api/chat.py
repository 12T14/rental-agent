"""本地单用户 Agent 桥接层的 SSE 聊天路由。"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..agent_runtime import AgentRuntime, DuplicateRequestError, SessionStateError
from ..checkpoint_store import StorageUnavailable
from ..schemas import ChatRequest, ResumeRequest, RecoverRequest, RenameSessionRequest


router = APIRouter()
logger = logging.getLogger(__name__)
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TIMEOUT_ERROR_NAMES = {
    "TimeoutError",
    "APITimeoutError",
    "ConnectTimeout",
    "ReadTimeout",
    "TimeoutException",
}


def _runtime_from_request(request: Request) -> AgentRuntime:
    return request.app.state.agent_runtime


def _sse(event: str, data: dict[str, Any], event_id: str) -> str:
    return (
        f"id: {event_id}\n"
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_session_id", "message": "session_id 格式无效"},
        )


def _is_timeout_error(error: BaseException) -> bool:
    """识别超时包装异常，但不检查或暴露异常文本。"""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in _TIMEOUT_ERROR_NAMES:
            return True
        current = current.__cause__ or current.__context__
    return False


def _is_storage_error(error: BaseException) -> bool:
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, StorageUnavailable) or type(current).__module__.startswith("pymongo."):
            return True
        current = current.__cause__ or current.__context__
    return False


@router.post("/sessions/{session_id}/chat/stream")
async def chat_stream(
    session_id: str,
    body: ChatRequest,
    request: Request,
    runtime: AgentRuntime = Depends(_runtime_from_request),
) -> StreamingResponse:
    """发送一条消息，并将安全的 Agent 事件流式传给前端。"""

    _validate_session_id(session_id)
    return _stream_response(
        session_id, request,
        runtime.stream(
            session_id, body.message, body.client_request_id,
            body.search_context.model_dump(mode="json", exclude_none=True) if body.search_context else None,
        ),
    )


@router.post("/sessions/{session_id}/resume/stream")
async def resume_stream(
    session_id: str, body: ResumeRequest, request: Request,
    runtime: AgentRuntime = Depends(_runtime_from_request),
):
    _validate_session_id(session_id)
    return _stream_response(
        session_id, request,
        runtime.stream(
            session_id, body.message, body.client_request_id,
            body.search_context.model_dump(mode="json", exclude_none=True) if body.search_context else None,
            interrupt_id=body.interrupt_id,
        ),
    )


@router.post("/sessions/{session_id}/recover/stream")
async def recover_stream(
    session_id: str, body: RecoverRequest, request: Request,
    runtime: AgentRuntime = Depends(_runtime_from_request),
):
    _validate_session_id(session_id)
    return _stream_response(
        session_id, request,
        runtime.stream(session_id, "", body.client_request_id, recover=True),
    )


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
    runtime: AgentRuntime = Depends(_runtime_from_request),
):
    return await runtime.list_sessions(limit, offset)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, runtime: AgentRuntime = Depends(_runtime_from_request)):
    _validate_session_id(session_id)
    return await runtime.get_session(session_id)


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str, body: RenameSessionRequest,
    runtime: AgentRuntime = Depends(_runtime_from_request),
):
    _validate_session_id(session_id)
    return await runtime.rename_session(session_id, body.title)


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    runtime: AgentRuntime = Depends(_runtime_from_request),
):
    _validate_session_id(session_id)
    return await runtime.delete_session(session_id)


def _stream_response(session_id: str, request: Request, agent_events) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        stream_id = uuid.uuid4().hex
        sequence = 0

        def encode(event: str, data: dict[str, Any]) -> str:
            nonlocal sequence
            sequence += 1
            return _sse(event, data, f"{stream_id}:{sequence}")

        yield encode(
            "status",
            {"status": "running", "session_id": session_id, "stream_id": stream_id},
        )
        outcome: str | None = None
        try:
            async for item in agent_events:
                if await request.is_disconnected():
                    return
                if item.event == "turn_status":
                    outcome = item.data["status"]
                    continue
                data = dict(item.data)
                if item.event == "status":
                    data["session_id"] = session_id
                yield encode(item.event, data)
            if await request.is_disconnected():
                return
            yield encode("done", {"status": outcome or "completed", "session_id": session_id})
        except DuplicateRequestError:
            if not await request.is_disconnected():
                yield encode(
                    "error",
                    {
                        "error": {
                            "code": "duplicate_request_id",
                            "message": "client_request_id 已被其他请求内容使用",
                        }
                    },
                )
                yield encode("done", {"status": "error", "session_id": session_id})
        except Exception as exc:
            # 不要发送服务提供方的异常文本：其中可能包含凭据、URL、
            # 本地路径或模型请求细节。
            if not await request.is_disconnected():
                if outcome in {"completed", "interrupted"}:
                    logger.warning(
                        "Agent event stream ended after terminal status stream_id=%s error_type=%s status=%s",
                        stream_id,
                        type(exc).__name__,
                        outcome,
                    )
                    yield encode("done", {"status": outcome, "session_id": session_id})
                elif _is_storage_error(exc):
                    code, message = "storage_unavailable", "会话存储不可用，请检查存储模式、MongoDB 和 Python 依赖。"
                elif isinstance(exc, SessionStateError):
                    code, message = exc.code, exc.message
                elif _is_timeout_error(exc):
                    code = "model_timeout"
                    message = "模型响应超时，请稍后重试。可先刷新会话，再恢复上次任务。"
                else:
                    code = "agent_error"
                    message = "Agent 执行失败，请稍后重试；可以刷新会话查看已保存的进度。"
                if outcome not in {"completed", "interrupted"}:
                    logger.warning(
                        "Agent event stream failed stream_id=%s error_type=%s",
                        stream_id,
                        type(exc).__name__,
                    )
                    yield encode(
                        "error",
                        {"error": {"code": code, "message": message}},
                    )
                    yield encode("done", {"status": "error", "session_id": session_id})
        finally:
            close = getattr(agent_events, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception as exc:
                    logger.warning(
                        "Agent event stream close failed stream_id=%s error_type=%s",
                        stream_id,
                        type(exc).__name__,
                    )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]

"""本地租房 Agent 桥接服务的 FastAPI 入口。"""

from __future__ import annotations

from typing import Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .agent_runtime import AgentRuntime, SessionStateError
from .checkpoint_store import StorageUnavailable
from .api.chat import router as chat_router


def create_app(runtime: AgentRuntime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        yield
        await app.state.agent_runtime.close()

    app = FastAPI(title="通勤找房 Agent API", version="0.2.0", lifespan=lifespan)
    app.state.agent_runtime = runtime or AgentRuntime()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        # 运行时按需加载，因此健康检查不会构造模型或调用服务提供方。
        # 这是为了支持本地启动检查。
        return {"status": "ok", "runtime": "checkpoint_lazy", "storage": app.state.agent_runtime.store.info}

    @app.exception_handler(StorageUnavailable)
    async def storage_error_handler(request, exc):
        return JSONResponse(status_code=503, content={"error": {
            "code": "storage_unavailable", "message": "会话存储不可用，请检查存储配置和 MongoDB。"
        }})

    @app.exception_handler(SessionStateError)
    async def session_error_handler(request, exc):
        return JSONResponse(
            status_code=404 if exc.code == "session_not_found" else 409,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        del request, exc
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "请求参数不合法",
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        del request
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code") or "http_error")
            message = str(detail.get("message") or "请求失败")
        else:
            code = "http_error"
            message = str(detail or "请求失败")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
        )

    app.include_router(chat_router, prefix="/api/v1")
    return app


app = create_app()


__all__ = ["app", "create_app"]

"""轻量 FastAPI 桥接层的 HTTP 请求模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfirmedTarget(BaseModel):
    """用户在地图界面明确选中的标准化地点。"""

    model_config = ConfigDict(extra="forbid")

    candidate_ref: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    formatted_address: str = Field(default="", max_length=500)
    city: str = Field(default="", max_length=100)
    district: str = Field(default="", max_length=100)
    adcode: str = Field(default="", max_length=20, pattern=r"^[0-9]*$")
    lng: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)

    @field_validator("name", "formatted_address", "city", "district", "adcode")
    @classmethod
    def trim_text_fields(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def coordinates_must_be_paired(self) -> "ConfirmedTarget":
        if (self.lng is None) != (self.lat is None):
            raise ValueError("lng 和 lat 必须同时提供")
        return self


class SearchContext(BaseModel):
    """结构化搜索条件，不应渲染为聊天气泡。"""

    model_config = ConfigDict(extra="forbid")

    confirmed_target: ConfirmedTarget | None = None


class ChatRequest(BaseModel):
    """发送到会话级 Agent 对话的一条用户消息。"""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=4000)
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    search_context: SearchContext | None = None

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message 不能为空")
        return value

    @field_validator("client_request_id")
    @classmethod
    def request_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ResumeRequest(ChatRequest):
    interrupt_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    client_request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")


class RecoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")


class RenameSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=32)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()

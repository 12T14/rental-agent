"""为会话生成一次性的短标题。

标题是展示辅助信息，不得影响 Agent 主流程；模型不可用时由调用方保留兜底标题。
"""

from __future__ import annotations

import re
from typing import Any

try:
    from ..agent_core.config import build_chat_model
except ImportError:  # pragma: no cover - backend 目录作为 sys.path 根运行时使用。
    from agent_core.config import build_chat_model


TITLE_PROMPT = """请把下面这条租房需求概括成一个简短的中文会话标题。
只输出标题本身，不要解释、不要加引号、不要加“标题：”。
最多 20 个汉字或 32 个字符。
优先保留目标地点；如果原文明确包含预算或户型，可以择一加入。
不要输出完整门牌号、电话号码、API Key、Cookie 或其他敏感信息。

用户需求：
"""


_CREDENTIAL_LABEL = (
    r"api[\s_-]*key|access[\s_-]*token|refresh[\s_-]*token|"
    r"authorization|cookie|set[\s_-]*cookie|password|passwd|secret|token|"
    r"密码|口令|密钥|令牌|凭证"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    rf"(?i)({_CREDENTIAL_LABEL})\s*(?:是|为|[:=：])\s*[^\s,，。；;]+"
)
_COOKIE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:cookie|set-cookie)\s*[:=：]\s*"
    r"(?:[A-Za-z0-9_.-]+\s*=\s*[^\s;]+(?:;\s*)?)+"
)
_BEARER_TOKEN = re.compile(
    r"(?i)\b(?:authorization\s+)?bearer\s+[A-Za-z0-9._~+/=-]+"
)
_PHONE_NUMBER = re.compile(r"(?<!\d)1[3-9](?:[\s-]?\d){9}(?!\d)")
_EMAIL_ADDRESS = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_OPAQUE_API_KEY = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})(?![A-Za-z0-9])"
)
_DOOR_NUMBER = re.compile(r"(?<!\d)\d{1,5}\s*号(?!线)")


def sanitize_title_input(value: str, max_length: int = 1200) -> str:
    """删除不需要进入标题模型的凭据、联系方式和精确门牌信息。"""

    text = value if isinstance(value, str) else ""
    text = _COOKIE_ASSIGNMENT.sub("Cookie=[已隐藏]", text)
    text = _BEARER_TOKEN.sub("Authorization=[已隐藏]", text)
    text = _CREDENTIAL_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[已隐藏]", text)
    text = _OPAQUE_API_KEY.sub("[密钥已隐藏]", text)
    text = _PHONE_NUMBER.sub("[手机号已隐藏]", text)
    text = _EMAIL_ADDRESS.sub("[邮箱已隐藏]", text)
    text = re.sub(r"https?://[^\s，。；;]+", "[链接已隐藏]", text, flags=re.IGNORECASE)
    text = _DOOR_NUMBER.sub("[门牌号]", text)
    return text[:max_length]


def _content_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(content or "")


def normalize_session_title(value: str, max_length: int = 32) -> str | None:
    text = re.sub(r"```(?:text|markdown)?", "", value or "", flags=re.IGNORECASE)
    text = text.replace("```", "").strip()
    text = re.sub(r"^(?:标题|会话标题)\s*[:：]\s*", "", text)
    text = text.strip().strip(" 。.，,：:;；\"'“”‘’")
    text = re.sub(r"\s+", " ", text).strip(" 。.，,：:;；")
    text = sanitize_title_input(text, max_length=max_length)
    text = re.sub(r"\[[^\]]*已隐藏\]", "", text).strip(" 。.，,：:;；")
    if not text:
        return None
    if any(marker in text.lower() for marker in ("api key", "cookie", "无法生成", "cannot")):
        return None
    return text[:max_length]


def fallback_session_title(message: str, max_length: int = 32) -> str:
    """生成不依赖模型的安全兜底标题。"""

    text = sanitize_title_input(message, max_length=1200)
    text = re.sub(r"\[[^\]]*(?:已隐藏|门牌号)[^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" 。.，,：:;；\"'“”‘’")
    return text[:max_length] or "新建会话"


async def generate_session_title(message: str) -> str | None:
    """调用当前主模型一次生成展示标题；任何异常都转换为无标题结果。"""

    try:
        model = build_chat_model("main")
        response = await model.ainvoke([
            {"role": "system", "content": "你是一个只负责生成会话标题的助手。"},
            {"role": "user", "content": TITLE_PROMPT + sanitize_title_input(message)},
        ])
        return normalize_session_title(_content_text(response))
    except Exception:
        return None

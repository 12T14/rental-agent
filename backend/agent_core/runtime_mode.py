"""房源数据源的统一选择；夹具只能通过显式 offline 配置启用。"""

from __future__ import annotations

import os
from typing import Literal


DEFAULT_RENTAL_MODE = "live"


def rental_mode() -> Literal["live", "offline"]:
    """未配置或留空时使用真实搜索；拼写错误不得静默落入夹具分支。"""
    value = os.getenv("RENTAL_DEMO_MODE", "").strip().lower() or DEFAULT_RENTAL_MODE
    if value not in {"live", "offline"}:
        raise ValueError("RENTAL_DEMO_MODE 只能是 live（真实搜索）或 offline（离线测试）。")
    return value

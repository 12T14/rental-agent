"""本地租房 Agent 的人工参与浏览器验证工具。

工具支持 58 同城、安居客和房天下的公开列表/搜索与受限详情验证流程。
它会打开可见的 Playwright 浏览器，等待用户完成平台自己的验证页面，
并保存按平台隔离的会话状态；不会破解或绕过验证码。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from langchain_core.tools import tool

from .runtime_mode import rental_mode

AGENT_CORE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_CORE_ROOT.parent
SKILL_ROOT = PROJECT_ROOT / "skills" / "rental-scraper"
DEFAULT_58_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "58" / "storage_state.json"
DEFAULT_ANJUKE_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "anjuke" / "storage_state.json"
DEFAULT_FANG_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "fang" / "storage_state.json"
_LAST_ATTEMPT_AT: dict[str, float] = {}
VERIFICATION_COOLDOWN_SECONDS = 300
PLATFORM_NAMES = {"58": "58同城", "anjuke": "安居客", "fang": "房天下"}


def _normalize_platform(value: str) -> str:
    raw = (value or "").strip().lower().replace(" ", "")
    aliases = {
        "58": "58", "58同城": "58", "58tongcheng": "58",
        "安居客": "anjuke", "anjuke": "anjuke",
        "房天下": "fang", "fang": "fang", "房天下租房": "fang",
    }
    return aliases.get(raw, raw)


def _normalize_phase(value: str) -> str:
    raw = (value or "search").strip().lower().replace("_", "-")
    if raw in {"search", "list", "listing-search"}:
        return "search"
    if raw in {"detail", "details", "listing-detail"}:
        return "detail"
    return raw


def _session_path(platform: str) -> Path:
    defaults = {
        "58": DEFAULT_58_SESSION,
        "anjuke": DEFAULT_ANJUKE_SESSION,
        "fang": DEFAULT_FANG_SESSION,
    }
    env_names = {
        "58": "RENTAL_58_SESSION_FILE",
        "anjuke": "RENTAL_ANJUKE_SESSION_FILE",
        "fang": "RENTAL_FANG_SESSION_FILE",
    }
    return Path(os.getenv(env_names[platform], str(defaults[platform]))).expanduser()


def _platform_url(platform: str, city: str, area: str, keyword: str) -> str:
    sys.path.insert(0, str(SKILL_ROOT))
    from scrape_58 import _make_url as url_58
    from scrape_anjuke import _make_url as url_anjuke
    from scrape_fang import _make_url as url_fang

    return {"58": url_58, "anjuke": url_anjuke, "fang": url_fang}[platform](city, area, keyword)


def _allowed_platform_url(platform: str, value: str) -> bool:
    if not value:
        return True
    sys.path.insert(0, str(SKILL_ROOT))
    from platform_pages import allowed_platform_url

    return allowed_platform_url(platform, value)


def _create_generic_session(platform: str, city: str, area: str, keyword: str,
                            session_file: Path, wait_seconds: int, target_url: str = "") -> dict:
    """复用三平台共同的可见浏览器验证实现。"""
    url = target_url or _platform_url(platform, city, area, keyword)
    if not _allowed_platform_url(platform, url):
        return {"status": "rejected", "platform": PLATFORM_NAMES[platform],
                "message": "target_url 不属于该平台允许的 HTTPS 域名。"}
    sys.path.insert(0, str(SKILL_ROOT))
    from platform_pages import create_platform_session

    return create_platform_session(platform, url, str(session_file), wait_seconds) | {"platform": PLATFORM_NAMES[platform]}


def _verify_58_session(city: str, area: str, keyword: str, session_file: Path,
                       wait_seconds: int, target_url: str = "") -> dict:
    """运行现有的 58 同城专用可见浏览器验证流程。"""
    sys.path.insert(0, str(SKILL_ROOT))
    from scrape_58 import create_58_session

    result = create_58_session(
        city=city,
        area=area,
        keyword=keyword,
        session_file=str(session_file),
        wait_seconds=wait_seconds,
        target_url=target_url,
    )
    result["platform"] = PLATFORM_NAMES["58"]
    return result


def _verify_anjuke_session(city: str, area: str, keyword: str, session_file: Path,
                           wait_seconds: int, target_url: str = "") -> dict:
    return _create_generic_session("anjuke", city, area, keyword, session_file, wait_seconds, target_url)


def _verify_fang_session(city: str, area: str, keyword: str, session_file: Path,
                         wait_seconds: int, target_url: str = "") -> dict:
    return _create_generic_session("fang", city, area, keyword, session_file, wait_seconds, target_url)


@tool
def human_verify_rental_platform(
    platform: str,
    city: str = "",
    area: str = "",
    keyword: str = "",
    target_url: str = "",
    phase: str = "search",
    wait_seconds: int = 300,
) -> str:
    """把一个平台验证请求路由到对应的平台适配器。

    ``phase=search`` 只接受最近一次列表搜索产生的授权；``phase=detail``
    只接受详情批次产生且与首个被拦原始 URL 完全匹配的授权。两种授权都短时、
    单次消费；工具只打开平台官方验证页，不破解或绕过验证。
    """
    if rental_mode() != "live":
        return json.dumps({
            "status": "unsupported",
            "message": "人工验证工具只在 --live 真实联网模式启用；离线模式不会打开浏览器。",
        }, ensure_ascii=False)
    normalized = _normalize_platform(platform)
    if normalized not in PLATFORM_NAMES:
        return json.dumps({
            "status": "unsupported",
            "platform": platform,
            "message": "不支持的平台。可选值：58同城、安居客、房天下。",
        }, ensure_ascii=False)
    normalized_phase = _normalize_phase(phase)
    if normalized_phase not in {"search", "detail"}:
        return json.dumps({
            "status": "unsupported",
            "platform": PLATFORM_NAMES[normalized],
            "message": "验证阶段必须是 search 或 detail。",
        }, ensure_ascii=False)
    if not _allowed_platform_url(normalized, target_url):
        return json.dumps({
            "status": "rejected",
            "platform": PLATFORM_NAMES[normalized],
            "message": "target_url 必须是对应平台域名下的 HTTPS 页面。",
        }, ensure_ascii=False)

    attempt_key = f"{normalized}:{normalized_phase}"
    now = time.monotonic()
    last_attempt = _LAST_ATTEMPT_AT.get(attempt_key)
    if last_attempt is not None and now - last_attempt < VERIFICATION_COOLDOWN_SECONDS:
        remaining = int(VERIFICATION_COOLDOWN_SECONDS - (now - last_attempt))
        return json.dumps({
            "status": "cooldown",
            "platform": PLATFORM_NAMES[normalized],
            "phase": normalized_phase,
            "message": f"本进程刚尝试过一次该阶段验证，暂不重复弹窗（建议等待约 {remaining} 秒）。",
        }, ensure_ascii=False)

    try:
        from .candidate_search import (
            consume_platform_verification_request, platform_verification_target, _area_code, _city_code,
        )

        normalized_city = _city_code(city)
        normalized_area = _area_code(area, normalized_city)
        if normalized_phase == "search" and not normalized_city:
            return json.dumps({
                "status": "rejected",
                "platform": PLATFORM_NAMES[normalized],
                "message": "缺少城市，不能为未知城市打开平台验证页面。",
            }, ensure_ascii=False)
        if normalized_phase == "search":
            recorded_target = platform_verification_target(normalized)
            if recorded_target:
                if target_url and target_url != recorded_target:
                    return json.dumps({"status": "rejected", "platform": PLATFORM_NAMES[normalized],
                                       "message": "验证入口与最近被拦的搜索页面不符；本次未消费授权。"}, ensure_ascii=False)
                target_url = recorded_target
            else:
                target_url = _platform_url(normalized, normalized_city, normalized_area, keyword)
            if not _allowed_platform_url(normalized, target_url):
                return json.dumps({"status": "rejected", "message": "被拦页面不属于对应平台，不打开验证。"}, ensure_ascii=False)
            authorized = consume_platform_verification_request(
                normalized, city=normalized_city, area=normalized_area, keyword=keyword
            )
            missing_message = (
                f"未发现本轮最近一次搜索明确报告 {PLATFORM_NAMES[normalized]} 列表页被拦截；"
                "本次不弹出验证窗口。"
            )
        else:
            from .batch_detail_tool import (
                consume_detail_verification_request,
                detail_verification_context,
            )

            authorized = bool(target_url) and consume_detail_verification_request(normalized, target_url)
            detail_context = detail_verification_context(normalized, target_url) if authorized else None
            missing_message = (
                f"未发现与该 URL 匹配的 {PLATFORM_NAMES[normalized]} 详情页验证授权；"
                "详情验证只能由刚发生的拦截触发且只能消费一次。"
            )
        if not authorized:
            return json.dumps({
                "status": "not_needed",
                "platform": PLATFORM_NAMES[normalized],
                "phase": normalized_phase,
                "message": missing_message,
            }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "rejected",
            "platform": PLATFORM_NAMES[normalized],
            "phase": normalized_phase,
            "message": f"无法确认本轮是否需要 {PLATFORM_NAMES[normalized]} 验证，已拒绝弹窗: {exc}",
        }, ensure_ascii=False)

    session_file = _session_path(normalized)
    _LAST_ATTEMPT_AT[attempt_key] = now
    try:
        verifier = {"58": _verify_58_session, "anjuke": _verify_anjuke_session, "fang": _verify_fang_session}[normalized]
        result = verifier(
            normalized_city,
            normalized_area,
            keyword,
            session_file,
            max(10, min(int(wait_seconds), 900)),
            target_url,
        )
        result["phase"] = normalized_phase
        if normalized_phase == "detail" and result.get("status") == "ok" and detail_context:
            result["retry_urls"] = detail_context["retry_urls"]
            result["continuation_urls"] = detail_context["continuation_urls"]
            result["continuation_count"] = len(detail_context["continuation_urls"])
        result["next_step"] = (
            "请先把 retry_urls 原样交给详情工具重试一次；若这次成功，再把 continuation_urls "
            "交给详情工具继续读取一次。继续读取再次触发验证时立即停止，不要循环弹窗。"
            if result.get("status") == "ok" and normalized_phase == "detail"
            else f"请用相同城市、区域和关键词重试刚才被拦截的 {PLATFORM_NAMES[normalized]} 搜索一次。"
            if result.get("status") == "ok"
            else "验证失败不要连续重试。"
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return json.dumps({
            "status": "error",
            "platform": PLATFORM_NAMES[normalized],
            "phase": normalized_phase,
            "message": "启动人工验证失败，请检查浏览器和网络。",
        }, ensure_ascii=False)

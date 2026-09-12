"""本地租房 Agent 的人工参与浏览器验证工具。

工具支持 58 同城、安居客和房天下的公开列表/搜索流程。
它会打开可见的 Playwright 浏览器，等待用户完成平台自己的验证页面，
并保存按平台隔离的会话状态；不会破解或绕过验证码。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup
from langchain_core.tools import tool

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
    city = (city or "cz").strip()
    area = (area or "").strip().strip("/")
    keyword = (keyword or "").strip()
    # 平台路径片段只允许已知的 ASCII slug。像“示例科教城”这样的
    # 自由地点名称不能直接拼成 /fangyuan/<name>/。
    if area and not area.isascii():
        keyword = " ".join(part for part in (area, keyword) if part)
        area = ""
    encoded = f"?keyword={quote(keyword)}" if keyword else ""
    if platform == "58":
        return f"https://{city}.zf.58.com/{area}/{encoded}" if area else f"https://{city}.zf.58.com/{encoded}"
    if platform == "anjuke":
        return f"https://{city}.zu.anjuke.com/fangyuan/{area}/{encoded}" if area else f"https://{city}.zu.anjuke.com/fangyuan/{encoded}"
    return f"https://{city}.zu.fang.com/house/{area}/a21/{encoded}" if area else f"https://{city}.zu.fang.com/house/{encoded}"


def _allowed_platform_url(platform: str, value: str) -> bool:
    if not value:
        return True
    host = (urlparse(value).hostname or "").lower().rstrip(".")
    suffix = {"58": "58.com", "anjuke": "anjuke.com", "fang": "fang.com"}[platform]
    return urlparse(value).scheme == "https" and (host == suffix or host.endswith("." + suffix))


def _challenge_present(platform: str, html: str, current_url: str) -> bool:
    text = " ".join(BeautifulSoup(html, "lxml").get_text(" ", strip=True).split())
    sample = f"{current_url} {text[:5000]}".lower()
    markers = ("antibot", "verifycode", "请输入验证码", "人机验证", "访问过于频繁", "安全验证", "captcha")
    if any(marker in sample for marker in markers):
        return True
    minimum = {"58": 80, "anjuke": 300, "fang": 500}[platform]
    return len(text) < minimum


def _create_generic_session(platform: str, city: str, area: str, keyword: str,
                            session_file: Path, wait_seconds: int, target_url: str = "") -> dict:
    """为安居客/房天下打开可见浏览器，并保存验证后的会话状态。"""
    url = target_url or _platform_url(platform, city, area, keyword)
    if not _allowed_platform_url(platform, url):
        return {"status": "rejected", "platform": PLATFORM_NAMES[platform],
                "message": "target_url 不属于该平台允许的 HTTPS 域名。"}
    try:
        from playwright.sync_api import sync_playwright
        sys.path.insert(0, str(SKILL_ROOT))
        from scrape_58 import _launch_browser

        target = session_file.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless=False)
            context_kwargs = {"locale": "zh-CN", "viewport": {"width": 1440, "height": 900}}
            if target.exists():
                context_kwargs["storage_state"] = str(target)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as exc:
                # 某些验证页面在首次响应后仍会继续跳转。
                # 保持可见浏览器打开，让用户完成官方验证，
                # 不要因为 DOM 尚未稳定就提前失败。
                response = None
                print(f"[{PLATFORM_NAMES[platform]}] 页面仍在跳转，继续等待验证: {exc}", file=sys.stderr)
            print(f"[{PLATFORM_NAMES[platform]}] 将打开可见浏览器。请完成官方验证，程序会自动检测。", file=sys.stderr)
            deadline = time.monotonic() + max(10, min(int(wait_seconds), 900))
            while time.monotonic() < deadline:
                page.wait_for_timeout(2_000)
                html = ""
                for _ in range(3):
                    try:
                        html = page.content()
                        break
                    except Exception:
                        page.wait_for_timeout(500)
                if _allowed_platform_url(platform, page.url) and not _challenge_present(platform, html, page.url):
                    verified_url = page.url
                    context.storage_state(path=str(target))
                    page.close()
                    browser.close()
                    return {"status": "ok", "platform": PLATFORM_NAMES[platform],
                            "message": "人工验证通过，会话已保存。", "session_saved": True,
                            "url": verified_url}
            page.close()
            browser.close()
            return {"status": "error", "platform": PLATFORM_NAMES[platform],
                    "message": "等待人工验证超时，未保存会话。", "session_saved": False}
    except Exception as exc:
        return {"status": "error", "platform": PLATFORM_NAMES[platform],
                "message": f"启动人工验证失败: {exc}", "session_saved": False}


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


def _allowed_58_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "58.com" or host.endswith(".58.com"))


@tool
def human_verify_rental_platform(
    platform: str,
    city: str = "",
    area: str = "",
    keyword: str = "",
    target_url: str = "",
    wait_seconds: int = 300,
) -> str:
    """把一个平台验证请求路由到对应的平台适配器。

    只有最近一次候选搜索明确报告该平台列表/搜索页被拦截时才调用。
    成功搜索或仅详情页被拦截时不要调用。请求是短时且只消费一次；
    返回 ``ok`` 后，应将同一候选搜索重试一次。
    """
    if os.getenv("RENTAL_DEMO_MODE", "offline").strip().lower() != "live":
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
    try:
        from .candidate_search import consume_platform_verification_request, _area_code, _city_code

        normalized_city = _city_code(city)
        if not normalized_city:
            return json.dumps({
                "status": "rejected",
                "platform": PLATFORM_NAMES[normalized],
                "message": "缺少城市，不能为未知城市打开平台验证页面。",
            }, ensure_ascii=False)
        normalized_area = _area_code(area)

        if not consume_platform_verification_request(normalized, city=normalized_city, area=normalized_area, keyword=keyword):
            return json.dumps({
                "status": "not_needed",
                "platform": PLATFORM_NAMES[normalized],
                "message": f"未发现本轮最近一次搜索明确报告 {PLATFORM_NAMES[normalized]} 列表页被拦截；候选搜索成功或仅详情页被拦时不弹出验证窗口。",
            }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "rejected",
            "platform": PLATFORM_NAMES[normalized],
            "message": f"无法确认本轮搜索是否需要 {PLATFORM_NAMES[normalized]} 验证，已拒绝弹窗: {exc}",
        }, ensure_ascii=False)
    if not _allowed_platform_url(normalized, target_url):
        return json.dumps({
            "status": "rejected",
            "platform": PLATFORM_NAMES[normalized],
            "message": "target_url 必须是对应平台域名下的 HTTPS 页面。",
        }, ensure_ascii=False)

    session_file = _session_path(normalized)
    if normalized != "58":
        now = time.monotonic()
        last_attempt = _LAST_ATTEMPT_AT.get(normalized)
        if last_attempt is not None and now - last_attempt < VERIFICATION_COOLDOWN_SECONDS:
            remaining = int(VERIFICATION_COOLDOWN_SECONDS - (now - last_attempt))
            return json.dumps({
                "status": "cooldown",
                "platform": PLATFORM_NAMES[normalized],
                "message": f"本进程刚尝试过一次 {PLATFORM_NAMES[normalized]} 验证，暂不重复弹窗（建议等待约 {remaining} 秒）。",
            }, ensure_ascii=False)
        _LAST_ATTEMPT_AT[normalized] = now
        result = _create_generic_session(
            normalized, normalized_city, normalized_area, keyword, session_file,
            max(10, min(int(wait_seconds), 900)), target_url,
        )
        result["next_step"] = (
            f"验证成功后只重试刚才被拦截的 {PLATFORM_NAMES[normalized]} 搜索一次。"
            if result.get("status") == "ok" else "验证失败不要连续重试。"
        )
        return json.dumps(result, ensure_ascii=False)
    # 列表搜索成功后详情页被拦，通常是独立的频率限制，
    # 并不能证明用户会话失效。不要自动重新打开浏览器，
    # 也不要要求用户重复验证同一会话。
    if target_url and session_file.exists() and os.getenv("RENTAL_ALLOW_DETAIL_REVERIFY", "0") != "1":
        return json.dumps({
            "status": "not_needed",
            "platform": "58同城",
            "message": "已有 58 会话；详情页被单独限流，本次不重复弹出验证窗口。请减少详情数量或稍后再试。",
            "session_saved": True,
        }, ensure_ascii=False)

    now = time.monotonic()
    last_attempt = _LAST_ATTEMPT_AT.get("58")
    if last_attempt is not None and now - last_attempt < VERIFICATION_COOLDOWN_SECONDS:
        remaining = int(VERIFICATION_COOLDOWN_SECONDS - (now - last_attempt))
        return json.dumps({
            "status": "cooldown",
            "platform": "58同城",
            "message": f"本进程刚完成过一次 58 验证，暂不重复弹窗（建议等待约 {remaining} 秒）。",
        }, ensure_ascii=False)

    _LAST_ATTEMPT_AT["58"] = now
    try:
        result = _verify_58_session(
            normalized_city,
            normalized_area,
            keyword,
            session_file,
            max(10, min(int(wait_seconds), 900)),
            target_url,
        )
        result["next_step"] = "验证成功后只重试刚才被拦截的 58 搜索一次；详情页被拦不要用此工具连续重试。" if result.get("status") != "ok" else "请用相同城市、区域和关键词重试刚才被拦截的 58 搜索一次。"
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "platform": "58同城",
            "message": f"启动人工验证失败: {exc}",
        }, ensure_ascii=False)

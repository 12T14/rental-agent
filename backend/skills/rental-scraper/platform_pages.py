"""Shared public-page reading and verification detection for rental adapters."""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup


CITY_CODES = frozenset((
    "bj", "sh", "gz", "sz", "cz", "nj", "hz", "cd", "wh", "su", "xa", "tj",
    "cq", "cs", "zz", "dg", "qd", "hf", "fs", "nb", "km", "sy", "dl", "fz",
    "xm", "jn", "wx", "nc",
))
PLATFORM_SUFFIXES = {"58": "58.com", "anjuke": "anjuke.com", "fang": "fang.com"}
VERIFICATION_TEXT = (
    "请输入验证码", "人机验证", "访问过于频繁", "安全验证", "访问验证", "登录后查看",
    "请完成以下验证后继续", "请完成下列验证后继续", "拖动滑块验证",
)
EMPTY_LIST_TEXT = (
    "没有找到符合条件的房源", "没有找到相关房源", "暂无符合条件的房源",
    "暂无房源", "未找到相关房源", "没有搜索到符合条件的房源",
)


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(("script", "style", "noscript")):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def allowed_platform_url(platform: str, value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    suffix = PLATFORM_SUFFIXES[platform]
    return parsed.scheme == "https" and not parsed.username and not parsed.password and (
        host == suffix or host.endswith("." + suffix)
    )


def platform_city(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in PLATFORM_SUFFIXES.values()):
        return ""
    prefix = host.split(".", 1)[0]
    if prefix in CITY_CODES:
        return prefix
    if host == "zu.fang.com":
        code = parsed.path.strip("/").split("/", 1)[0]
        return code if code in CITY_CODES else ""
    return ""


def is_verification_page(html: str, current_url: str = "") -> bool:
    url = unquote(current_url).lower()
    if any(marker in url for marker in ("antibot", "verifycode", "captcha")):
        return True
    text = page_text(html)[:5000]
    if any(marker in text for marker in VERIFICATION_TEXT):
        return True
    # A 200 response can contain a JS/meta redirect instead of an HTTP redirect.
    sample = unquote(html).lower().replace("\\/", "/")
    return any(marker in sample for marker in (
        "callback.58.com/antibot", "/antibot/verifycode", "location.href=\"captcha",
        "checkyzm.min.js", "slidertools",
    ))


def is_empty_list_page(html: str) -> bool:
    text = page_text(html)
    return any(marker in text for marker in EMPTY_LIST_TEXT)


def classify_list_result(html: str, card_count: int, parsed_count: int) -> str:
    if card_count == 0:
        return "empty" if is_empty_list_page(html) else "parse_error"
    if parsed_count == 0:
        return "parse_error"
    return "partial" if parsed_count < card_count else "ok"


def verification_ready(platform: str, html: str, current_url: str, *, phase: str = "search",
                       expected_city: str = "", target_url: str = "") -> bool:
    if not allowed_platform_url(platform, current_url) or is_verification_page(html, current_url):
        return False
    if expected_city and platform_city(current_url) != expected_city:
        return False
    if phase == "detail":
        if target_url and urlparse(current_url).path.rstrip("/") != urlparse(target_url).path.rstrip("/"):
            return False
        text = page_text(html)
        return len(text) >= 80 and any(marker in text for marker in ("元/月", "元/ 月", "租金", "房源详情"))
    selectors = {"58": "a.house-link", "anjuke": ".zu-itemmod", "fang": "dl.list"}
    return bool(BeautifulSoup(html, "lxml").select(selectors[platform])) or is_empty_list_page(html)


def create_platform_session(platform: str, url: str, session_file: str, wait_seconds: int = 300) -> dict:
    """Wait for the user to complete the official challenge; save only that platform's state."""
    result = {"status": "error", "session_saved": False}
    if not allowed_platform_url(platform, url):
        return result | {"status": "rejected", "message": "验证入口必须属于对应平台的 HTTPS 域名。"}
    path = urlparse(url).path.rstrip("/")
    phase = "detail" if re.search(r"/(?:chuzu|zufang|hezu)/[^/]+\.(?:htm|html|shtml)$", path) or (
        platform == "anjuke" and re.search(r"/fangyuan/(?:\d+|[^/]+\.(?:html|shtml))$", path)
    ) else "search"
    target = Path(session_file).resolve()
    try:
        from playwright.sync_api import sync_playwright
        from scrape_58 import _launch_browser

        target.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless=False)
            try:
                kwargs = {"locale": "zh-CN", "viewport": {"width": 1440, "height": 900}}
                if target.is_file():
                    kwargs["storage_state"] = str(target)
                context = browser.new_context(**kwargs)
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                except Exception:
                    # Challenge pages can keep navigating after their first response.
                    print("页面仍在跳转，请在可见浏览器中完成官方验证。", file=sys.stderr)
                deadline = time.monotonic() + max(10, min(int(wait_seconds), 900))
                while time.monotonic() < deadline:
                    page.wait_for_timeout(2_000)
                    try:
                        html = page.content()
                    except Exception:
                        continue
                    if verification_ready(platform, html, page.url, phase=phase,
                                          expected_city=platform_city(url), target_url=url):
                        context.storage_state(path=str(target))
                        return {"status": "ok", "session_saved": True, "url": url,
                                "message": "人工验证通过，会话已保存；只重试刚才被拦的请求一次。"}
                return result | {"message": "等待人工验证超时，未保存会话。"}
            finally:
                browser.close()
    except Exception:
        return result | {"message": "启动或等待人工验证失败，未保存会话；请检查浏览器和网络。"}


def _navigate_public_page(page, url: str) -> tuple[str, str, int]:
    """At most one timeout recovery; never retry a challenge or other errors."""
    from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError

    for attempt in range(2):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3_000)
            return page.content(), page.url, response.status if response is not None else 200
        except Error as exc:
            if not isinstance(exc, PlaywrightTimeoutError) and "ERR_TIMED_OUT" not in str(exc):
                raise
            try:
                html, final_url = page.content(), page.url
            except Error:
                html, final_url = "", ""
            if is_verification_page(html, final_url):
                return html, final_url, 200
            platform = next((key for key in PLATFORM_SUFFIXES if allowed_platform_url(key, url)), "")
            # domcontentloaded may time out while usable cards are already present.
            if platform and urlparse(final_url).path.rstrip("/") == urlparse(url).path.rstrip("/") and verification_ready(
                platform, html, final_url, expected_city=platform_city(url)
            ):
                return html, final_url, 200
            if attempt:
                raise


def read_public_page(url: str, session_file: str | None = None, *, delay_seconds: float = 8.0
                     ) -> tuple[str, str, int]:
    """Read with the verified profile and bounded timeout recovery."""
    from playwright.sync_api import sync_playwright
    from scrape_58 import _launch_browser

    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless=True)
            try:
                kwargs = {"locale": "zh-CN", "viewport": {"width": 1440, "height": 900}}
                if session_file:
                    if not Path(session_file).is_file():
                        raise ValueError("Specified browser session file does not exist")
                    kwargs["storage_state"] = session_file
                context = browser.new_context(**kwargs)
                page = context.new_page()
                return _navigate_public_page(page, url)
            finally:
                browser.close()
    finally:
        if delay_seconds > 0:
            time.sleep(delay_seconds)


def save_page_snapshot(html: str, platform: str, city: str, area: str,
                       output_dir: Path | None = None) -> None:
    directory = output_dir or Path(__file__).resolve().parents[2] / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    label = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{platform}_{city}_{area or 'all'}")
    (directory / f"{label}_{stamp}.html").write_text(html, encoding="utf-8")

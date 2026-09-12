"""租房 Agent 使用的批量详情页访问工具。

工具只返回结构化事实和简短证据片段，同时把完整批次结果写入产物文件。
工具不会重试、并发访问、轮换身份，也不会尝试绕过访问控制。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from langchain_core.tools import tool

AGENT_CORE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_CORE_ROOT.parent
SKILL_ROOT = PROJECT_ROOT / "skills" / "rental-scraper"
DEFAULT_58_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "58" / "storage_state.json"
DEFAULT_ANJUKE_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "anjuke" / "storage_state.json"
DEFAULT_FANG_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "fang" / "storage_state.json"
FIXTURE_PATH = AGENT_CORE_ROOT / "fixtures" / "details.json"
ARTIFACT_DIR = PROJECT_ROOT / "artifacts"

REQUEST_TIMEOUT = 20
MAX_URLS = 20
DEFAULT_DELAY_SECONDS = 3.0
ALLOWED_SUFFIXES = ("58.com", "anjuke.com", "fang.com")
BLOCK_MARKERS = (
    "请输入验证码", "人机验证", "访问过于频繁", "安全验证", "verifycode",
    "captcha", "登录后查看",
)


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "", ""))


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_SUFFIXES)


def _is_probable_list_page(url: str) -> bool:
    """拒绝被误当成详情 URL 的已知平台列表/搜索页地址。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if host.endswith(".zf.58.com"):
        return True
    # 房天下列表使用 /house/<area>；单条记录可能是没有扩展名的 slug
    # 或数字片段，因此不能把所有无扩展名的 /house/ URL 都判为列表页。
    if host.endswith("zu.fang.com") and (
        path in {"", "/house"}
        or path.startswith("/house/") and path.count("/") <= 2
    ):
        return True
    if host.endswith("zu.anjuke.com") and path in {"", "/fangyuan"}:
        return True
    return False


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(("script", "style", "noscript")):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _is_blocked(html: str, final_url: str = "") -> bool:
    sample = f"{final_url} {_page_text(html)[:4000]}".lower()
    return any(marker.lower() in sample for marker in BLOCK_MARKERS) or len(sample.strip()) < 80


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def extract_detail_facts(html: str, url: str) -> dict[str, Any]:
    """只提取页面明确写出的事实；未出现的字段保持未知。"""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    text = _page_text(html)
    if _is_blocked(html, url):
        return {
            "status": "blocked",
            "url": url,
            "title": title,
            "facts": {},
            "evidence": [],
            "message": "页面疑似验证码、登录或访问拦截，未尝试绕过。",
        }

    facts: dict[str, Any] = {
        "monthly_rent_cny": _first_match(text, (
            r"(?:租金|月租|价格)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*元\s*/?\s*月",
            r"(\d+(?:\.\d+)?)\s*元\s*/?\s*月",
        )),
        "payment_rule": _first_match(text, (r"(押\s*一\s*付\s*[一二三六])", r"(押金[^，。；;]{0,30})")),
        "agency_fee": _first_match(text, (r"((?:需收|另收|收取)?\s*(?:中介费|服务费)[^，。；;]{0,30})",)),
        "utility_rule": _first_match(text, (r"((?:水电|水费|电费)[^，。；;]{0,40})",)),
        "minimum_lease": _first_match(text, (r"(最短租期[^，。；;]{0,30})", r"(租期[^，。；;]{0,30})")),
        "available_date": _first_match(text, (r"(可入住时间[^，。；;]{0,30})", r"(随时入住)",)),
    }
    if facts["monthly_rent_cny"] is not None:
        facts["monthly_rent_cny"] = float(facts["monthly_rent_cny"])

    evidence: list[str] = []
    for marker in ("押", "中介", "服务费", "水电", "租期", "入住"):
        index = text.find(marker)
        if index >= 0:
            evidence.append(text[max(0, index - 35): index + 125])
        if len(evidence) >= 3:
            break

    return {
        "status": "ok",
        "url": url,
        "title": title,
        "facts": facts,
        "evidence": evidence,
        "message": "详情页已访问并提取明确写出的租赁条件；未写明的字段保持为空。",
    }


def _load_offline_pages() -> dict[str, str]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return {canonical_url(url): item["html"] for url, item in data.items()}


def _artifact_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACT_DIR / f"detail_batch_{stamp}.json"


def _open_browser_session(session_file: Path):
    """使用此前验证过的会话状态打开一个无头 Playwright 上下文。"""
    sys.path.insert(0, str(SKILL_ROOT))
    from playwright.sync_api import sync_playwright
    from scrape_58 import _launch_browser

    playwright = sync_playwright().start()
    browser = _launch_browser(playwright, headless=True)
    context = browser.new_context(
        storage_state=str(session_file),
        locale="zh-CN",
        viewport={"width": 1440, "height": 900},
    )
    return playwright, browser, context


def _platform_for_host(host: str) -> str | None:
    host = host.lower().rstrip(".")
    if host == "58.com" or host.endswith(".58.com"):
        return "58"
    if host == "anjuke.com" or host.endswith(".anjuke.com"):
        return "anjuke"
    if host == "fang.com" or host.endswith(".fang.com"):
        return "fang"
    return None


def fetch_detail_batch(
    urls: list[str],
    *,
    live: bool = False,
    max_urls: int = MAX_URLS,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> dict[str, Any]:
    """串行读取有界 URL 批次，并返回精简的结构化事实。"""
    seen: set[str] = set()
    normalized: list[str] = []
    invalid: list[dict[str, Any]] = []
    for raw_url in urls:
        url = canonical_url(str(raw_url))
        if not url or url in seen:
            continue
        seen.add(url)
        if not url.startswith(("http://", "https://")) or not _host_allowed(url):
            invalid.append({"url": url, "status": "rejected", "message": "URL 不属于允许的公开房源平台域名。"})
            continue
        if _is_probable_list_page(url):
            invalid.append({
                "url": url,
                "status": "source_list",
                "message": "这是平台列表页，不是单条房源详情页；已跳过详情访问。",
            })
            continue
        normalized.append(url)

    limit = max(1, min(int(max_urls), MAX_URLS))
    omitted_count = max(0, len(normalized) - limit)
    normalized = normalized[:limit]
    pages = {} if live else _load_offline_pages()
    last_request: dict[str, float] = defaultdict(lambda: 0.0)
    results: list[dict[str, Any]] = list(invalid)
    session_files = {
        "58": Path(os.getenv("RENTAL_58_SESSION_FILE", str(DEFAULT_58_SESSION))).expanduser(),
        "anjuke": Path(os.getenv("RENTAL_ANJUKE_SESSION_FILE", str(DEFAULT_ANJUKE_SESSION))).expanduser(),
        "fang": Path(os.getenv("RENTAL_FANG_SESSION_FILE", str(DEFAULT_FANG_SESSION))).expanduser(),
    }
    browser_parts: dict[str, tuple[Any, Any, Any]] = {}
    browser_session_used = False
    if live:
        for platform, session_path in session_files.items():
            if not session_path.exists() or not any(_platform_for_host(urlparse(url).hostname or "") == platform for url in normalized):
                continue
            try:
                browser_parts[platform] = _open_browser_session(session_path)
                browser_session_used = True
                print(f"[详情] {platform} 使用已验证浏览器会话串行访问。", file=sys.stderr)
            except Exception as exc:
                print(f"[详情] 无法加载 {platform} 会话，回退公开 HTTP 请求: {exc}", file=sys.stderr)

    for url in normalized:
        host = urlparse(url).hostname or ""
        try:
            if live:
                elapsed = time.monotonic() - last_request[host]
                if elapsed < max(0.0, delay_seconds):
                    time.sleep(max(0.0, delay_seconds) - elapsed)
                platform = _platform_for_host(host)
                browser_context = browser_parts.get(platform, (None, None, None))[2] if platform else None
                if browser_context is not None:
                    page = browser_context.new_page()
                    response = page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    page.wait_for_timeout(2_000)
                    status_code = response.status if response is not None else 200
                    html = page.content()
                    final_url = page.url
                    page.close()
                    last_request[host] = time.monotonic()
                    if status_code in (401, 403, 429):
                        results.append({"url": url, "status": "blocked", "message": f"浏览器页面 HTTP {status_code}，未重试。"})
                        continue
                    if not _host_allowed(final_url):
                        results.append({"url": url, "status": "rejected_redirect", "message": "页面重定向到了不在允许列表中的域名，未继续解析。"})
                        continue
                    results.append(extract_detail_facts(html, final_url))
                    continue
                response = requests.get(
                    url,
                    headers={"User-Agent": "RentalResearchAgent/0.1 (public-detail-reader)"},
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
                last_request[host] = time.monotonic()
                response.encoding = response.apparent_encoding or "utf-8"
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location", "")
                    results.append({
                        "url": url,
                        "status": "redirect_not_followed",
                        "message": "详情页返回重定向；为避免访问未校验域名，当前工具不自动跟随。",
                        "location": location[:300],
                    })
                    continue
                if response.status_code in (401, 403, 429):
                    results.append({"url": url, "status": "blocked", "message": f"HTTP {response.status_code}，未重试。"})
                    continue
                response.raise_for_status()
                html = response.text
                final_url = response.url
                if not _host_allowed(final_url):
                    results.append({
                        "url": url,
                        "status": "rejected_redirect",
                        "message": "页面重定向到了不在允许列表中的域名，未继续解析。",
                    })
                    continue
            else:
                html = pages.get(url, "")
                final_url = url
                if not html:
                    results.append({"url": url, "status": "fixture_missing", "message": "离线夹具没有该 URL，未访问网络。"})
                    continue
            results.append(extract_detail_facts(html, final_url))
        except requests.RequestException as exc:
            results.append({"url": url, "status": "error", "message": f"请求失败: {exc}"})
        except Exception as exc:  # 单个异常页面不能中止整个批次。
            results.append({"url": url, "status": "error", "message": f"解析失败: {exc}"})

    for parts in browser_parts.values():
        try:
            parts[1].close()
            parts[0].stop()
        except Exception:
            pass

    counts = {
        "ok": sum(item.get("status") == "ok" for item in results),
        "blocked": sum(item.get("status") == "blocked" for item in results),
        "error": sum(item.get("status") in {
            "error", "fixture_missing", "rejected", "source_list", "rejected_redirect", "redirect_not_followed",
        } for item in results),
    }
    status = "complete"
    if counts["blocked"] or counts["error"] or omitted_count:
        status = "partial"
    if results and counts["ok"] == 0 and (counts["blocked"] or counts["error"]):
        status = "blocked" if counts["blocked"] and not counts["error"] else "failed"

    payload = {
        "schema_version": "1.0",
        "status": status,
        "mode": "live" if live else "offline_fixture",
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "requested_count": len(urls),
        "unique_allowed_count": len(normalized),
        "processed_count": len(normalized),
        "omitted_count": omitted_count,
        "counts": counts,
        "results": results,
        "warnings": [
            "详情页结果只代表访问时页面内容，不代表当前仍然可租。",
            "未明确写出的租赁条件保持为空，不做推断。",
        ],
        "browser_session_used": browser_session_used,
        "browser_session_platforms": list(browser_parts),
    }
    artifact = _artifact_path()
    artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        **{key: payload[key] for key in (
            "schema_version", "status", "mode", "retrieved_at", "requested_count",
            "unique_allowed_count", "processed_count", "omitted_count", "counts", "warnings",
            "browser_session_used", "browser_session_platforms",
        )},
        "results": results,
        "artifact_saved": True,
        "context_note": "完整批次结果已保存到本地审计产物；本返回不包含本机路径，仅包含结构化事实和短证据。",
    }


@tool
def batch_fetch_listing_details(
    urls: list[str],
    max_urls: int = 10,
) -> str:
    """一次性读取允许的房源详情 URL，并返回精简事实。

    一次调用应传入完整 URL 列表。工具会去重并串行访问，遇到访问控制就停止，
    同时把完整结果保存到磁盘。工具不会重试或绕过验证。只有控制台设置
    ``RENTAL_DEMO_MODE=live`` 时才启用联网访问。应传入候选搜索工具返回的详情 URL；
    平台列表页地址不属于详情结果。
    """
    live = __import__("os").environ.get("RENTAL_DEMO_MODE", "offline") == "live"
    result = fetch_detail_batch(urls, live=live, max_urls=max_urls)
    return json.dumps(result, ensure_ascii=False)

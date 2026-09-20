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
from threading import RLock
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
DETAIL_VERIFICATION_TTL_SECONDS = 300
ALLOWED_SUFFIXES = ("58.com", "anjuke.com", "fang.com")
PLATFORM_NAMES = {"58": "58同城", "anjuke": "安居客", "fang": "房天下"}
BLOCK_MARKERS = (
    "请输入验证码", "人机验证", "访问过于频繁", "安全验证", "verifycode",
    "captcha", "登录后查看",
)
_LOCATION_UI_MARKERS = (
    "附近高薪工作", "查看地图", "已实名认证", "已传房本", "在线聊", "打电话",
    "微信扫码在线聊", "微信扫码打电话", "小程序快速在线聊", "扫一扫", "电话被冒用",
    "信息虚假", "我要报案", "租房神奇", "营业执照", "房源编码", "房源详情",
    "小区详情", "签约前切勿", "周房东", "经纪人", "房东(个人)", "房东（个人）",
)
_LOCATION_UI_BOUNDARY = re.compile(
    r"\s+(?:" + "|".join(re.escape(marker) for marker in _LOCATION_UI_MARKERS) + r")",
    re.IGNORECASE,
)
_LOCATION_SECTION_BOUNDARY = re.compile(
    r"\s+(?:所属区域|距离地铁|详细地址|房源编码|房源详情|小区详情)\s*[:：]?",
    re.IGNORECASE,
)
_CITY_NAMES_BY_CODE = {
    "bj": "北京", "sh": "上海", "gz": "广州", "sz": "深圳", "nj": "南京",
    "hz": "杭州", "cd": "成都", "wh": "武汉", "su": "苏州", "xa": "西安",
    "tj": "天津", "cq": "重庆", "cs": "长沙", "zz": "郑州", "dg": "东莞",
    "qd": "青岛", "hf": "合肥", "nb": "宁波", "km": "昆明", "sy": "沈阳",
    "dl": "大连", "fz": "福州", "xm": "厦门", "jn": "济南", "wx": "无锡",
    "nc": "南昌", "cz": "常州",
}

# 详情页验证码只能授权一次可见浏览器验证。授权与本进程、本平台和
# 首个被拦的原始详情 URL 绑定，避免模型把它扩展成任意网页访问。
_DETAIL_VERIFICATION_REQUESTS: dict[str, dict[str, Any]] = {}
_DETAIL_VERIFICATION_LOCK = RLock()


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
    if host.endswith("zu.fang.com"):
        if host == "zu.fang.com":
            path = re.sub(r"^/[a-z]{2,6}(?=/(?:house|hezu|chuzu))", "", path)
        if path in {"", "/house", "/hezu"} or path.startswith("/house-"):
            return True
        if path.startswith("/house/") and (path.count("/") <= 2 or re.search(r"/a\d+$", path)):
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
    sys.path.insert(0, str(SKILL_ROOT))
    from platform_pages import is_verification_page

    sample = f"{final_url} {_page_text(html)[:4000]}".lower()
    return is_verification_page(html, final_url) or any(marker.lower() in sample for marker in BLOCK_MARKERS) or len(sample.strip()) < 80


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _clean_location_piece(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip(" ,，;；|_")
    value = _LOCATION_SECTION_BOUNDARY.split(value, maxsplit=1)[0]
    value = _LOCATION_UI_BOUNDARY.split(value, maxsplit=1)[0]
    # 58 的小区字段经常把“在租 N 套”作为同一个文本节点返回。
    value = re.sub(r"\s*[（(][^（）()]{0,40}(?:在租|套)[^（）()]{0,40}[）)]", "", value)
    value = re.sub(r"(?:[-—|])?\s*(?:\d+图|租房网|租房房源|合租房源|整租房源)\s*$", "", value, flags=re.IGNORECASE)
    return value.strip(" ,，;；|_")[:300]


def _looks_like_address(value: str) -> bool:
    value = _clean_location_piece(value)
    if not value or len(value) > 160:
        return False
    # 只接受具备道路/门牌/小区地标语义的片段；普通标题不会被当成地址。
    return bool(re.search(r"(?:路|街|巷|道|号|弄|村|小区|公寓|花园|苑|广场|大厦|城)", value))


def _community_from_title(value: str) -> str:
    value = _clean_location_piece(value)
    value = re.sub(r"\s*[一二两三四五六七八九十\d]+\s*室[^,，]*$", "", value)
    value = re.sub(r"\s*(?:整租|合租|次卧|主卧|精装修|电梯房|房源)\s*$", "", value)
    return value.strip(" ,，")[:200]


def _city_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    code = host.split(".", 1)[0]
    if host == "zu.fang.com":
        code = parsed.path.strip("/").split("/", 1)[0]
    return _CITY_NAMES_BY_CODE.get(code, "")


def _city_name_from_title(title: str | None) -> str:
    if not title:
        return ""
    normalized = title.replace(" ", "")
    for city in _CITY_NAMES_BY_CODE.values():
        if city in normalized:
            return city
    return ""


def _extract_detail_location(text: str, title: str | None, url: str = "") -> dict[str, str]:
    """提取页面明确地址，标题推断的字段会显式标为低置信度。"""

    explicit_address = _first_match(text, (
        r"(?:详细地址|房源地址|地址)\s*[:：]\s*([^。；;\n]{2,160})",
    ))
    explicit_community = _first_match(text, (
        r"(?:小区名称|小区|社区)\s*[:：]\s*([^。；;\n]{2,100})",
    ))
    explicit_region = _first_match(text, (
        r"所属区域\s*[:：]\s*([^。；;\n]{2,100})",
    ))
    address = _clean_location_piece(explicit_address or "")
    community = _clean_location_piece(explicit_community or "")
    region = _clean_location_piece(explicit_region or "")
    confidence = "page_explicit" if address or community else ""

    if title and not (address and community):
        clean_title = re.sub(
            r"\s*[-—]\s*(?:[^-—]*?)(?:58同城|安居客|房天下).*?$", "", title,
            flags=re.IGNORECASE,
        ).strip()
        parts = re.split(r"[,，]", clean_title, maxsplit=1)
        if len(parts) == 2:
            title_community = _community_from_title(parts[0])
            title_address = _clean_location_piece(parts[1].split("_", 1)[0])
            if not address and _looks_like_address(title_address):
                address = title_address
            if not community and title_community and address:
                community = title_community
            if address or community:
                confidence = "title_inferred_low"

    if not community and title:
        match = re.search(r"([^,，\s]{2,80}(?:小区|社区|花园|公寓|苑|村))", title)
        if match:
            community = _clean_location_piece(match.group(1))
            confidence = confidence or "title_inferred_low"

    location = {}
    if address:
        location["address"] = address
    if community:
        location["community"] = community
    if region:
        location["region"] = region
    city = _city_name_from_url(url) or _city_name_from_title(title)
    if city:
        location["city"] = city
    if confidence and location:
        location["confidence"] = confidence
    return location


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
            "location": {},
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

    location = _extract_detail_location(text, title, url)

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
        "location": location,
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


def _record_detail_verification_request(platform: str, target_url: str) -> dict[str, Any]:
    request = {
        "platform": platform,
        "target_url": canonical_url(target_url),
        "recorded_at": time.monotonic(),
        "consumed": False,
    }
    with _DETAIL_VERIFICATION_LOCK:
        _DETAIL_VERIFICATION_REQUESTS[platform] = request
    return dict(request)


def consume_detail_verification_request(platform: str, target_url: str) -> bool:
    """消费最近一次详情页拦截授权；URL、平台和有效期必须同时匹配。"""

    normalized_url = canonical_url(target_url)
    with _DETAIL_VERIFICATION_LOCK:
        request = _DETAIL_VERIFICATION_REQUESTS.get(platform)
        if not request:
            return False
        fresh = time.monotonic() - float(request.get("recorded_at", 0.0)) <= DETAIL_VERIFICATION_TTL_SECONDS
        matches = request.get("target_url") == normalized_url
        if request.get("consumed") or not fresh or not matches:
            return False
        request["consumed"] = True
        return True


def _is_challenge_url(url: str) -> bool:
    value = (url or "").lower()
    return any(marker in value for marker in ("antibot", "verifycode", "captcha"))


def _detail_result_for_requested_url(html: str, requested_url: str, final_url: str) -> dict[str, Any]:
    """解析最终页面，但始终以原始房源 URL 作为结果身份。"""

    result = extract_detail_facts(html, final_url)
    result["url"] = requested_url
    if result.get("status") == "blocked":
        # 验证页标题和带签名的跳转 URL 不是房源事实，也不应进入公开投影。
        result["title"] = None
        result["message"] = "详情页跳转到平台官方验证页面，未尝试绕过。"
    return result


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
    blocked_platforms: set[str] = set()
    first_blocked_url: dict[str, str] = {}
    attempted_count = 0
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
        platform = _platform_for_host(host)
        if platform in blocked_platforms:
            results.append({
                "url": url,
                "status": "skipped_after_block",
                "message": f"{PLATFORM_NAMES.get(platform, '该平台')} 已触发验证，本批次未继续访问。",
            })
            continue
        attempted_count += 1
        try:
            if live:
                elapsed = time.monotonic() - last_request[host]
                if elapsed < max(0.0, delay_seconds):
                    time.sleep(max(0.0, delay_seconds) - elapsed)
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
                        if platform:
                            blocked_platforms.add(platform)
                            first_blocked_url.setdefault(platform, url)
                        continue
                    if not _host_allowed(final_url):
                        results.append({"url": url, "status": "rejected_redirect", "message": "页面重定向到了不在允许列表中的域名，未继续解析。"})
                        continue
                    detail_result = _detail_result_for_requested_url(html, url, final_url)
                    results.append(detail_result)
                    if detail_result.get("status") == "blocked" and platform:
                        blocked_platforms.add(platform)
                        first_blocked_url.setdefault(platform, url)
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
                    if _is_challenge_url(location):
                        results.append({
                            "url": url,
                            "status": "blocked",
                            "message": "详情页重定向到平台官方验证页面，未自动跟随。",
                        })
                        if platform:
                            blocked_platforms.add(platform)
                            first_blocked_url.setdefault(platform, url)
                    else:
                        results.append({
                            "url": url,
                            "status": "redirect_not_followed",
                            "message": "详情页返回重定向；为避免访问未校验域名，当前工具不自动跟随。",
                        })
                    continue
                if response.status_code in (401, 403, 429):
                    results.append({"url": url, "status": "blocked", "message": f"HTTP {response.status_code}，未重试。"})
                    if platform:
                        blocked_platforms.add(platform)
                        first_blocked_url.setdefault(platform, url)
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
            detail_result = _detail_result_for_requested_url(html, url, final_url)
            results.append(detail_result)
            if live and detail_result.get("status") == "blocked" and platform:
                blocked_platforms.add(platform)
                first_blocked_url.setdefault(platform, url)
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
        "skipped": sum(item.get("status") == "skipped_after_block" for item in results),
        "error": sum(item.get("status") in {
            "error", "fixture_missing", "rejected", "source_list", "rejected_redirect", "redirect_not_followed",
        } for item in results),
    }
    status = "complete"
    if counts["blocked"] or counts["skipped"] or counts["error"] or omitted_count:
        status = "partial"
    if results and counts["ok"] == 0 and (counts["blocked"] or counts["error"]):
        status = "blocked" if counts["blocked"] and not counts["error"] else "failed"

    verification_requests = []
    if live:
        for platform, target_url in first_blocked_url.items():
            request = _record_detail_verification_request(platform, target_url)
            verification_requests.append({
                "platform": PLATFORM_NAMES[platform],
                "target_url": request["target_url"],
                # 验证后只重试触发验证的这一条，避免立即再次打满整批。
                "retry_urls": [request["target_url"]],
                "reason": "detail_access_blocked",
            })

    payload = {
        "schema_version": "1.0",
        "status": status,
        "mode": "live" if live else "offline_fixture",
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "requested_count": len(urls),
        "unique_allowed_count": len(normalized),
        "processed_count": attempted_count,
        "skipped_count": counts["skipped"],
        "omitted_count": omitted_count,
        "counts": counts,
        "results": results,
        "warnings": [
            "详情页结果只代表访问时页面内容，不代表当前仍然可租。",
            "未明确写出的租赁条件保持为空，不做推断。",
        ],
        "browser_session_used": browser_session_used,
        "browser_session_platforms": list(browser_parts),
        "needs_human_verification": bool(verification_requests),
        "human_verification_reason": "detail_access_blocked" if verification_requests else None,
        "detail_verification_requests": verification_requests,
    }
    artifact = _artifact_path()
    artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        **{key: payload[key] for key in (
            "schema_version", "status", "mode", "retrieved_at", "requested_count",
            "unique_allowed_count", "processed_count", "skipped_count", "omitted_count", "counts", "warnings",
            "browser_session_used", "browser_session_platforms", "needs_human_verification",
            "human_verification_reason", "detail_verification_requests",
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

    一次调用应传入筛选后的 URL 列表。工具会去重并串行访问；一个平台首次遇到访问控制后，
    本批次不再访问该平台的剩余 URL，并只为首个被拦详情生成一次短时人工验证授权。
    工具不会自行重试或绕过验证。只有控制台设置
    ``RENTAL_DEMO_MODE=live`` 时才启用联网访问。应传入候选搜索工具返回的详情 URL；
    平台列表页地址不属于详情结果。
    """
    live = __import__("os").environ.get("RENTAL_DEMO_MODE", "offline") == "live"
    result = fetch_detail_batch(urls, live=live, max_urls=max_urls)
    return json.dumps(result, ensure_ascii=False)

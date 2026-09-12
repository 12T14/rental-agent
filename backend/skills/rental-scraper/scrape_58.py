#!/usr/bin/env python3
"""使用 Playwright 采集 58 同城公开租房列表页。

采集器只读取公开 SEO 列表页，会限制请求频率、保存页面快照，
并在 58 同城要求验证时停止；不尝试破解验证码、注入隐身补丁、
轮换代理或调用私有接口。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CITY_CODE = {
    "bj": "北京", "sh": "上海", "gz": "广州", "sz": "深圳", "cz": "示例市",
    "nj": "南京", "hz": "杭州", "cd": "成都", "wh": "武汉", "su": "苏州",
    "xa": "西安", "tj": "天津", "cq": "重庆", "cs": "长沙", "zz": "郑州",
    "dg": "东莞", "qd": "青岛", "hf": "合肥", "fs": "佛山", "nb": "宁波",
    "km": "昆明", "sy": "沈阳", "dl": "大连", "fz": "福州", "xm": "厦门",
    "jn": "济南", "wx": "无锡", "nc": "南昌",
}

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]
REQUEST_TIMEOUT = 30_000
DEFAULT_DELAY = 8.0
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "artifacts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _find_chrome() -> Optional[str]:
    return next((path for path in CHROME_PATHS if Path(path).exists()), None)


def _launch_browser(playwright, *, headless: bool):
    """优先使用已安装的 Chrome/Edge，否则使用 Playwright Chromium。"""
    chrome_path = _find_chrome()
    kwargs = {"headless": headless}
    if chrome_path:
        kwargs["executable_path"] = chrome_path
    if headless:
        kwargs["args"] = ["--no-sandbox"]
    return playwright.chromium.launch(**kwargs)


def _make_url(city: str, area: str = "", keyword: str = "", page: int = 1) -> str:
    base = f"https://{city}.zf.58.com"
    if area:
        base += f"/{area.strip('/') }"
    if page > 1:
        base += f"/pg{page}"
    url = base + "/"
    return f"{url}?key={quote(keyword)}" if keyword else url


def _clean(value: str) -> str:
    return " ".join(value.split())


def _is_blocked(text: str, current_url: str = "") -> bool:
    prefix = text[:800]
    return (
        "antibot" in current_url
        or "callback.58.com/antibot" in prefix
        or "访问过于频繁" in prefix
        or "请输入验证码" in prefix
        or ("验证码" in prefix and len(text) < 2_000)
        or len(text) < 300
    )


class _58CardParser(HTMLParser):
    """解析稳定的 58 同城公开卡片标记的轻量标准库解析器。"""

    FIELDS = {
        "house-title": "title",
        "house-type": "type",
        "price-val": "price",
        "house-advantage": "tags",
    }
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, str]] = []
        self.card: Optional[dict[str, str]] = None
        self.anchor_depth = 0
        self.capture: Optional[str] = None
        self.capture_depth = 0
        self.buffer: list[str] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, Optional[str]]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = self._attrs(attrs)
        classes = set(values.get("class", "").split())
        if tag == "a" and "house-link" in classes:
            self.card = {"href": values.get("href", "")}
            self.anchor_depth = 1
            return
        if not self.card:
            return
        if tag in self.VOID_TAGS:
            return
        self.anchor_depth += 1
        if self.capture:
            self.capture_depth += 1
            return
        field = next((self.FIELDS[name] for name in classes if name in self.FIELDS), None)
        if field:
            self.capture = field
            self.capture_depth = 1
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.buffer.append(data)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        # <img /> 等空元素不会改变锚点嵌套深度。
        return

    def handle_endtag(self, tag: str) -> None:
        if not self.card:
            return
        if self.capture:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                self.card[self.capture] = _clean(unescape("".join(self.buffer)))
                self.capture = None
                self.buffer = []
        self.anchor_depth -= 1
        if tag == "a" and self.anchor_depth == 0:
            self.cards.append(self.card)
            self.card = None


def parse_58_html(html: str, source_url: str, city: str = "", region: str = "") -> list[dict]:
    """解析包含详情链接的结构化 58 同城公开卡片标记。"""
    parser = _58CardParser()
    parser.feed(html)
    listings: list[dict] = []
    for card in parser.cards:
        title = card.get("title", "")
        house_type = card.get("type", "")
        price_text = card.get("price", "")
        if not (title and house_type and price_text):
            continue

        price_match = re.search(r"\d{3,6}", price_text)
        layout_match = re.search(r"(\d+)室(\d+)厅", house_type)
        area_match = re.search(r"([\d.]+)\s*㎡", house_type)
        if not (price_match and layout_match and area_match):
            continue
        price = int(price_match.group())
        if not 100 <= price <= 50_000:
            continue

        tags = card.get("tags", "")
        tags = re.sub(r"^推荐理由[：:]?\s*", "", tags)
        href = urljoin(source_url, card.get("href", ""))
        listings.append({
            "title": title,
            "price": price,
            "monthly_rent_cny": price,
            "room": f"{layout_match.group(1)}室{layout_match.group(2)}厅",
            "area": f"{area_match.group(1)}㎡",
            "area_sqm": float(area_match.group(1)),
            "tags": tags,
            "platform": "58同城",
            "city": city,
            "region": region,
            "url": href,
            "detail_url": href,
            "source_page": source_url,
            "data_quality": "current_public_list_page_candidate",
            "detail_verification": "not_attempted",
        })

    unique: dict[str, dict] = {}
    for listing in listings:
        unique.setdefault(listing["url"] or listing["title"], listing)
    return list(unique.values())


def parse_58_snapshot(path: str | Path, source_url: str, city: str = "", region: str = "") -> list[dict]:
    return parse_58_html(Path(path).read_text(encoding="utf-8"), source_url, city, region)


def _save_snapshot(html: str, city: str, region: str, output_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"58_{city}_{region or 'all'}_{timestamp}.html"
    path.write_text(html, encoding="utf-8")
    return path


def _save_json(listings: list[dict], output: str | Path) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(listings, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_status(status: dict, path: Optional[str]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def _status(status: str, source_page: str, message: str, listings: Optional[list[dict]] = None,
            snapshot_file: Optional[str] = None, session_file: Optional[str] = None) -> dict:
    return {
        "schema_version": "1.0",
        "status": status,
        "message": message,
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source_page": source_page,
        "snapshot_saved": bool(snapshot_file),
        "session_used": bool(session_file),
        "listing_count": len(listings or []),
        "listings": listings or [],
    }


def create_58_session(
    city: str,
    area: str = "",
    keyword: str = "",
    session_file: str = "",
    wait_seconds: int = 300,
    target_url: str = "",
) -> dict:
    """打开可见浏览器，并自动检测验证是否完成。"""
    if not session_file:
        raise ValueError("--session is required for --login")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _status("error", "", "当前 Python 没有 playwright；请使用项目虚拟环境的 Python。")
    url = target_url or _make_url(city, area, keyword)
    target = Path(session_file).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    print("[58同城] 将打开可见浏览器。请在窗口中完成验证，程序会自动检测通过。", file=sys.stderr)
    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless=False)
            context_kwargs = {"locale": "zh-CN", "viewport": {"width": 1440, "height": 900}}
            if target.exists():
                context_kwargs["storage_state"] = str(target)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT)
            deadline = time.monotonic() + max(1, wait_seconds)
            verified = False
            while time.monotonic() < deadline:
                text = page.inner_text("body")
                if not _is_blocked(text, page.url):
                    verified = True
                    break
                page.wait_for_timeout(1_000)
            if not verified:
                browser.close()
                return _status("error", url, "等待人工验证超时，未保存会话。", session_file=str(target))
            context.storage_state(path=str(target))
            browser.close()
        return _status("ok", url, "人工验证通过，会话已保存；文件包含敏感登录状态，请勿提交到 Git。", session_file=str(target))
    except Exception as exc:
        return _status("error", url, f"生成会话失败: {exc}", session_file=str(target))


def scrape_58_status(
    city: str = "bj",
    area: str = "",
    keyword: str = "",
    max_listings: int = 30,
    output: Optional[str] = None,
    delay: float = DEFAULT_DELAY,
    snapshot_dir: Optional[str] = None,
    session_file: Optional[str] = None,
    status_output: Optional[str] = None,
) -> dict:
    """读取一个公开列表页，并返回明确的状态封装。"""
    url = _make_url(city, area, keyword)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result = _status("error", url, "当前 Python 没有 playwright；请使用项目虚拟环境的 Python。")
        _write_status(result, status_output)
        return result

    print(f"[58同城] 请求公开列表页: {url}", file=sys.stderr)
    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright, headless=True)
            context_kwargs = {"locale": "zh-CN", "viewport": {"width": 1440, "height": 900}}
            if session_file:
                session_path = Path(session_file).resolve()
                if not session_path.exists():
                    result = _status("error", url, "指定的会话文件不存在，请先运行 --login。", session_file=str(session_path))
                    browser.close()
                    _write_status(result, status_output)
                    return result
                context_kwargs["storage_state"] = str(session_path)
            else:
                session_path = None
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT)
            page.wait_for_timeout(3_000)
            text = page.inner_text("body")
            current_url = page.url
            html = page.content()

            if _is_blocked(text, current_url):
                print(f"[58同城] 平台要求验证或页面被拦截（{len(text)} 字符），已停止。", file=sys.stderr)
                browser.close()
                result = _status("blocked", url, "58 要求验证码或访问被拦截；未绕过验证。", session_file=str(session_path) if session_path else None)
                _write_status(result, status_output)
                return result

            target_dir = Path(snapshot_dir) if snapshot_dir else OUTPUT_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            snapshot = _save_snapshot(html, city, area, target_dir)
            listings = parse_58_html(html, url, city, area)
            browser.close()

        listings = listings[:max_listings]
        print(f"[58同城] 获取 {len(listings)} 条候选，详情链接 {sum(bool(x['url']) for x in listings)} 条", file=sys.stderr)
        print("[58同城] 页面快照已保存。", file=sys.stderr)
        result = _status("ok", url, "公开列表页采集完成。详情页仍需用户核验。", listings, str(snapshot), str(session_path) if session_path else None)
        if output:
            _write_status(result, output)
        _write_status(result, status_output)
        return result
    except Exception as exc:
        print(f"[58同城] 采集失败: {exc}", file=sys.stderr)
        result = _status("error", url, f"采集失败: {exc}", session_file=str(session_path) if "session_path" in locals() and session_path else None)
        _write_status(result, status_output)
        return result
    finally:
        if delay > 0:
            time.sleep(delay)


def scrape_58(
    city: str = "bj", area: str = "", keyword: str = "", max_listings: int = 30,
    output: Optional[str] = None, delay: float = DEFAULT_DELAY,
    snapshot_dir: Optional[str] = None, session_file: Optional[str] = None,
) -> list[dict]:
    """供 scrape_all.py 使用的兼容旧版列表返回接口。"""
    return scrape_58_status(city, area, keyword, max_listings, output, delay, snapshot_dir, session_file)["listings"]


def main() -> None:
    parser = argparse.ArgumentParser(description="58同城公开 SEO 租房列表采集器")
    parser.add_argument("--city", "-c", required=True)
    parser.add_argument("--area", "-a", default="")
    parser.add_argument("--keyword", "-k", default="")
    parser.add_argument("--max", "-m", type=int, default=30)
    parser.add_argument("--output", "-o", default="")
    parser.add_argument("--snapshot", default="", help="只解析已保存 HTML，不访问 58")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--session", default="", help="Playwright storage state 文件（由 --login 生成）")
    parser.add_argument("--login", action="store_true", help="打开可见浏览器，人工验证一次并保存 --session")
    parser.add_argument("--wait", type=int, default=300, help="--login 等待人工验证的秒数")
    parser.add_argument("--status", action="store_true", help="输出 ok/blocked/error 状态封装")
    args = parser.parse_args()

    if args.login:
        result = create_58_session(args.city, args.area, args.keyword, args.session, args.wait)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.snapshot:
        listings = parse_58_snapshot(args.snapshot, _make_url(args.city, args.area, args.keyword), args.city, args.area)
        result = _status("ok", _make_url(args.city, args.area, args.keyword), "快照解析完成。", listings[: args.max], args.snapshot)
        if args.output:
            if args.status:
                _write_status(result, args.output)
            else:
                _save_json(listings[: args.max], args.output)
        print(json.dumps(result if args.status else listings[: args.max], ensure_ascii=False, indent=2))
    else:
        result = scrape_58_status(
            args.city, args.area, args.keyword, args.max, args.output, args.delay,
            session_file=args.session or None,
            status_output=args.output if args.status else None,
        )
        print(json.dumps(result if args.status else result["listings"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

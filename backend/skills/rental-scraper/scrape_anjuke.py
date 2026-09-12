#!/usr/bin/env python3
"""在不绕过访问控制的前提下采集安居客公开租房列表卡片。

采集器只读取一个公开列表页；遇到验证码或访问控制页面就停止，
不访问私有接口、不轮换代理，也不尝试解决验证挑战。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REQUEST_TIMEOUT = 20
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "artifacts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _fetch_with_session(url: str, session_file: str) -> tuple[str, str, int]:
    """使用已保存的用户状态，在无头上下文中读取一个公开页面。"""
    from playwright.sync_api import sync_playwright
    from scrape_58 import _launch_browser

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, headless=True)
        context = browser.new_context(storage_state=session_file, locale="zh-CN")
        page = context.new_page()
        response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2_000)
        html, final_url = page.content(), page.url
        status = response.status if response is not None else 200
        browser.close()
        return html, final_url, status


def _make_url(city: str, area: str = "", keyword: str = "") -> str:
    # 自由地点名称是搜索词，不是安居客路径片段。
    if area and not area.isascii():
        keyword = " ".join(part for part in (area, keyword) if part)
        area = ""
    path = f"/fangyuan/{area.strip('/')}/" if area else "/fangyuan/"
    url = f"https://{city}.zu.anjuke.com{path}"
    return f"{url}?keyword={quote(keyword)}" if keyword else url


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _is_blocked(text: str, current_url: str) -> bool:
    prefix = text[:3_000]
    return (
        "antibot" in current_url
        or "verifycode" in current_url
        or "请输入验证码" in prefix
        or "人机验证" in prefix
        or "访问过于频繁" in prefix
        or len(text) < 300
    )


def _parse_card(card, source_url: str, city: str, region: str) -> Optional[dict]:
    anchor = card.select_one(".zu-info h3 a[href]")
    info = card.select_one(".zu-info")
    if not anchor or not info:
        return None

    title = _text(anchor)
    paragraphs = [_text(node) for node in info.select("p")]
    metadata = " ".join(paragraphs)
    layout_match = re.search(r"(\d+)\s*室\s*(\d+)\s*厅", metadata)
    area_match = re.search(r"([\d.]+)\s*平米", metadata)
    price_text = _text(card.select_one(".zu-side"))
    price_match = re.search(r"(\d+(?:-\d+)?)\s*元/月", price_text)
    address = _text(info.select_one("address"))
    if not (title and price_match and layout_match and area_match and address):
        return None

    raw_price = price_match.group(1)
    price = int(raw_price.split("-", 1)[0])
    if not 100 <= price <= 50_000:
        return None
    detail_url = _canonical_url(anchor["href"])
    signals = [
        signal for signal in ("安选", "7日内实拍验真", "今天维护", "新上", "VR看房")
        if signal in _text(card)
    ]
    return {
        "title": title,
        "price": price,
        "monthly_rent_cny": price,
        "price_display": f"{raw_price} 元/月",
        "room": f"{layout_match.group(1)}室{layout_match.group(2)}厅",
        "area": f"{area_match.group(1)}㎡",
        "area_sqm": float(area_match.group(1)),
        "address": address,
        "listing_type": "合租" if "合租" in metadata else "整租",
        "listing_status_signals": signals,
        "platform": "安居客",
        "city": city,
        "region": region,
        "url": detail_url,
        "detail_url": detail_url,
        "source_page": source_url,
        "data_quality": "current_public_list_page_candidate",
        "detail_verification": "not_attempted",
    }


def parse_anjuke_html(html: str, source_url: str, city: str, region: str = "",
                      keyword: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    listings = [
        item for card in soup.select("#list-content .zu-itemmod")
        if (item := _parse_card(card, source_url, city, region))
    ]
    token = keyword.replace("附近", "").strip()
    if token:
        listings = [
            item for item in listings
            if token in " ".join((item["title"], item["address"]))
        ]
    unique: dict[str, dict] = {}
    for listing in listings:
        unique.setdefault(listing["detail_url"], listing)
    return list(unique.values())


def _status(status: str, source_page: str, message: str, listings: Optional[list[dict]] = None) -> dict:
    return {
        "schema_version": "1.0",
        "status": status,
        "message": message,
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source_page": source_page,
        "listing_count": len(listings or []),
        "listings": listings or [],
    }


def scrape_anjuke_status(city: str = "cz", area: str = "", keyword: str = "",
                         max_listings: int = 30, session_file: str | None = None) -> dict:
    """读取一个安居客公开列表页，并返回明确的状态封装。"""
    url = _make_url(city, area, keyword)
    print(f"[安居客] 请求公开列表页: {url}", file=sys.stderr)
    try:
        if session_file and os.path.exists(session_file):
            html, final_url, status_code = _fetch_with_session(url, session_file)
        else:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.encoding = response.apparent_encoding or "utf-8"
            html, final_url, status_code = response.text, response.url, response.status_code
        if status_code in (401, 403, 429) or _is_blocked(html, final_url):
            return _status("blocked", url, "安居客要求验证码或访问被拦截；未绕过验证。")
        if status_code >= 400:
            return _status("error", url, f"安居客返回 HTTP {status_code}。")
        listings = parse_anjuke_html(html, url, city, area, keyword)[:max_listings]
        print(f"[安居客] 获取 {len(listings)} 条候选，详情链接 {len(listings)} 条", file=sys.stderr)
        return _status("ok", url, "公开列表页采集完成。详情页仍需用户核验。", listings)
    except requests.RequestException as exc:
        return _status("error", url, f"采集失败: {exc}")
    except Exception as exc:
        return _status("error", url, f"浏览器会话采集失败: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="安居客公开租房列表采集器")
    parser.add_argument("--city", "-c", required=True, help="城市子域，例如 cz")
    parser.add_argument("--area", "-a", default="", help="区域路径，例如 wujin")
    parser.add_argument("--keyword", "-k", default="", help="在标题和地址中筛选的地点词")
    parser.add_argument("--max", "-m", type=int, default=30)
    parser.add_argument("--output", "-o", default="")
    args = parser.parse_args()

    result = scrape_anjuke_status(args.city, args.area, args.keyword, args.max)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

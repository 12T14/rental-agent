#!/usr/bin/env python3
"""
房天下租房爬虫

通过 zu.fang.com 获取房源数据。
返回结构化 JSON：标题、价格、户型、小区、地铁距离。

用法:
    python scrape_fang.py --city gz --area tianhe
    python scrape_fang.py --city cz --keyword "大学城"
    python scrape_fang.py --city gz --area tianhe --output ../../artifacts/gz_fang.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

REQUEST_TIMEOUT = 20
MAX_RETRIES = 2
RETRY_DELAY = 2


def _resolve_output_dir() -> str:
    """解析输出目录，统一使用项目的 backend/artifacts 目录。"""
    local_dir = Path(__file__).resolve().parents[2] / "artifacts"
    local_dir.mkdir(parents=True, exist_ok=True)
    return str(local_dir)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

CITY_CODE = {
    "bj": "北京", "sh": "上海", "gz": "广州", "sz": "深圳",
    "cz": "示例市", "nj": "南京", "hz": "杭州", "cd": "成都",
    "wh": "武汉", "su": "苏州", "tj": "天津", "cq": "重庆",
}


def _fetch_with_session(url: str, session_file: str) -> tuple[str, str, int]:
    """在无头上下文中使用已保存的用户状态读取一个公开页面。"""
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


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def _make_url(city: str, area: str = "", keyword: str = "") -> str:
    """构建房天下 URL"""
    if area:
        base = f"https://{city}.zu.fang.com/house/{area}/a21/"
    else:
        base = f"https://{city}.zu.fang.com/house/"
    if keyword:
        base += f"?keyword={quote(keyword)}"
    return base


def _parse_fang_text(text: str, url: str, city: str, area: str) -> list[dict]:
    """
    解析房天下页面文本。

    数据格式:
        小区名
        距X号线XX站约XXX米 (可选)
        价格数字
        元/月
        对比
        描述标题 (含户型装修)
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    listings = []

    i = 0
    while i < len(lines):
        if lines[i] == "元/月" and i > 0 and lines[i - 1].isdigit():
            price = int(lines[i - 1])
            desc = lines[i + 2] if i + 2 < len(lines) else ""

            # 找小区名和地铁
            community = ""
            metro = ""
            for j in range(i - 2, max(i - 5, -1), -1):
                prev = lines[j]
                if "距" in prev and "号线" in prev:
                    metro = prev
                elif prev and prev != "-" and not prev.isdigit() and "距" not in prev:
                    if not community:
                        community = prev

            # 解析户型
            room_match = re.search(r"(\d)室(\d)厅", desc)
            room = f"{room_match.group(1)}室{room_match.group(2)}厅" if room_match else None

            # 解析面积
            area_match = re.search(r"([\d.]+)\s*(?:㎡|平|平米)", desc)
            area_str = f"{area_match.group(1)}㎡" if area_match else None

            if 100 <= price <= 50000 and desc:
                listings.append({
                    "title": desc[:200],
                    "price": price,
                    "monthly_rent_cny": price,
                    "community": community,
                    "metro": metro,
                    "room": room,
                    "area": area_str,
                    "platform": "房天下",
                    "city": city,
                    "region": area,
                    "url": url,
                    "detail_url": None,
                    "source_page": url,
                    "data_quality": "current_public_list_page_candidate",
                    "detail_verification": "not_available_on_parser",
                })
            i += 5
        else:
            i += 1

    return listings


def scrape_fang(
    city: str = "gz",
    area: str = "",
    keyword: str = "",
    max_listings: int = 30,
    output: Optional[str] = None,
    session_file: Optional[str] = None,
    status: bool = False,
) -> list[dict] | dict:
    """
    爬取房天下租房信息。

    Args:
        city: 城市代码
        area: 区域名
        keyword: 搜索关键词
        max_listings: 最大房源数
        output: 输出文件路径

    Returns:
        房源信息列表
    """
    url = _make_url(city, area, keyword)
    print(f"[房天下] 请求: {url}", file=sys.stderr)

    blocked = False
    for attempt in range(MAX_RETRIES):
        try:
            if session_file and os.path.exists(session_file):
                html, final_url, status_code = _fetch_with_session(url, session_file)
            else:
                resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                resp.encoding = resp.apparent_encoding or "utf-8"
                html, final_url, status_code = resp.text, resp.url, resp.status_code

            if status_code in (401, 403, 429) or "验证" in html[:500] or len(html) < 500:
                blocked = True
                print(f"[房天下] 反爬或内容过少 (尝试 {attempt + 1})", file=sys.stderr)
                time.sleep(RETRY_DELAY)
                continue

            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            body = soup.find("body") or soup
            text = body.get_text("\n", strip=True)

            listings = _parse_fang_text(text, url, city, area)

            # 去重
            seen = set()
            unique = []
            for l in listings:
                if l["title"] not in seen:
                    seen.add(l["title"])
                    unique.append(l)

            unique = unique[:max_listings]
            unique.sort(key=lambda x: x["price"])

            print(f"[房天下] 获取 {len(unique)} 条房源", file=sys.stderr)

            if output or not sys.stdout.isatty():
                _save_results(unique, output, city, area)

            if status:
                return {"status": "ok", "message": "公开列表页采集完成。", "listings": unique,
                "source_page": url, "session_used": bool(session_file)}
            return unique

        except Exception as e:
            print(f"[房天下] 错误: {e}", file=sys.stderr)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)

    if status:
        return {"status": "blocked" if blocked else "error",
                "message": "房天下要求验证或访问被拦截；未绕过验证。" if blocked else "采集失败。",
                "listings": [], "source_page": url, "session_used": bool(session_file)}
    return []


def _save_results(listings: list[dict], output: Optional[str], city: str, area: str):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output or f"{_resolve_output_dir()}/rentals_fang_{city}_{area}_{ts}.json"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(listings, f, ensure_ascii=False, indent=2)
    print(f"[房天下] 已保存: {out_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="房天下租房爬虫")
    parser.add_argument("--city", "-c", required=True, help="城市代码")
    parser.add_argument("--area", "-a", default="", help="区域名")
    parser.add_argument("--keyword", "-k", default="", help="搜索关键词")
    parser.add_argument("--max", "-m", type=int, default=30, help="最大房源数")
    parser.add_argument("--output", "-o", default=None, help="输出 JSON 文件路径")
    args = parser.parse_args()

    if args.city not in CITY_CODE:
        print(f"错误: 未知城市代码 '{args.city}'", file=sys.stderr)
        sys.exit(1)

    listings = scrape_fang(
        city=args.city,
        area=args.area,
        keyword=args.keyword,
        max_listings=args.max,
        output=args.output,
    )

    print(json.dumps(listings, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

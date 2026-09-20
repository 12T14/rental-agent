#!/usr/bin/env python3
"""在不绕过访问控制的前提下采集安居客公开租房列表卡片。

采集器只读取一个公开列表页；遇到验证码或访问控制页面就停止，
不访问私有接口、不轮换代理，也不尝试解决验证挑战。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from platform_pages import (
    allowed_platform_url, classify_list_result, is_verification_page, platform_city,
    read_public_page, save_page_snapshot,
)

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def _fetch_with_session(url: str, session_file: str | None) -> tuple[str, str, int]:
    return read_public_page(url, session_file)


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
    return is_verification_page(text, current_url)


def _parse_card(card, source_url: str, city: str, region: str) -> Optional[dict]:
    anchor = card.select_one(".zu-info h3 a[href]")
    info = card.select_one(".zu-info")
    if not anchor or not info:
        return None

    title = _text(anchor)
    paragraphs = [_text(node) for node in info.select("p")]
    metadata = " ".join(paragraphs)
    layout_match = re.search(r"(\d+)\s*室\s*(\d+)\s*厅", metadata)
    area_match = re.search(r"([\d.]+)\s*(?:平米|㎡|m²)", metadata)
    price_text = _text(card.select_one(".zu-side"))
    price_match = re.search(r"(\d+(?:-\d+)?)\s*元\s*/\s*月", price_text)
    address = _text(info.select_one("address"))
    if not (title and price_match):
        return None

    raw_price = price_match.group(1)
    price = int(raw_price.split("-", 1)[0])
    if not 100 <= price <= 50_000:
        return None
    detail_url = _canonical_url(urljoin(source_url, anchor["href"]))
    if not allowed_platform_url("anjuke", detail_url) or platform_city(detail_url) != city:
        return None
    if urlparse(detail_url).path.rstrip("/") == "/fangyuan":
        return None
    signals = [
        signal for signal in ("安选", "7日内实拍验真", "今天维护", "新上", "VR看房")
        if signal in _text(card)
    ]
    return {
        "title": title,
        "price": price,
        "monthly_rent_cny": price,
        "price_display": f"{raw_price} 元/月",
        "room": f"{layout_match.group(1)}室{layout_match.group(2)}厅" if layout_match else None,
        "area": f"{area_match.group(1)}㎡" if area_match else None,
        "area_sqm": float(area_match.group(1)) if area_match else None,
        "address": address,
        "listing_type": "合租" if "合租" in metadata else "整租" if "整租" in metadata else None,
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
    """地点全名仅供上层评估匹配证据，不在这里硬筛掉行政区候选。"""
    soup = BeautifulSoup(html, "lxml")
    listings = [
        item for card in soup.select(".zu-itemmod")
        if (item := _parse_card(card, source_url, city, region))
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
        html, final_url, status_code = _fetch_with_session(url, session_file)
        save_page_snapshot(html, "anjuke", city, area)
        if status_code in (401, 403, 429) or _is_blocked(html, final_url):
            result = _status("blocked", url, "安居客要求官方验证，已停止访问；可进行一次人工验证。")
        elif status_code >= 400:
            result = _status("error", url, f"安居客返回 HTTP {status_code}。")
        elif not allowed_platform_url("anjuke", final_url) or platform_city(final_url) != city:
            result = _status("city_mismatch", url, "安居客最终页面不属于请求城市，已丢弃结果。")
        elif area and urlparse(url).path.rstrip("/") != urlparse(final_url).path.rstrip("/"):
            result = _status("region_mismatch", url, "安居客未保留请求区域路径，未扩大为全市结果。")
        else:
            parsed = parse_anjuke_html(html, final_url, city, area, keyword)
            raw_count = len(BeautifulSoup(html, "lxml").select(".zu-itemmod"))
            source_status = classify_list_result(html, raw_count, len(parsed))
            messages = {"ok": "公开列表卡片采集完成。详情页仍需核验。", "partial": "部分卡片未通过解析或城市校验。",
                        "empty": "平台明确显示当前查询暂无房源。", "parse_error": "未识别有效房源卡片，不能视为平台没有房源。"}
            listings = parsed[:max(1, min(int(max_listings), 30))]
            result = _status(source_status, final_url, messages[source_status], listings)
            result.update(raw_card_count=raw_count, parsed_count=len(parsed),
                          keyword_match_count=sum(bool(keyword) and keyword in f"{item['title']} {item['address']}" for item in parsed),
                          match_scope="administrative_region" if area else "city")
            print(f"[安居客] 获取 {len(listings)} 条候选，详情链接 {len(listings)} 条", file=sys.stderr)
        result.update(snapshot_saved=True, session_used=bool(session_file))
        return result
    except Exception as exc:
        return _status("error", url, f"浏览器会话采集失败: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="安居客公开租房列表采集器")
    parser.add_argument("--city", "-c", required=True, help="城市子域，例如 cz")
    parser.add_argument("--area", "-a", default="", help="区域路径，例如 wujin")
    parser.add_argument("--keyword", "-k", default="", help="目标地点关键词（不进行完整名称硬筛选）")
    parser.add_argument("--max", "-m", type=int, default=30)
    parser.add_argument("--output", "-o", default="")
    parser.add_argument("--session", default=None, help="该平台已验证的浏览器会话文件")
    args = parser.parse_args()

    result = scrape_anjuke_status(args.city, args.area, args.keyword, args.max, args.session)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

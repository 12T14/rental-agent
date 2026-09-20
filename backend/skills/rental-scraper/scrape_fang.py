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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from platform_pages import (
    allowed_platform_url, classify_list_result, is_verification_page, platform_city,
    read_public_page, save_page_snapshot,
)

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CITY_CODE = {
    "bj": "北京", "sh": "上海", "gz": "广州", "sz": "深圳",
    "cz": "常州", "nj": "南京", "hz": "杭州", "cd": "成都",
    "wh": "武汉", "su": "苏州", "tj": "天津", "cq": "重庆",
}


def _fetch_with_session(url: str, session_file: str | None) -> tuple[str, str, int]:
    return read_public_page(url, session_file)


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def _make_url(city: str, area: str = "", keyword: str = "") -> str:
    """先打开城市入口，区域代码只从该页官方筛选链接获取。"""
    return f"https://zu.fang.com/{city}/house/"


AREA_NAMES = {
    "wujin": "武进", "xinbei": "新北", "tianning": "天宁", "zhonglou": "钟楼",
    "jingkai": "经开", "gulou": "鼓楼", "gulouqu": "鼓楼", "xuanwu": "玄武",
    "qinhuai": "秦淮", "jianye": "建邺", "jiangning": "江宁", "pukou": "浦口",
    "qixia": "栖霞", "yuhuatai": "雨花台", "tianhe": "天河", "haizhu": "海珠",
    "yuexiu": "越秀", "chaoyang": "朝阳", "haidian": "海淀", "pudong": "浦东",
}


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _region_link(card, source_url: str, city: str) -> tuple[str, str]:
    for anchor in card.select("a[href]"):
        value = urljoin(source_url, anchor["href"])
        if allowed_platform_url("fang", value) and re.fullmatch(
            rf"/{re.escape(city)}/house-a\d+(?:-[^/]+)?/?", urlparse(value).path
        ):
            return value, _text(anchor)
    return "", ""


def _region_key(value: str, city: str) -> str:
    match = re.search(rf"/{re.escape(city)}/house-(a\d+)(?:-|/|$)", urlparse(value).path)
    return match.group(1) if match else ""


def _resolve_region_url(html: str, source_url: str, city: str, area: str) -> str:
    label = AREA_NAMES.get(area.lower(), area).removesuffix("区")
    for anchor in BeautifulSoup(html, "lxml").select("a[href]"):
        value = urljoin(source_url, anchor["href"])
        if not allowed_platform_url("fang", value) or not re.fullmatch(
            rf"/{re.escape(city)}/house-a\d+/?", urlparse(value).path
        ):
            continue
        if _text(anchor).removesuffix("区") == label or urlparse(value).path.strip("/").split("/")[-1] == area:
            return value
    return ""


def parse_fang_html(html: str, source_url: str, city: str, area: str = "") -> list[dict]:
    """每张卡片独立读取，不跨卡片关联标题、价格和小区。"""
    listings: dict[str, dict] = {}
    for card in BeautifulSoup(html, "lxml").select("dl.list"):
        anchor = card.select_one("p.title a[href]")
        if anchor is None:
            anchor = next((a for a in card.select('a[href*="/chuzu/"]') if _text(a)), None)
        if anchor is None:
            continue
        title = _text(anchor)
        detail_url = urljoin(source_url, anchor["href"]).split("?", 1)[0]
        if not title or not allowed_platform_url("fang", detail_url) or platform_city(detail_url) != city:
            continue
        if not re.search(r"/chuzu/[^/]+\.htm$", urlparse(detail_url).path):
            continue
        price_node = card.select_one(".moreInfo .price, .moreInfo .price_num, .price")
        price_match = re.search(r"\d+(?:\.\d+)?", _text(price_node))
        if not price_match:
            continue
        price = float(price_match.group())
        if not 100 <= price <= 50000:
            continue
        info = card.select_one("dd.info") or card
        metadata = " ".join(_text(p) for p in info.select("p") if "title" not in p.get("class", []))
        room_match = re.search(r"(\d+)\s*室\s*(\d+)\s*厅", metadata)
        area_match = re.search(r"([\d.]+)\s*(?:㎡|平米|m²)", metadata)
        community_anchor = info.select_one('a[href*="house-xm"]')
        region_url, region_name = _region_link(card, source_url, city)
        metro = next((_text(p) for p in info.select("p") if re.search(r"距.*(?:号线|地铁).*米", _text(p))), "")
        listings.setdefault(detail_url, {
            "listing_id": urlparse(detail_url).path.rsplit("/", 1)[-1],
            "title": title[:200], "price": price, "monthly_rent_cny": price,
            "community": _text(community_anchor), "address": "", "metro": metro,
            "room": f"{room_match.group(1)}室{room_match.group(2)}厅" if room_match else None,
            "listing_type": "合租" if "合租" in metadata else "整租" if "整租" in metadata else None,
            "area": f"{area_match.group(1)}㎡" if area_match else None,
            "area_sqm": float(area_match.group(1)) if area_match else None,
            "platform": "房天下", "city": city, "region": area,
            "region_name": region_name, "region_url": region_url,
            "region_evidence": "card_region_link" if region_url else "unverified_card_area",
            "url": detail_url, "detail_url": detail_url, "source_page": source_url,
            "data_quality": "current_public_list_page_candidate", "detail_verification": "not_attempted",
        })
    return list(listings.values())


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

    result = {
        "schema_version": "1.0", "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "status": "error", "message": "采集失败。", "listings": [], "listing_count": 0,
        "source_page": url, "session_used": bool(session_file), "snapshot_saved": False,
        "requested_area": area, "match_scope": "administrative_region" if area else "city",
        "warnings": [],
    }
    try:
        region_url = ""
        for phase in ("city", "region") if area else ("city",):
            html, final_url, status_code = _fetch_with_session(url, session_file)
            save_page_snapshot(html, "fang", city, phase)
            result.update(source_page=url, snapshot_saved=True)
            if status_code in (401, 403, 429) or is_verification_page(html, final_url):
                result.update(status="blocked", message="房天下要求官方验证，已停止访问；可进行一次人工验证。")
                break
            if status_code >= 400:
                result.update(status="error", message=f"房天下返回 HTTP {status_code}。")
                break
            if not allowed_platform_url("fang", final_url) or platform_city(final_url) != city:
                result.update(status="city_mismatch", message="房天下最终页面不属于请求城市，已丢弃结果。")
                break
            if phase == "city" and area:
                region_url = _resolve_region_url(html, final_url, city, area)
                if not region_url:
                    result.update(status="region_unavailable", message=f"未在房天下官方页面找到区域 {area} 的筛选链接，未扩大到全市。")
                    break
                url = region_url
                continue
            if region_url and urlparse(final_url).path.rstrip("/") != urlparse(region_url).path.rstrip("/"):
                result.update(status="region_mismatch", message="房天下未保留已选区域路径，已丢弃不可靠结果。")
                break
            raw_count = len(BeautifulSoup(html, "lxml").select("dl.list"))
            parsed = parse_fang_html(html, final_url, city, area)
            parse_status = classify_list_result(html, raw_count, len(parsed))
            if region_url:
                requested_region_key = _region_key(region_url, city)
                accepted = []
                conflicting = 0
                for item in parsed:
                    card_region_key = _region_key(item["region_url"], city) if item["region_url"] else ""
                    if card_region_key and card_region_key != requested_region_key:
                        conflicting += 1
                        continue
                    item["region_scope"] = "administrative_region"
                    item["region_scope_name"] = AREA_NAMES.get(area.lower(), area).removesuffix("区")
                    item["region_evidence"] = "card_region_link" if card_region_key else "official_filter_page"
                    accepted.append(item)
                if conflicting:
                    parse_status = "partial"
                    result["warnings"].append(f"已丢弃 {conflicting} 条卡片中明确指向其他行政区的记录。")
                if accepted and any(item["region_evidence"] == "official_filter_page" for item in accepted):
                    result["warnings"].append("候选来自官方行政区筛选页；未带独立区域链接的卡片仅证明属于该页范围，具体地址仍需详情和地图核验。")
                parsed = accepted
            else:
                for item in parsed:
                    item["region_scope"] = "city"
                    item["region_evidence"] = "city_page"
            listings = sorted(parsed, key=lambda item: item["price"])[:max(1, min(int(max_listings), 30))]
            messages = {"ok": "公开列表卡片采集完成，已提取真实详情链接。", "partial": "部分卡片未通过解析或区域校验。",
                        "empty": "平台明确显示当前区域暂无房源。", "parse_error": "未识别有效房源卡片，不能视为平台没有房源。"}
            result.update(status=parse_status, message=messages[parse_status], listings=listings,
                          listing_count=len(listings), source_page=final_url, raw_card_count=raw_count,
                          parsed_count=len(parsed), applied_region_url=region_url)
            result["warnings"].append("本次按行政区或城市读取公开列表，目标关键词不作为完整名称硬筛选；距离需地图核验。")
        if output:
            target = Path(output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result if status else result["listings"], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        result.update(status="error", message=f"浏览器采集失败: {exc}")
    return result if status else result["listings"]


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
    parser.add_argument("--session", default=None, help="该平台已验证的浏览器会话文件")
    parser.add_argument("--status", action="store_true", help="输出明确的平台状态")
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
        session_file=args.session,
        status=args.status,
    )

    print(json.dumps(listings, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

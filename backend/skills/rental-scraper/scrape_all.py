#!/usr/bin/env python3
"""统一租房搜索入口。

使用 Playwright 串行调用 58 同城、安居客和房天下，合并去重后按平台轮询取数；
每个平台内部按价格排序，避免单一平台占满候选池。
单个平台失败只记录警告，不影响其他平台继续返回结果。

用法:
    python scrape_all.py --city cz --keyword "目标地点"
    python scrape_all.py --city bj --area chaoyang --keyword "一室一厅"
    python scrape_all.py --city gz --area tianhe --max 50 --output ../../artifacts/gz.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scrape_58 import scrape_58_status
from scrape_anjuke import scrape_anjuke_status
from scrape_fang import scrape_fang

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _normalize_listing(listing: dict, retrieved_at: str) -> dict:
    """保持平台适配器兼容，同时暴露统一的数据结构。"""
    item = dict(listing)
    url_type = str(item.get("url_type") or "").strip().lower()
    if url_type:
        item["url_type"] = url_type
    if "price" not in item and item.get("monthly_rent_cny") is not None:
        match = re.search(r"\d+(?:\.\d+)?", str(item["monthly_rent_cny"]))
        if match:
            item["price"] = float(match.group())
    if "monthly_rent_cny" not in item and item.get("price") is not None:
        item["monthly_rent_cny"] = item["price"]
    # ``url`` 在不同适配器中可能只是来源列表页。只有适配器明确标记
    # ``url_type=detail`` 时才允许把它提升为详情 URL，不能凭字段名猜测。
    if url_type == "source_list":
        item.pop("detail_url", None)
    elif not item.get("detail_url") and url_type == "detail":
        item["detail_url"] = item.get("url")
    if "source_page" not in item:
        item["source_page"] = item.get("url")
    item.setdefault("retrieved_at", retrieved_at)
    item.setdefault("data_quality", "current_public_list_page_candidate")
    return item


def _listing_key(listing: dict) -> tuple:
    """优先使用详情 URL；不要合并不同来源的同标题房源。"""
    detail_url = listing.get("detail_url") or ""
    if detail_url and detail_url != listing.get("source_page"):
        return ("url", detail_url.split("?", 1)[0])
    return (
        "fallback",
        listing.get("platform", ""),
        listing.get("title", "")[:120],
        listing.get("price"),
        listing.get("area_sqm"),
    )


def _resolve_output_dir() -> str:
    """解析输出目录，统一使用项目的 backend/artifacts 目录。"""
    local_dir = Path(__file__).resolve().parents[2] / "artifacts"
    local_dir.mkdir(parents=True, exist_ok=True)
    return str(local_dir)


def _balanced_platform_limit(listings: list[dict], limit: int) -> list[dict]:
    """按平台轮询取数，避免单一来源占满合并候选池。"""
    platform_order: list[str] = []
    queues: dict[str, list[dict]] = {}
    for listing in listings:
        platform = str(listing.get("platform") or "未知平台")
        if platform not in queues:
            platform_order.append(platform)
            queues[platform] = []
        queues[platform].append(listing)

    for queue in queues.values():
        queue.sort(key=lambda item: item["price"])

    selected: list[dict] = []
    offsets = {platform: 0 for platform in platform_order}
    while len(selected) < limit:
        added = False
        for platform in platform_order:
            offset = offsets[platform]
            queue = queues[platform]
            if offset >= len(queue):
                continue
            selected.append(queue[offset])
            offsets[platform] += 1
            added = True
            if len(selected) >= limit:
                break
        if not added:
            break
    return selected


def scrape_all(
    city: str = "bj",
    area: str = "",
    keyword: str = "",
    max_listings: int = 60,
    per_platform_limit: int = 30,
    output: Optional[str] = None,
    session_file: Optional[str] = None,
    platform_sessions: Optional[dict[str, str]] = None,
    status: bool = False,
) -> list[dict] | dict:
    """
    统一搜索所有平台。

    Args:
        city: 城市代码
        area: 区域名
        keyword: 搜索关键词
        max_listings: 合并去重后的最大房源数（最多 60）
        per_platform_limit: 单个平台的最大抓取数（最多 30）
        output: 输出文件路径
        session_file: 58 同城 Playwright 会话状态（由 --login 生成）
        platform_sessions: 各平台可选的 Playwright 会话状态文件
        status: 为 True 时返回带平台状态的结构化封装，供上层判断降级

    返回：
        合并去重后的房源列表（按平台均衡选取，各平台内部按价格升序）；
        status=True 时返回 {platforms, blocked_hints, listings, ...} 封装
    """
    max_listings = max(1, min(int(max_listings), 60))
    per_platform_limit = max(1, min(int(per_platform_limit), 30))
    retrieved_at = datetime.now(timezone.utc).astimezone().isoformat()
    all_listings = []
    platform_stats = {}
    platform_diagnostics = {}
    blocked_hints: list[str] = []
    warnings: list[str] = []
    platform_sessions = platform_sessions or {}

    def record_diagnostics(platform: str, payload: dict) -> None:
        platform_diagnostics[platform] = {key: payload[key] for key in (
            "status", "message", "source_page", "raw_card_count", "parsed_count", "keyword_match_count",
            "match_scope", "requested_area", "applied_region_url", "snapshot_saved", "session_used",
        ) if key in payload}
        warnings.extend(payload.get("warnings", []))
        if payload.get("status") not in {"ok", "empty", "blocked"} and payload.get("message"):
            warnings.append(f"{platform}: {payload['message']}")

    # ---- 平台1: 58同城（Playwright） ----
    print("=" * 50, file=sys.stderr)
    print("[统一搜索] 平台 1/3: 58同城", file=sys.stderr)
    try:
        result_58 = scrape_58_status(
            city=city, area=area, keyword=keyword,
            max_listings=per_platform_limit, output=None, session_file=session_file,
        )
        listings_58 = result_58["listings"]
        record_diagnostics("58同城", result_58)
        all_listings.extend(_normalize_listing(item, retrieved_at) for item in listings_58)
        platform_stats["58同城"] = f"{result_58['status']} ({len(listings_58)} 条)"
        if result_58["status"] == "blocked":
            hint = "58同城本次被平台验证码拦截；上层 Agent 可调用人工验证工具弹出浏览器，成功后只重试一次。"
            blocked_hints.append(hint)
            print("[统一搜索] 58 被拦截，继续使用其他平台。", file=sys.stderr)
        elif result_58["status"] == "city_mismatch":
            warning = result_58.get("message", "58 返回了其他城市页面，已丢弃该平台结果。")
            warnings.append(warning)
            print(f"[统一搜索] {warning}", file=sys.stderr)
    except Exception as e:
        platform_stats["58同城"] = f"失败: {e}"
        print(f"[统一搜索] 58同城异常: {e}", file=sys.stderr)

    time.sleep(1)

    # ---- 平台2: 安居客（公开列表页） ----
    print("[统一搜索] 平台 2/3: 安居客", file=sys.stderr)
    try:
        result_anjuke = scrape_anjuke_status(
            city=city, area=area, keyword=keyword, max_listings=per_platform_limit,
            session_file=platform_sessions.get("anjuke"),
        )
        listings_anjuke = result_anjuke["listings"]
        record_diagnostics("安居客", result_anjuke)
        all_listings.extend(_normalize_listing(item, retrieved_at) for item in listings_anjuke)
        platform_stats["安居客"] = f"{result_anjuke['status']} ({len(listings_anjuke)} 条)"
        if result_anjuke["status"] == "blocked":
            blocked_hints.append("安居客本次被平台验证拦截；可调用统一人工验证工具，验证成功后只重试一次。")
            print("[统一搜索] 安居客被拦截，继续使用其他平台。", file=sys.stderr)
    except Exception as e:
        platform_stats["安居客"] = f"失败: {e}"
        print(f"[统一搜索] 安居客异常: {e}", file=sys.stderr)

    time.sleep(1)

    # ---- 平台3: 房天下（公开浏览器卡片） ----
    print("[统一搜索] 平台 3/3: 房天下", file=sys.stderr)
    try:
        fang_result = scrape_fang(
            city=city, area=area, keyword=keyword,
            max_listings=per_platform_limit, output=None,
            session_file=platform_sessions.get("fang"), status=True,
        )
        listings_fang = fang_result.get("listings", [])
        record_diagnostics("房天下", fang_result)
        all_listings.extend(_normalize_listing(item, retrieved_at) for item in listings_fang)
        platform_stats["房天下"] = f"{fang_result.get('status', 'ok')} ({len(listings_fang)} 条)"
        if fang_result.get("status") == "blocked":
            blocked_hints.append("房天下本次被验证码拦截；上层 Agent 可调用人工验证工具弹出浏览器，成功后只重试一次。")
            print("[统一搜索] 房天下被拦截，继续使用其他平台。", file=sys.stderr)
    except Exception as e:
        platform_stats["房天下"] = f"失败: {e}"
        print(f"[统一搜索] 房天下异常: {e}", file=sys.stderr)

    # ---- 去重（优先详情 URL；无详情 URL 时按平台+核心字段） ----
    seen = set()
    unique = []
    for l in all_listings:
        key = _listing_key(l)
        if key not in seen:
            seen.add(key)
            unique.append(l)

    # ---- 过滤 + 平台均衡截断 ----
    unique = [l for l in unique if 100 <= l.get("price", 0) <= 50000]
    unique = _balanced_platform_limit(unique, max_listings)

    # ---- 汇总输出 ----
    print("\n" + "=" * 50, file=sys.stderr)
    print(f"[统一搜索] 汇总（去重后 {len(unique)} 条）:", file=sys.stderr)
    for platform, count in platform_stats.items():
        print(f"  - {platform}: {count}", file=sys.stderr)

    # ---- 组装返回结果 ----
    if status:
        result: dict | list = {
            "schema_version": "1.0",
            "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "city": city,
            "area": area,
            "keyword": keyword,
            "platforms": platform_stats,
            "platform_diagnostics": platform_diagnostics,
            "blocked_hints": blocked_hints,
            "warnings": warnings,
            "listing_count": len(unique),
            "listings": unique,
        }
    else:
        result = unique

    # ---- 保存 ----
    if output or not sys.stdout.isatty():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = output or f"{_resolve_output_dir()}/rentals_all_{city}_{ts}.json"
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[统一搜索] 已保存: {out_path}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="统一租房搜索")
    parser.add_argument("--city", "-c", required=True, help="城市代码")
    parser.add_argument("--area", "-a", default="", help="区域名")
    parser.add_argument("--keyword", "-k", default="", help="搜索关键词")
    parser.add_argument("--max", "-m", type=int, default=60, help="合并后的最大房源数（最多 60）")
    parser.add_argument("--per-platform", type=int, default=30, help="单个平台最大抓取数（最多 30）")
    parser.add_argument("--output", "-o", default=None, help="输出 JSON 文件路径")
    parser.add_argument("--session", default="", help="58 Playwright storage state 文件")
    parser.add_argument("--status", action="store_true",
                        help="输出包含各平台状态的结构化封装（子智能体据此判断降级）")
    args = parser.parse_args()

    result = scrape_all(
        city=args.city,
        area=args.area,
        keyword=args.keyword,
        max_listings=args.max,
        per_platform_limit=args.per_platform,
        output=args.output,
        session_file=args.session or None,
        status=args.status,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

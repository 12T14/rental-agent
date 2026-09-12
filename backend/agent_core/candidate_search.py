"""搜索已配置 rental-scraper 技能的租房 Agent 工具。

调用方通过 RENTAL_DEMO_MODE 明确选择数据源。
真实控制台使用 live；offline 仅用于冒烟测试，绝不把夹具记录伪装成实时房源。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

AGENT_CORE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_CORE_ROOT.parent
SKILL_ROOT = PROJECT_ROOT / "skills" / "rental-scraper"
FIXTURE_PATH = AGENT_CORE_ROOT / "fixtures" / "candidates.json"
DEFAULT_58_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "58" / "storage_state.json"
DEFAULT_ANJUKE_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "anjuke" / "storage_state.json"
DEFAULT_FANG_SESSION = PROJECT_ROOT / "runtime" / "sessions" / "fang" / "storage_state.json"

# 只有最近一次搜索可以授权浏览器验证提示。
# 这样可避免过期会话上下文或详情页拦截误触发验证。
_LAST_PLATFORM_SEARCH_STATE: dict[str, dict[str, Any]] = {}
VERIFICATION_REQUEST_TTL_SECONDS = 300


def _record_platform_search_state(platform: str, *, blocked: bool, city: str, area: str, keyword: str) -> None:
    _LAST_PLATFORM_SEARCH_STATE[platform] = {
        "blocked": bool(blocked),
        "city": city,
        "area": area,
        "keyword": keyword,
        "recorded_at": time.monotonic(),
    }


def consume_platform_verification_request(platform: str, city: str = "", area: str = "", keyword: str = "") -> bool:
    """消费某个平台和查询的一次最新搜索阶段拦截状态。"""
    state = _LAST_PLATFORM_SEARCH_STATE.get(platform, {})
    fresh = bool(state.get("blocked")) and time.monotonic() - state.get("recorded_at", 0.0) <= VERIFICATION_REQUEST_TTL_SECONDS
    same_query = all(
        (value or "").strip() == (state.get(key) or "").strip()
        for value, key in ((city, "city"), (area, "area"), (keyword, "keyword"))
    )
    if not (fresh and same_query):
        return False
    state["blocked"] = False
    return True


def _record_58_search_state(*, blocked: bool, city: str, area: str, keyword: str) -> None:
    _record_platform_search_state("58", blocked=blocked, city=city, area=area, keyword=keyword)


def consume_58_verification_request(city: str = "", area: str = "", keyword: str = "") -> bool:
    return consume_platform_verification_request("58", city, area, keyword)

CITY_ALIASES = {
    "北京": "bj", "上海": "sh", "广州": "gz", "深圳": "sz",
    "南京": "nj", "杭州": "hz", "成都": "cd", "武汉": "wh", "苏州": "su",
    "西安": "xa", "天津": "tj", "重庆": "cq", "长沙": "cs", "郑州": "zz",
    "东莞": "dg", "青岛": "qd", "合肥": "hf", "宁波": "nb", "昆明": "km",
    "沈阳": "sy", "大连": "dl", "福州": "fz", "厦门": "xm", "济南": "jn",
    "无锡": "wx", "南昌": "nc",
}
AREA_ALIASES = {
    "新城区": "xinbei", "新城区区": "xinbei",
    "天宁": "tianning", "天宁区": "tianning", "钟楼": "zhonglou", "钟楼区": "zhonglou",
    "经开": "jingkai", "经开区": "jingkai",
}


def _load_fixture() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _compact(item: dict[str, Any]) -> dict[str, Any]:
    detail_url = item.get("detail_url")
    listing_url = detail_url or item.get("url") or item.get("source_page")
    return {
        key: item.get(key)
        for key in (
            "listing_id", "title", "price", "monthly_rent_cny", "price_display",
            "room", "area", "area_sqm", "community", "address", "listing_type",
            "tags", "metro", "platform", "city", "region", "url", "detail_url",
            "source_page", "data_quality", "detail_verification",
            "location_match", "location_match_score", "location_match_terms",
        )
    } | {
        "listing_url": listing_url,
        "url_type": "detail" if detail_url else "source_list",
    }


def _city_code(city: str) -> str:
    value = (city or "").strip().lower()
    return CITY_ALIASES.get(value, value)


def _area_code(area: str) -> str:
    value = (area or "").strip().lower()
    return AREA_ALIASES.get(value, value.removesuffix("区"))


def _location_evidence(item: dict[str, Any], keyword: str) -> dict[str, Any]:
    """报告文本匹配证据，但不声称平台结果在地理位置上确实相邻。"""
    target = (keyword or "").replace("附近", "").replace("周边", "").strip()
    haystack = " ".join(str(item.get(key) or "") for key in ("title", "community", "address", "region", "tags"))
    if not target:
        return {"location_match": "not_requested", "location_match_score": 0.0}
    if target in haystack:
        return {"location_match": "exact_text", "location_match_score": 1.0}
    # 中文全称经常不会原样出现；只把有意义的片段作为弱证据，
    # 距离仍保持未解析状态。
    chunks = [part for part in re.split(r"[\s,，。/、·()（）\-]+", target) if len(part) >= 2]
    hits = [part for part in chunks if part in haystack]
    if hits:
        return {
            "location_match": "partial_text",
            "location_match_score": round(len(hits) / max(1, len(chunks)), 2),
            "location_match_terms": hits,
        }
    return {"location_match": "unverified", "location_match_score": 0.0}


@tool
def search_rental_candidates(
    city: str = "",
    keyword: str = "",
    area: str = "",
    max_results: int = 10,
) -> str:
    """搜索租房列表候选，并返回带 URL 的精简记录。

    ``city`` 可以是受支持的城市代码（例如 ``cz``）或中文名称。优先返回详情 URL；
    如果平台只提供列表页，则将 ``url_type`` 设为 ``source_list``，避免 Agent 把它当成详情页。
    """
    max_results = max(1, min(int(max_results), 20))
    mode = os.getenv("RENTAL_DEMO_MODE", "offline").strip().lower()
    live = mode == "live"
    # 不要从机构名称推断实际城市。学校或公司名称中的城市前缀，
    # 可能并不是实体所在位置。
    city = _city_code(city)
    area = _area_code(area)
    # 新搜索会使之前的验证请求失效。
    for platform in ("58", "anjuke", "fang"):
        _record_platform_search_state(platform, blocked=False, city=city, area=area, keyword=keyword)

    if not city:
        return json.dumps({
            "mode": "live_skill" if live else "offline_fixture",
            "status": "needs_city",
            "listing_count": 0,
            "listings": [],
            "detail_urls": [],
            "message": "平台入口必须知道城市。请补充目标公司/学校所在城市；不会把地标名称猜成行政区。",
        }, ensure_ascii=False)

    if not live:
        items = [
            item for item in _load_fixture()
            if not city or item.get("city") == city
        ]
        if keyword:
            token = keyword.replace("附近", "").strip()
            items = [
                item for item in items
                if token in f"{item.get('title', '')} {item.get('community', '')}"
            ] or items
        return json.dumps({
            "mode": "offline_fixture",
            "status": "ok",
            "listing_count": min(len(items), max_results),
            "listings": [(_compact(item) | _location_evidence(item, keyword)) for item in items[:max_results]],
            "detail_urls": [item["detail_url"] for item in items[:max_results] if item.get("detail_url")],
            "message": "这是离线夹具数据，仅用于回归测试，不能代表实时房源。",
        }, ensure_ascii=False)

    sys.path.insert(0, str(SKILL_ROOT))
    from scrape_all import scrape_all

    # 已验证会话会自动复用。文件包含敏感浏览器状态，
    # 并且位于技能源码目录之外。
    session_file = Path(os.getenv("RENTAL_58_SESSION_FILE", str(DEFAULT_58_SESSION))).expanduser()
    anjuke_session_file = Path(os.getenv("RENTAL_ANJUKE_SESSION_FILE", str(DEFAULT_ANJUKE_SESSION))).expanduser()
    fang_session_file = Path(os.getenv("RENTAL_FANG_SESSION_FILE", str(DEFAULT_FANG_SESSION))).expanduser()
    result = scrape_all(
        city=city,
        area=area,
        keyword=keyword,
        max_listings=max_results,
        session_file=str(session_file) if session_file.exists() else None,
        platform_sessions={
            "anjuke": str(anjuke_session_file) if anjuke_session_file.exists() else None,
            "fang": str(fang_session_file) if fang_session_file.exists() else None,
        },
        status=True,
    )
    listings = result.get("listings", []) if isinstance(result, dict) else result
    compact = [(_compact(item) | _location_evidence(item, keyword)) for item in listings[:max_results]]
    blocked_hints = result.get("blocked_hints", []) if isinstance(result, dict) else []
    platform_status = result.get("platforms", {}) if isinstance(result, dict) else {}
    verification_platforms = []
    if any("58同城" in str(hint) for hint in blocked_hints):
        verification_platforms.append("58同城")
    if str(platform_status.get("安居客", "")).lower().startswith("blocked"):
        verification_platforms.append("安居客")
    if str(platform_status.get("房天下", "")).lower().startswith("blocked"):
        verification_platforms.append("房天下")
    needs_verification = bool(verification_platforms)
    _record_platform_search_state(
        "58",
        blocked="58同城" in verification_platforms,
        city=city,
        area=area,
        keyword=keyword,
    )
    _record_platform_search_state(
        "anjuke",
        blocked="安居客" in verification_platforms,
        city=city,
        area=area,
        keyword=keyword,
    )
    _record_platform_search_state(
        "fang",
        blocked="房天下" in verification_platforms,
        city=city,
        area=area,
        keyword=keyword,
    )
    return json.dumps({
        "mode": "live_skill",
        "status": "ok" if compact else ("partial" if result else "failed"),
        "platforms": platform_status,
        "blocked_hints": blocked_hints,
        "needs_human_verification": needs_verification,
        "human_verification_platforms": verification_platforms,
        "human_verification_reason": "list_search_blocked" if needs_verification else None,
        "browser_session_used": any(path.exists() for path in (
            session_file, anjuke_session_file, fang_session_file,
        )),
        "browser_session_platforms": [
            platform for platform, path in (
                ("58同城", session_file),
                ("安居客", anjuke_session_file),
                ("房天下", fang_session_file),
            ) if path.exists()
        ],
        "listing_count": len(compact),
        "listings": compact,
        "detail_urls": [item["detail_url"] for item in compact if item.get("detail_url")],
        "message": "候选来自公开列表页；列表显示不等于当前可租，详情和联系方式需用户在原平台核验。",
    }, ensure_ascii=False)

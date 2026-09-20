"""房源条件解析、硬过滤和可解释排序。

模型负责理解用户意图，确定性代码负责判断一个候选是否满足已经明确的
硬条件。字段缺失时保留候选并标记为 ``unknown``，避免把“没有证据”误当
成“不满足”。
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}
_MISSING_TEXT = {
    "",
    "未说明",
    "未知",
    "平台列表未提供小区",
    "平台列表未提供具体地址",
    "平台列表未提供",
}


@dataclass(frozen=True)
class SearchCriteria:
    """从用户可见请求中提取的、可用于确定性判断的条件。"""

    raw_text: str = ""
    budget_min: float | None = None
    budget_max: float | None = None
    layout: str | None = None  # whole / shared
    room_count: int | None = None
    preferred_layout: str | None = None  # whole / shared
    preferred_room_count: int | None = None
    area_min_sqm: float | None = None
    area_max_sqm: float | None = None
    commute_max_minutes: float | None = None
    radius_km: float | None = None
    commute_mode: str = "transit"

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if key != "raw_text" and value is not None}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _chinese_number(value: str) -> float | None:
    """解析常见的一到两位中文数量词，例如“一”“十二”“二十五”。"""

    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return _number(value)
    if not all(char in _CHINESE_DIGITS or char in _CHINESE_UNITS for char in value):
        return None
    total = 0
    current = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
        else:
            unit = _CHINESE_UNITS[char]
            if current == 0:
                current = 1
            total += current * unit
            current = 0
    total += current
    return float(total) if total else None


def _first_number(pattern: str, text: str, flags: int = re.IGNORECASE) -> float | None:
    match = re.search(pattern, text, flags)
    return _number(match.group(1)) if match else None


def _parse_budget(text: str) -> tuple[float | None, float | None]:
    if re.search(r"预算\s*(?:不限|不设限)|租金\s*(?:不限|不设限)", text):
        return None, None

    budget_label = r"(?:预算|租金|月租|房租)"
    upper_bound = r"(?:不超过|不高于|以内|上限|至多|最多|小于等于|低于|少于|≤|<=)"
    maximum = _first_number(
        rf"{budget_label}\s*{upper_bound}\s*([0-9]+(?:\.\d+)?)\s*(?:元|块)?",
        text,
    )
    if maximum is None:
        # 无预算标签时只接受带“元/块”的金额，避免把通勤分钟数、面积或距离
        # 的上限误识别成月租上限。
        maximum = _first_number(
            r"([0-9]+(?:\.\d+)?)\s*(?:元|块)\s*(?:以内|以下|上限|封顶|不超过|不高于|至多|最多|小于等于|低于|少于)",
            text,
        )
    if maximum is None:
        maximum = _first_number(
            rf"{budget_label}\s*[:：]?\s*([0-9]+(?:\.\d+)?)\s*(?:元|块)?",
            text,
        )
    range_match = re.search(
        r"([0-9]+(?:\.\d+)?)\s*(?:-|~|～|到|至)\s*([0-9]+(?:\.\d+)?)\s*(?:元|块)",
        text,
    )
    if range_match:
        low, high = (_number(item) for item in range_match.groups())
        return low, high
    return None, maximum


def _parse_room_count(text: str) -> int | None:
    match = re.search(r"([一二两三四五六七八九十\d]+)\s*(?:室|居|房)", text)
    if match:
        value = _chinese_number(match.group(1))
        return int(value) if value is not None and value > 0 else None
    if re.search(r"(?:单间|一居|一室)", text):
        return 1
    return None


def _parse_area(text: str) -> tuple[float | None, float | None]:
    range_match = re.search(
        r"([0-9]+(?:\.\d+)?)\s*(?:到|至|-|~|～)\s*([0-9]+(?:\.\d+)?)\s*(?:平|平方米|㎡)",
        text,
    )
    if range_match:
        low, high = (_number(item) for item in range_match.groups())
        return low, high
    minimum = _first_number(
        r"(?:面积|建筑面积)?\s*(?:不小于|不少于|至少|大于等于|≥|>=)\s*([0-9]+(?:\.\d+)?)\s*(?:平|平方米|㎡)",
        text,
    )
    maximum = _first_number(
        r"(?:面积|建筑面积)?\s*(?:不超过|以内|至多|小于等于|≤|<=)\s*([0-9]+(?:\.\d+)?)\s*(?:平|平方米|㎡)",
        text,
    )
    return minimum, maximum


def _parse_commute(text: str) -> float | None:
    return _first_number(
        r"(?:通勤|路程|车程|出行|公交|地铁|驾车|自驾|步行|骑行)[^。；;，,\n]{0,24}?(?:不超过|不高于|以内|至多|最多|≤|<=)?\s*([0-9]+(?:\.\d+)?)\s*(?:分钟|min|分)",
        text,
    )


def _parse_radius(text: str) -> float | None:
    return _first_number(
        r"(?:半径|距离目标|距离|周边|附近)[^。；;，,\n]{0,18}?([0-9]+(?:\.\d+)?)\s*(?:公里|千米|km|KM)",
        text,
    )


def _preference_mentions(text: str, pattern: str) -> bool:
    return bool(re.search(
        rf"(?:优先|尽量|最好)[^，。；;\n]{{0,18}}(?:{pattern})|(?:{pattern})[^，。；;\n]{{0,10}}(?:优先|更好)",
        text,
    ))


def _layout_is_relaxed(text: str) -> bool:
    return bool(re.search(
        r"(?:不要求|不限|不限定)[^，。；;\n]{0,8}(?:整租|合租)"
        r"|(?:整租|合租)[^，。；;\n]{0,8}(?:不要求|不限|不限定|都可以|均可)"
        r"|(?:整租|合租)[^，。；;\n]{0,8}(?:整租|合租)[^，。；;\n]{0,8}(?:都可以|均可)",
        text,
    ))


def _room_count_is_relaxed(text: str) -> bool:
    return bool(re.search(
        r"(?:户型|几室|房间数)[^，。；;\n]{0,8}(?:不限|不要求|都可以|均可)"
        r"|(?:不限|不要求)[^，。；;\n]{0,8}(?:户型|几室|房间数)",
        text,
    ))


def parse_search_criteria(text: str) -> SearchCriteria:
    """从自然语言提取有限的硬条件；无法确认的字段保持 ``None``。"""

    raw_text = _text(text)
    budget_min, budget_max = _parse_budget(raw_text)
    layout: str | None = None
    has_whole = bool(re.search(r"整租|整套|独立一居|单独租", raw_text))
    has_shared = bool(re.search(r"合租|拼租|次卧|主卧|室友", raw_text))
    preferred_layout: str | None = None
    if has_whole and _preference_mentions(raw_text, r"整租|整套|独立一居|单独租"):
        preferred_layout = "whole"
    elif has_shared and _preference_mentions(raw_text, r"合租|拼租|次卧|主卧|室友"):
        preferred_layout = "shared"
    if not _layout_is_relaxed(raw_text) and preferred_layout is None and has_whole != has_shared:
        layout = "whole" if has_whole else "shared"
    parsed_room_count = _parse_room_count(raw_text)
    preferred_room_count = parsed_room_count if _preference_mentions(
        raw_text, r"[一二两三四五六七八九十\d]+\s*(?:室|居|房)|单间|一居|一室"
    ) else None
    room_count = None if preferred_room_count is not None or _room_count_is_relaxed(raw_text) else parsed_room_count
    area_min, area_max = _parse_area(raw_text)
    if re.search(r"(?:公交|地铁|公共交通)", raw_text):
        commute_mode = "transit"
    elif re.search(r"(?:驾车|开车|开车|自驾)", raw_text):
        commute_mode = "driving"
    elif re.search(r"(?:步行|走路)", raw_text):
        commute_mode = "walking"
    elif re.search(r"(?:骑行|自行车|电动车)", raw_text):
        commute_mode = "riding"
    else:
        commute_mode = "transit"
    return SearchCriteria(
        raw_text=raw_text,
        budget_min=budget_min,
        budget_max=budget_max,
        layout=layout,
        room_count=room_count,
        preferred_layout=preferred_layout,
        preferred_room_count=preferred_room_count,
        area_min_sqm=area_min,
        area_max_sqm=area_max,
        commute_max_minutes=_parse_commute(raw_text),
        radius_km=_parse_radius(raw_text),
        commute_mode=commute_mode,
    )


def update_search_criteria(previous: SearchCriteria | None, text: str) -> SearchCriteria:
    """Apply one conversational preference update without dropping earlier constraints."""

    previous = previous or SearchCriteria()
    raw_text = _text(text)
    parsed = parse_search_criteria(raw_text)
    budget_relaxed = bool(re.search(r"预算\s*(?:不限|不设限)|租金\s*(?:不限|不设限)", raw_text))
    layout_relaxed = _layout_is_relaxed(raw_text)
    room_relaxed = _room_count_is_relaxed(raw_text) or (
        layout_relaxed and bool(re.search(r"都可以|均可", raw_text))
    )
    area_relaxed = bool(re.search(r"面积[^，。；;\n]{0,8}(?:不限|不要求)|(?:不限|不要求)[^，。；;\n]{0,8}面积", raw_text))
    commute_relaxed = bool(re.search(r"通勤[^，。；;\n]{0,8}(?:不限|不要求)|(?:不限|不要求)[^，。；;\n]{0,8}通勤", raw_text))
    radius_relaxed = bool(re.search(r"距离[^，。；;\n]{0,8}(?:不限|不要求)|(?:不限|不要求)[^，。；;\n]{0,8}距离", raw_text))
    commute_mode_mentioned = bool(re.search(r"公交|地铁|公共交通|驾车|开车|自驾|步行|走路|骑行|自行车|电动车", raw_text))

    return SearchCriteria(
        raw_text=raw_text,
        budget_min=None if budget_relaxed else (parsed.budget_min if parsed.budget_min is not None else previous.budget_min),
        budget_max=None if budget_relaxed else (parsed.budget_max if parsed.budget_max is not None else previous.budget_max),
        layout=None if layout_relaxed else (parsed.layout if parsed.layout is not None else previous.layout),
        room_count=None if room_relaxed else (parsed.room_count if parsed.room_count is not None else previous.room_count),
        preferred_layout=None if layout_relaxed else (
            parsed.preferred_layout if parsed.preferred_layout is not None else previous.preferred_layout
        ),
        preferred_room_count=None if room_relaxed else (
            parsed.preferred_room_count
            if parsed.preferred_room_count is not None else previous.preferred_room_count
        ),
        area_min_sqm=None if area_relaxed else (
            parsed.area_min_sqm if parsed.area_min_sqm is not None else previous.area_min_sqm
        ),
        area_max_sqm=None if area_relaxed else (
            parsed.area_max_sqm if parsed.area_max_sqm is not None else previous.area_max_sqm
        ),
        commute_max_minutes=None if commute_relaxed else (
            parsed.commute_max_minutes
            if parsed.commute_max_minutes is not None else previous.commute_max_minutes
        ),
        radius_km=None if radius_relaxed else (
            parsed.radius_km if parsed.radius_km is not None else previous.radius_km
        ),
        commute_mode=parsed.commute_mode if commute_mode_mentioned else previous.commute_mode,
    )


def _field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


def _meaningful(value: Any) -> str:
    text = _text(value)
    return "" if text in _MISSING_TEXT else text


def _listing_room_count(item: dict[str, Any]) -> int | None:
    value = _field(item, "room", "layout", "room_type")
    match = re.search(r"([一二两三四五六七八九十\d]+)\s*(?:室|居|房)", _text(value))
    if match:
        parsed = _chinese_number(match.group(1))
        return int(parsed) if parsed is not None and parsed > 0 else None
    return None


def _listing_layout(item: dict[str, Any]) -> str | None:
    explicit = " ".join(
        _text(_field(item, key))
        for key in ("listing_type", "listingType", "layout", "room", "title")
    )
    tags = _field(item, "tags")
    if isinstance(tags, list):
        explicit += " " + " ".join(_text(tag) for tag in tags)
    elif isinstance(tags, str):
        explicit += " " + tags
    has_whole = bool(re.search(r"整租|整套", explicit))
    has_shared = bool(re.search(r"合租|拼租|次卧|主卧|室友", explicit))
    if has_whole != has_shared:
        return "whole" if has_whole else "shared"
    return None


def _status_reason(status: str, value: Any, label: str) -> str:
    if status == "unknown":
        return f"{label}未说明"
    return str(value)


def evaluate_listing(item: dict[str, Any], criteria: SearchCriteria) -> dict[str, Any]:
    """返回单条候选的硬条件结论和人类可读原因。"""

    violations: list[str] = []
    unknown: list[str] = []
    passed: list[str] = []

    rent = _number(_field(item, "rent", "monthly_rent_cny", "price"))
    if criteria.budget_min is not None or criteria.budget_max is not None:
        if rent is None:
            unknown.append("租金")
        else:
            if criteria.budget_min is not None and rent < criteria.budget_min:
                violations.append(f"租金 {rent:g} 元低于预算下限 {criteria.budget_min:g} 元")
            elif criteria.budget_max is not None and rent > criteria.budget_max:
                violations.append(f"租金 {rent:g} 元超过预算上限 {criteria.budget_max:g} 元")
            else:
                passed.append("租金符合预算")

    if criteria.layout:
        layout = _listing_layout(item)
        if layout is None:
            unknown.append("整租/合租类型")
        elif layout != criteria.layout:
            expected = "整租" if criteria.layout == "whole" else "合租"
            actual = "整租" if layout == "whole" else "合租"
            violations.append(f"房源为{actual}，不符合{expected}条件")
        else:
            passed.append("整租/合租符合条件")

    if criteria.room_count is not None:
        room_count = _listing_room_count(item)
        if room_count is None:
            unknown.append("户型")
        elif room_count != criteria.room_count:
            violations.append(f"户型为 {room_count} 室，不是 {criteria.room_count} 室")
        else:
            passed.append("户型符合条件")

    if criteria.area_min_sqm is not None or criteria.area_max_sqm is not None:
        area = _number(_field(item, "area", "area_sqm"))
        if area is None:
            unknown.append("面积")
        elif criteria.area_min_sqm is not None and area < criteria.area_min_sqm:
            violations.append(f"面积 {area:g}㎡ 小于下限 {criteria.area_min_sqm:g}㎡")
        elif criteria.area_max_sqm is not None and area > criteria.area_max_sqm:
            violations.append(f"面积 {area:g}㎡ 超过上限 {criteria.area_max_sqm:g}㎡")
        else:
            passed.append("面积符合条件")

    distance_meters = _number(_field(item, "distance_meters", "distanceMeters"))
    if distance_meters is None:
        distance_km = _number(_field(item, "distance", "distance_km"))
        if distance_km is not None:
            distance_meters = distance_km * 1000
    if criteria.radius_km is not None:
        if distance_meters is None:
            unknown.append("到目标地点的距离")
        elif distance_meters > criteria.radius_km * 1000:
            violations.append(f"直线距离 {distance_meters / 1000:.1f} km 超过范围 {criteria.radius_km:g} km")
        else:
            passed.append("直线距离在范围内")

    commute_seconds = _number(_field(item, "commute_duration_seconds", "commuteDurationSeconds"))
    if commute_seconds is None:
        commute_minutes = _number(_field(item, "commute_value", "commuteValue"))
        if commute_minutes is not None:
            commute_seconds = commute_minutes * 60
    if criteria.commute_max_minutes is not None:
        if commute_seconds is None:
            unknown.append("通勤时间")
        elif commute_seconds > criteria.commute_max_minutes * 60:
            violations.append(
                f"通勤约 {commute_seconds / 60:.0f} 分钟，超过上限 {criteria.commute_max_minutes:g} 分钟"
            )
        else:
            passed.append("通勤时间在范围内")

    if violations:
        filter_status = "excluded"
        hard_filter_pass: bool | None = False
    elif unknown:
        filter_status = "unknown"
        hard_filter_pass = None
    else:
        filter_status = "passed"
        hard_filter_pass = True

    reasons = [*violations, *(_status_reason("unknown", "", value) for value in unknown)]
    if not reasons:
        reasons = passed or ["没有可用于过滤的明确硬条件"]
    return {
        "filter_status": filter_status,
        "hard_filter_pass": hard_filter_pass,
        "filter_reasons": reasons[:8],
        "_filter_passed_reasons": passed[:8],
    }


def _ranking_score(item: dict[str, Any], criteria: SearchCriteria, result: dict[str, Any]) -> tuple[float, list[str]]:
    """计算只用于排序的透明分数；硬过滤结论优先于此分数。"""

    score = {"passed": 100.0, "unknown": 62.0, "excluded": 0.0}[result["filter_status"]]
    reasons: list[str] = []
    rent = _number(_field(item, "rent", "monthly_rent_cny", "price"))
    if rent is not None:
        if criteria.budget_max and criteria.budget_max > 0:
            score += max(0.0, 18.0 * (1.0 - rent / criteria.budget_max))
            reasons.append("预算内租金越低排序越靠前")
        else:
            score += max(0.0, 8.0 * (1.0 - min(rent, 10000) / 10000))
    distance = _number(_field(item, "distance_meters", "distanceMeters"))
    if distance is None:
        km = _number(_field(item, "distance", "distance_km"))
        distance = km * 1000 if km is not None else None
    if distance is not None:
        score += max(0.0, 15.0 * (1.0 - min(distance, 50000) / 50000))
        reasons.append("已计算距离，距离目标越近排序越靠前")
    commute = _number(_field(item, "commute_duration_seconds", "commuteDurationSeconds"))
    if commute is not None:
        score += max(0.0, 12.0 * (1.0 - min(commute, 7200) / 7200))
        reasons.append("已计算通勤，通勤时间越短排序越靠前")
    if criteria.preferred_layout and _listing_layout(item) == criteria.preferred_layout:
        score += 8.0
        reasons.append("符合整租/合租偏好")
    if criteria.preferred_room_count is not None and _listing_room_count(item) == criteria.preferred_room_count:
        score += 5.0
        reasons.append("符合户型偏好")
    if result["filter_status"] == "excluded":
        reasons.insert(0, "明确违反硬条件，排在符合条件候选之后")
    elif result["filter_status"] == "unknown":
        reasons.insert(0, "条件字段不完整，待核验后再判断")
    else:
        reasons.insert(0, "满足目前已知硬条件")
    return round(score, 2), reasons[:6]


def rank_listings(listings: Iterable[dict[str, Any]], criteria: SearchCriteria) -> list[dict[str, Any]]:
    """给候选补充过滤/排序字段并稳定排序，保留 excluded 和 unknown 候选。"""

    ranked: list[tuple[int, dict[str, Any]]] = []
    for index, raw in enumerate(listings):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        result = evaluate_listing(item, criteria)
        score, ranking_reasons = _ranking_score(item, criteria, result)
        item.update({key: value for key, value in result.items() if not key.startswith("_")})
        item["ranking_score"] = score
        item["ranking_reasons"] = ranking_reasons
        item["ranking_explanation"] = "；".join(ranking_reasons)
        ranked.append((index, item))

    status_rank = {"passed": 0, "unknown": 1, "excluded": 2}

    def sort_key(pair: tuple[int, dict[str, Any]]) -> tuple[Any, ...]:
        index, item = pair
        distance = _number(_field(item, "distance_meters", "distanceMeters"))
        rent = _number(_field(item, "rent", "monthly_rent_cny", "price"))
        return (
            status_rank.get(item.get("filter_status"), 3),
            -float(item.get("ranking_score") or 0),
            distance if distance is not None else float("inf"),
            rent if rent is not None else float("inf"),
            _text(item.get("id") or item.get("listing_id")),
            index,
        )

    ranked.sort(key=sort_key)
    return [item for _, item in ranked]


# 这些别名让调用方名称与领域文档中的说法保持兼容。
parse_rental_criteria = parse_search_criteria
evaluate_hard_filters = evaluate_listing


__all__ = [
    "SearchCriteria",
    "evaluate_hard_filters",
    "evaluate_listing",
    "parse_rental_criteria",
    "parse_search_criteria",
    "rank_listings",
    "update_search_criteria",
]

"""租房 Agent 使用的有界目标地点解析工具。

Agent 负责理解用户措辞；本模块通过简洁的服务方边界返回规范化地点候选。
公开工具契约与具体服务方无关：夹具测试和真实高德 Web Service 返回相同的紧凑数据结构。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests
from langchain_core.tools import tool

from .runtime_mode import rental_mode


AGENT_CORE_ROOT = Path(__file__).resolve().parent
FIXTURE_PATH = AGENT_CORE_ROOT / "fixtures" / "places.json"
MAX_CANDIDATES = 10
AMAP_INPUT_TIPS_URL = "https://restapi.amap.com/v3/assistant/inputtips"
DEFAULT_AMAP_TIMEOUT_SECONDS = 5.0
MAX_AMAP_TIMEOUT_SECONDS = 15.0
SAME_ENTITY_MAX_DISTANCE_KM = 3.0
ACCESS_POINT_SUFFIX_RE = re.compile(
    r"(?:"
    r"(?:东|西|南|北|东北|西北|东南|西南|正|侧)?"
    r"(?:[一二三四五六七八九十\d]+号?)?"
    r"(?:大门|校门|门|入口|出口|出入口)"
    r")$"
)

AMAP_CONFIGURATION_INFOCODES = frozenset(
    {
        "10001",  # Key 无效或已过期
        "10002",  # 当前 Key 未开通该服务
        "10005",  # IP 白名单不匹配
        "10006",  # 域名限制不匹配
        "10007",  # 签名不匹配
        "10008",  # 安全密钥校验不匹配
        "10009",  # Key 类型不匹配
        "10012",  # 权限不足
        "10013",  # Key 已被删除
        "10041",  # 接口权限已过期
    }
)
AMAP_QUOTA_INFOCODES = frozenset(
    {
        "10003",
        "10004",
        "10010",
        "10014",
        "10019",
        "10020",
        "10021",
        "10029",
        "10044",
        "10045",
        "40000",
    }
)
AMAP_TIMEOUT_INFOCODES = frozenset({"10015"})
AMAP_CITY_HINT_FALLBACK_INFOCODES = frozenset({"20000"})


@dataclass(frozen=True)
class PlaceCandidate:
    """提供方无关的地点结构，供 Agent 和界面使用。"""

    candidate_ref: str
    name: str
    formatted_address: str
    city: str
    district: str
    adcode: str
    lng: float
    lat: float
    confidence: str
    provider: str
    data_quality: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_ref": self.candidate_ref,
            "name": self.name,
            "formatted_address": self.formatted_address,
            "city": self.city,
            "district": self.district,
            "adcode": self.adcode,
            "lng": self.lng,
            "lat": self.lat,
            "confidence": self.confidence,
            "provider": self.provider,
            "data_quality": self.data_quality,
        }


class PlaceProvider(Protocol):
    """目标地点工具使用的最小提供方边界。"""

    name: str

    def suggest(self, query: str, limit: int, city_hint: str = "") -> list[PlaceCandidate]:
        """返回候选地点，不根据查询内容臆测城市。"""


class PlaceProviderError(RuntimeError):
    """可安全转换为工具响应封装的提供方错误。"""

    def __init__(self, status: str, public_message: str) -> None:
        super().__init__(public_message)
        self.status = status
        self.public_message = public_message


@dataclass(frozen=True)
class ProviderSelection:
    """已解析的提供方及可安全放入响应封装的元数据。"""

    provider: PlaceProvider | None
    name: str
    mode: str
    unavailable_status: str = ""
    unavailable_message: str = ""


def _normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[\s,，。！？!?、；;：:/\\()（）\[\]【】'\"“”‘’]", "", value)
    return value


def _query_text(value: str) -> str:
    """去掉通用搜索措辞，同时保留地点名称。"""

    normalized = _normalize_text(value)
    for phrase in (
        "请帮我找",
        "帮我找",
        "我想找",
        "请帮我",
        "帮我",
        "搜索",
        "查找",
        "找房",
        "租房",
        "房源",
        "房子",
        "目标地点",
        "工作地点",
        "附近",
        "周边",
        "周围",
        "一带",
    ):
        normalized = normalized.replace(_normalize_text(phrase), "")
    normalized = re.sub(r"^(?:找|定位)+", "", normalized)
    normalized = normalized.rstrip("的")
    if normalized in {"公司", "学校", "地标", "地点"}:
        return ""
    return normalized


def _canonical_city(value: str) -> str:
    """把用户写的城市说法归一化；末尾的“市”会被去掉，简称保留原样。"""

    normalized = _normalize_text(value)
    aliases = (
        ("临江", "临江"),
        ("南京", "南京"),
        ("上海", "上海"),
        ("北京", "北京"),
        ("广州", "广州"),
        ("深圳", "深圳"),
    )
    for token, city in aliases:
        if token in normalized:
            return city
    if normalized.endswith("市"):
        return normalized[:-1]
    return normalized


def _candidate_matches_city(candidate: PlaceCandidate, requested_city: str) -> bool:
    if _canonical_city(candidate.city) == requested_city:
        return True
    # 县级市在高德返回里可能挂在地级市下面：
    # 只要用户给的城市提示确实出现在 Provider 返回的区县或地址里就接受它，
    # 但候选自身的城市字段保持归一化后的值，供后续平台路由使用。
    if len(requested_city) < 2:
        return False
    admin_text = _normalize_text(f"{candidate.district}{candidate.formatted_address}")
    return requested_city in admin_text


def _canonical_district(value: str) -> str:
    normalized = _normalize_text(value)
    if normalized.endswith(("区", "县", "市")):
        return normalized[:-1]
    return normalized


def _entity_name(value: str) -> str:
    """合并出入口名称变体，同时保留校区和分支差异。"""

    normalized = re.sub(r"[-—·]", "", _normalize_text(value))
    while normalized:
        base = ACCESS_POINT_SUFFIX_RE.sub("", normalized)
        if base == normalized:
            break
        normalized = base
    return normalized


def _is_access_point_name(value: str) -> bool:
    normalized = re.sub(r"[-—·]", "", _normalize_text(value))
    return bool(ACCESS_POINT_SUFFIX_RE.search(normalized))


def _distance_km(left: PlaceCandidate, right: PlaceCandidate) -> float:
    lat1, lon1 = math.radians(left.lat), math.radians(left.lng)
    lat2, lon2 = math.radians(right.lat), math.radians(right.lng)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value)))


def _same_admin_area(left: PlaceCandidate, right: PlaceCandidate) -> bool:
    if _canonical_city(left.city) != _canonical_city(right.city):
        return False

    left_adcode = left.adcode.strip()
    right_adcode = right.adcode.strip()
    if left_adcode and right_adcode:
        return left_adcode == right_adcode

    return _canonical_district(left.district) == _canonical_district(right.district)


def _same_substantive_place(candidates: list[PlaceCandidate]) -> bool:
    """仅当所有候选都是同一 POI 的邻近变体时才返回真。"""

    if len(candidates) <= 1:
        return True

    entity_name = _entity_name(candidates[0].name)
    if not entity_name:
        return False

    access_point_flags = [_is_access_point_name(candidate.name) for candidate in candidates]
    # 如果提供方没有恰好返回一个主体 POI，同名出入口可能属于附近的不同实体，
    # 不能安全合并。
    if access_point_flags.count(False) != 1:
        return False

    if any(_entity_name(candidate.name) != entity_name for candidate in candidates[1:]):
        return False

    return all(
        _same_admin_area(left, right)
        and _distance_km(left, right) <= SAME_ENTITY_MAX_DISTANCE_KM
        for index, left in enumerate(candidates)
        for right in candidates[index + 1 :]
    )


def _canonical_candidate_first(
    candidates: list[PlaceCandidate], query: str
) -> list[PlaceCandidate]:
    """把用户精确匹配项或实体中心项放到第一个位置。"""

    normalized_query = re.sub(r"[-—·]", "", _normalize_text(query))

    def rank(candidate: PlaceCandidate) -> tuple[int, int, int, int]:
        normalized_name = re.sub(r"[-—·]", "", _normalize_text(candidate.name))
        entity_name = _entity_name(candidate.name)
        return (
            int(normalized_name == normalized_query),
            int(entity_name == normalized_query),
            int(normalized_name == entity_name),
            -len(normalized_name),
        )

    if not candidates:
        return []
    selected_index = max(range(len(candidates)), key=lambda index: rank(candidates[index]))
    if selected_index == 0:
        return candidates
    return [candidates[selected_index], *candidates[:selected_index], *candidates[selected_index + 1 :]]


def _opaque_ref(provider: str, provider_id: str) -> str:
    digest = hashlib.sha256(f"{provider}:{provider_id}".encode("utf-8")).hexdigest()
    return f"pc_{digest[:20]}"


class FakePlaceProvider:
    """用于 M1 测试的确定性、明确不联网的地点提供方。"""

    name = "fake"

    def __init__(self, fixture_path: Path = FIXTURE_PATH) -> None:
        self.fixture_path = fixture_path

    def _load(self) -> list[dict[str, Any]]:
        return json.loads(self.fixture_path.read_text(encoding="utf-8"))

    def suggest(self, query: str, limit: int, city_hint: str = "") -> list[PlaceCandidate]:
        needle = _query_text(query)
        if not needle:
            return []

        matches: list[PlaceCandidate] = []
        for item in self._load():
            searchable = [
                str(item.get("name") or ""),
                *(str(alias) for alias in item.get("aliases", [])),
                str(item.get("formatted_address") or ""),
            ]
            normalized_terms = [_normalize_text(term) for term in searchable]
            if not any(needle in term or term in needle for term in normalized_terms if term):
                continue
            matches.append(
                PlaceCandidate(
                    # 保留现有演示会话使用的 M1 引用命名空间，同时隐藏提供方 ID。
                    candidate_ref=_opaque_ref("fake-place", str(item["id"])),
                    name=str(item["name"]),
                    formatted_address=str(item["formatted_address"]),
                    city=str(item["city"]),
                    district=str(item.get("district") or ""),
                    adcode=str(item.get("adcode") or ""),
                    lng=float(item["lng"]),
                    lat=float(item["lat"]),
                    confidence="fixture",
                    provider=self.name,
                    data_quality="fixture_only",
                )
            )
        return matches[:limit]


def _string_field(value: Any) -> str:
    """高德有时会用 ``[]`` 表示不可用的字符串字段。"""

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _parse_location(value: Any) -> tuple[float, float] | None:
    raw = _string_field(value)
    parts = raw.split(",")
    if len(parts) != 2:
        return None
    try:
        lng, lat = (float(part.strip()) for part in parts)
    except ValueError:
        return None
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    return lng, lat


def _city_from_admin_area(admin_area: str, city_hint: str = "") -> str:
    """从高德的省、市、区县组合字段中提取地级市。"""

    compact = _normalize_text(admin_area)
    for municipality in ("北京", "上海", "天津", "重庆"):
        if compact.startswith(municipality):
            return municipality

    tail = re.split(r"省|自治区", compact, maxsplit=1)[-1]
    match = re.match(r"(.+?市)", tail)
    if match:
        return _canonical_city(match.group(1))

    hinted_city = _canonical_city(city_hint)
    if hinted_city and hinted_city in compact:
        return hinted_city
    return ""


def _district_from_admin_area(admin_area: str, city: str) -> str:
    compact = _normalize_text(admin_area)
    if not compact:
        return ""
    if city:
        marker = f"{_canonical_city(city)}市"
        marker_index = compact.find(marker)
        if marker_index >= 0:
            remainder = compact[marker_index + len(marker):]
            if remainder:
                return remainder
    return admin_area.strip()


def _formatted_address(admin_area: str, address: str) -> str:
    if not address:
        return admin_area
    if not admin_area or admin_area in address:
        return address
    return f"{admin_area}{address}"


def _amap_timeout_seconds() -> float:
    raw = os.getenv("AMAP_REQUEST_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_AMAP_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError:
        return DEFAULT_AMAP_TIMEOUT_SECONDS
    return max(1.0, min(timeout, MAX_AMAP_TIMEOUT_SECONDS))


def _amap_use_env_proxy() -> bool:
    """默认绕过系统代理，避免本地代理接管高德请求时破坏 TLS。"""

    return os.getenv("AMAP_USE_ENV_PROXY", "false").strip().lower() in {"1", "true", "yes", "on"}


class AmapPlaceProvider:
    """高德输入提示 Web API 的小型固定端点适配器。"""

    name = "amap"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = DEFAULT_AMAP_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("AMap Web Service key is required")
        self._api_key = api_key.strip()
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), MAX_AMAP_TIMEOUT_SECONDS))
        if session is None:
            session = requests.Session()
            # 某些本地代理/全局节点会让 Python 的 TLS 握手异常；
            # HTTPS 证书校验仍保持开启，只默认不读取 HTTP(S)_PROXY。
            session.trust_env = _amap_use_env_proxy()
        self._session = session

    def _request_payload(self, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._session.get(
                AMAP_INPUT_TIPS_URL,
                params=params,
                timeout=self._timeout_seconds,
            )
        except requests.Timeout as exc:
            raise PlaceProviderError("timeout", "高德地点服务响应超时，请稍后再试。") from exc
        except requests.RequestException as exc:
            raise PlaceProviderError("provider_error", "高德地点服务暂时不可用，请稍后再试。") from exc

        if response.status_code != 200:
            raise PlaceProviderError("provider_error", "高德地点服务返回异常，请稍后再试。")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise PlaceProviderError("provider_error", "高德地点服务返回了无法解析的数据。") from exc
        if not isinstance(payload, dict):
            raise PlaceProviderError("provider_error", "高德地点服务返回了无法解析的数据。")
        return payload

    def suggest(self, query: str, limit: int, city_hint: str = "") -> list[PlaceCandidate]:
        params = {
            "key": self._api_key,
            "keywords": query,
            "datatype": "poi",
            "output": "JSON",
        }
        if city_hint:
            params["city"] = city_hint

        payload = self._request_payload(params)
        infocode = _string_field(payload.get("infocode"))
        if (
            str(payload.get("status", "")) != "1"
            and city_hint
            and infocode in AMAP_CITY_HINT_FALLBACK_INFOCODES
        ):
            # 该 API 不支持把县级市传入 ``city``。先去掉城市限制重试一次，
            # 再使用提供方返回的行政区字段完成本地城市/区县确认。
            fallback_params = dict(params)
            fallback_params.pop("city", None)
            payload = self._request_payload(fallback_params)

        if str(payload.get("status", "")) != "1":
            infocode = _string_field(payload.get("infocode"))
            if infocode in AMAP_CONFIGURATION_INFOCODES:
                raise PlaceProviderError(
                    "not_configured",
                    "高德 Web 服务 Key 无效、类型不匹配或未开通当前服务，请检查后端配置。",
                )
            if infocode in AMAP_QUOTA_INFOCODES:
                raise PlaceProviderError("quota_exceeded", "高德地点服务当前已达到调用限额，请稍后再试。")
            if infocode in AMAP_TIMEOUT_INFOCODES:
                raise PlaceProviderError("timeout", "高德地点服务响应超时，请稍后再试。")
            raise PlaceProviderError("provider_error", "高德地点服务未能完成本次地点解析。")

        raw_tips = payload.get("tips")
        if not isinstance(raw_tips, list):
            raise PlaceProviderError("provider_error", "高德地点服务返回了无法解析的数据。")

        candidates: list[PlaceCandidate] = []
        for raw_tip in raw_tips:
            if not isinstance(raw_tip, dict):
                continue
            name = _string_field(raw_tip.get("name"))
            location = _parse_location(raw_tip.get("location"))
            if not name or location is None:
                continue
            lng, lat = location
            admin_area = _string_field(raw_tip.get("district"))
            city = _city_from_admin_area(admin_area, city_hint)
            address = _string_field(raw_tip.get("address"))
            provider_id = _string_field(raw_tip.get("id")) or f"{name}|{location[0]},{location[1]}"
            candidates.append(
                PlaceCandidate(
                    candidate_ref=_opaque_ref(self.name, provider_id),
                    name=name,
                    formatted_address=_formatted_address(admin_area, address),
                    city=city,
                    district=_district_from_admin_area(admin_area, city),
                    adcode=_string_field(raw_tip.get("adcode")),
                    lng=lng,
                    lat=lat,
                    confidence="input_tip",
                    provider=self.name,
                    data_quality="provider_result",
                )
            )
            if len(candidates) >= limit:
                break
        return candidates


def _base_result(
    *,
    query: str,
    status: str,
    message: str,
    candidates: list[PlaceCandidate] | None = None,
    mode: str = "not_requested",
    provider: str = "none",
    city_hint: str = "",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "provider": provider,
        "status": status,
        "query": query.strip(),
        "city_hint": city_hint.strip(),
        "candidates": [candidate.as_dict() for candidate in (candidates or [])],
        "requires_user_confirmation": status in {
            "needs_confirmation", "candidates_ready", "needs_city_confirmation"
        },
        "message": message,
    }


def _provider_for_current_mode() -> ProviderSelection:
    """独立于房源平台联网模式选择地图访问方式。"""

    provider_name = os.getenv("MAP_PROVIDER", "").strip().lower()
    if not provider_name:
        provider_name = "amap" if rental_mode() == "live" else "fake"

    if provider_name in {"fake", "fixture", "offline"}:
        return ProviderSelection(FakePlaceProvider(), "fake", "offline_fixture")
    if provider_name == "amap":
        api_key = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
        if not api_key:
            return ProviderSelection(
                None,
                "amap",
                "live",
                unavailable_status="not_configured",
                unavailable_message=(
                    "尚未配置后端高德 Web 服务 Key，地点解析暂不可用；"
                    "聊天、房源搜索和来源链接仍可继续使用。"
                ),
            )
        return ProviderSelection(
            AmapPlaceProvider(api_key, timeout_seconds=_amap_timeout_seconds()),
            "amap",
            "live",
        )
    return ProviderSelection(
        None,
        "unsupported",
        "disabled",
        unavailable_status="not_configured",
        unavailable_message="地图 Provider 配置无效，请使用 fake 或 amap。",
    )


@tool
def resolve_target_place(query: str, city_hint: str = "", limit: int = 5) -> str:
    """把公司、学校、地标或地址解析为地点候选。

    ``query`` 是用户输入的地点说法。``city_hint`` 只能包含用户明确说出的城市，
    不得从学校或品牌名称臆测城市。结果是一个 JSON 封装。唯一地点，或同一地点的
    多个邻近出入口变体，会自动解析；只有实质不同的候选才需要用户确认。
    """

    query = (query or "").strip()
    city_hint = (city_hint or "").strip()
    try:
        requested_limit = max(1, min(int(limit), MAX_CANDIDATES))
    except (TypeError, ValueError):
        requested_limit = 5

    if not query:
        return json.dumps(
            _base_result(
                query=query,
                city_hint=city_hint,
                status="invalid_input",
                message="请提供公司、学校、地标或地址名称。",
            ),
            ensure_ascii=False,
        )

    provider_query = _query_text(query)
    if not provider_query:
        return json.dumps(
            _base_result(
                query=query,
                city_hint=city_hint,
                status="invalid_input",
                message="请提供具体的公司、学校、地标或地址名称。",
            ),
            ensure_ascii=False,
        )

    selection = _provider_for_current_mode()
    provider = selection.provider
    if provider is None:
        result = _base_result(
            query=query,
            city_hint=city_hint,
            status=selection.unavailable_status,
            message=selection.unavailable_message,
            mode=selection.mode,
            provider=selection.name,
        )
        return json.dumps(result, ensure_ascii=False)

    try:
        # 分类必须看到完整的有界结果集。``limit`` 只控制展示数量，
        # 不能让 Agent 看不到地点歧义。
        candidates = provider.suggest(
            provider_query,
            MAX_CANDIDATES,
            city_hint=city_hint,
        )
    except PlaceProviderError as exc:
        result = _base_result(
            query=query,
            city_hint=city_hint,
            status=exc.status,
            message=exc.public_message,
            mode=selection.mode,
            provider=selection.name,
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        result = _base_result(
            query=query,
            city_hint=city_hint,
            status="provider_error",
            message="地点服务暂时不可用，请稍后再试。",
            mode=selection.mode,
            provider=selection.name,
        )
        return json.dumps(result, ensure_ascii=False)
    if not candidates:
        message = (
            "高德地点服务没有返回带有效坐标的匹配项，请补充城市或更完整的地点名称。"
            if selection.name == "amap"
            else "离线地点夹具中没有匹配项；可切换到高德地点 Provider 继续解析。"
        )
        result = _base_result(
            query=query,
            city_hint=city_hint,
            status="no_match",
            message=message,
            mode=selection.mode,
            provider=selection.name,
        )
        return json.dumps(result, ensure_ascii=False)

    if city_hint:
        requested_city = _canonical_city(city_hint)
        filtered_candidates = [
            candidate
            for candidate in candidates
            if _candidate_matches_city(candidate, requested_city)
        ]
    else:
        filtered_candidates = candidates

    if city_hint and not filtered_candidates:
        result = _base_result(
            query=query,
            city_hint=city_hint,
            status="needs_confirmation",
            candidates=candidates[: max(2, requested_limit)],
            message="地点候选所在城市与用户提供的城市不一致，请先确认，不要直接开始房源搜索。",
            mode=selection.mode,
            provider=selection.name,
        )
        return json.dumps(result, ensure_ascii=False)

    ranked_candidates = _canonical_candidate_first(filtered_candidates, provider_query)
    if _same_substantive_place(ranked_candidates):
        result = _base_result(
            query=query,
            city_hint=city_hint,
            status="resolved",
            candidates=ranked_candidates[:requested_limit],
            message="已自动采用明确的目标地点，可继续处理找房条件。",
            mode=selection.mode,
            provider=selection.name,
        )
    else:
        result = _base_result(
            query=query,
            city_hint=city_hint,
            status="needs_confirmation",
            candidates=ranked_candidates[: max(2, requested_limit)],
            message="找到多个不同地点，请确认具体目标后再继续房源搜索。",
            mode=selection.mode,
            provider=selection.name,
        )
    return json.dumps(result, ensure_ascii=False)


@tool
def confirm_target_place(candidate_ref: str) -> str:
    """确认当前地点候选中的一个标准化地点。

    Agent 应根据用户的自然语言选择，从最近一次 ``resolve_target_place`` 返回的
    candidates 中找到对应的 ``candidate_ref`` 后调用本工具。工具不重新搜索地点，
    运行时会把该引用绑定回当前会话候选并校验它是否有效。
    """

    reference = (candidate_ref or "").strip()
    if not reference or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reference):
        return json.dumps({
            "status": "invalid_selection",
            "candidate_ref": "",
            "message": "候选引用无效；请使用最近一次地点解析结果中的 candidate_ref。",
        }, ensure_ascii=False)
    return json.dumps({
        "status": "confirmed",
        "candidate_ref": reference,
        "message": "已提交地点候选确认，运行时将校验它是否属于当前候选集。",
    }, ensure_ascii=False)


__all__ = [
    "AmapPlaceProvider",
    "FakePlaceProvider",
    "PlaceCandidate",
    "PlaceProvider",
    "PlaceProviderError",
    "confirm_target_place",
    "resolve_target_place",
]

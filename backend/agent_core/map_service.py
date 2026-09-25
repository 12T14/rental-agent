"""目标地点和房源位置增强服务。

房源采集工具只负责拿到平台事实；本模块负责把明确地址转换为坐标，计算
直线距离，并对有限数量的候选请求路线。所有提供方都经过同一个边界，便于
离线测试、缓存和安全降级。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib.parse import urlparse

import requests

from .runtime_mode import rental_mode


MAP_POINTS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "map_points.json"
GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
ROUTE_URLS = {
    "driving": "https://restapi.amap.com/v3/direction/driving",
    "walking": "https://restapi.amap.com/v3/direction/walking",
    "transit": "https://restapi.amap.com/v3/direction/transit/integrated",
    "riding": "https://restapi.amap.com/v4/direction/bicycling",
}
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 15.0
DEFAULT_GEOCODE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_ROUTE_TTL_SECONDS = 15 * 60
DEFAULT_MAX_ROUTE_CANDIDATES = 5
PLACEHOLDER_LOCATIONS = {
    "未说明",
    "平台列表未提供",
    "平台列表未提供小区",
    "平台列表未提供具体地址",
    "地址未提供",
}
CITY_CODE_NAMES = {
    "bj": "北京", "sh": "上海", "gz": "广州", "sz": "深圳", "nj": "南京",
    "hz": "杭州", "cd": "成都", "wh": "武汉", "su": "苏州", "xa": "西安",
    "tj": "天津", "cq": "重庆", "cs": "长沙", "zz": "郑州", "dg": "东莞",
    "qd": "青岛", "hf": "合肥", "nb": "宁波", "km": "昆明", "sy": "沈阳",
    "dl": "大连", "fz": "福州", "xm": "厦门", "jn": "济南", "wx": "无锡",
    "nc": "南昌", "cz": "常州",
}
KNOWN_CITY_NAMES = frozenset(CITY_CODE_NAMES.values()) | {"示例市"}
AMAP_CONFIGURATION_INFOCODES = frozenset(
    {"10001", "10002", "10005", "10006", "10007", "10008", "10009", "10012", "10013", "10041"}
)
AMAP_QUOTA_INFOCODES = frozenset(
    {"10003", "10004", "10010", "10014", "10019", "10020", "10021", "10029", "10044", "10045", "40000"}
)
# 这些状态表示上一次地图增强明确没有得到可信的房源坐标。
# 如果本轮记录还带着旧坐标，也不能把旧坐标继续当成当前地址的证据。
UNTRUSTED_POINT_STATUSES = frozenset({
    "city_conflict", "geocode_failed", "not_configured", "provider_error",
    "timeout", "quota_exceeded", "missing_address",
})


def _text(value: Any, limit: int = 500) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _coordinate(value: Any, minimum: float, maximum: float) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and minimum <= number <= maximum else None


def _point_coordinates(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lng = _coordinate(value[0], -180, 180)
        lat = _coordinate(value[1], -90, 90)
    else:
        lng = _coordinate(value.get("lng") if isinstance(value, dict) else None, -180, 180)
        lat = _coordinate(value.get("lat") if isinstance(value, dict) else None, -90, 90)
    return (lng, lat) if lng is not None and lat is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _compact(value: str) -> str:
    return re.sub(r"[\s,，。；;、:：/\\()（）\[\]【】'\"“”‘’]", "", value.lower())


def _geocode_city_hint(value: Any) -> str:
    """把平台短城市代码转换成地图服务可理解的中文城市名。"""

    text = _text(value, 100)
    if re.fullmatch(r"[a-z]{2,4}", text.lower()):
        return CITY_CODE_NAMES.get(text.lower(), "")
    return text


def _city_from_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    code = host.split(".", 1)[0]
    return CITY_CODE_NAMES.get(code, "")


def _canonical_city(value: Any) -> str:
    text = _geocode_city_hint(value)
    compact = _compact(text)
    if not compact:
        return ""
    for city in sorted(KNOWN_CITY_NAMES, key=len, reverse=True):
        city_compact = _compact(city)
        if city_compact and city_compact in compact:
            return city_compact.removesuffix("市")
    return compact.removesuffix("市")


def _city_conflicts(expected_city: Any, point: "GeoPoint") -> bool:
    """只在提供方明确返回另一个城市时判冲突；缺失城市字段不误拒绝。"""

    expected = _canonical_city(expected_city)
    if not expected:
        return False
    point_city = _canonical_city(point.city)
    if point_city and point_city != expected:
        return True
    address_text = _compact(f"{point.formatted_address}{point.district}")
    if expected in address_text:
        return False
    observed_known = {
        _compact(city).removesuffix("市")
        for city in KNOWN_CITY_NAMES
        if _compact(city).removesuffix("市") in address_text
    }
    return bool(observed_known and expected not in observed_known)


def _haversine_meters(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, origin)
    lon2, lat2 = map(math.radians, destination)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371_008.8 * 2 * math.asin(min(1.0, math.sqrt(value)))


class MapProviderError(RuntimeError):
    """不会携带 Key、请求参数或提供方原始响应的地图错误。"""

    def __init__(self, status: str, public_message: str) -> None:
        super().__init__(public_message)
        self.status = status
        self.public_message = public_message


@dataclass(frozen=True)
class GeoPoint:
    point_id: str
    lng: float
    lat: float
    formatted_address: str = ""
    adcode: str = ""
    city: str = ""
    district: str = ""
    confidence: str = "provider"
    provider: str = ""
    status: str = "ok"
    name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "lng": self.lng,
            "lat": self.lat,
            "formatted_address": self.formatted_address,
            "adcode": self.adcode,
            "city": self.city,
            "district": self.district,
            "confidence": self.confidence,
            "provider": self.provider,
            "status": self.status,
            "name": self.name,
        }


@dataclass(frozen=True)
class DistanceResult:
    origin_id: str
    destination_id: str
    distance_meters: float | None
    duration_seconds: int | None
    mode: str
    calculated_at: str
    status: str = "ok"
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "origin_id": self.origin_id,
            "destination_id": self.destination_id,
            "distance_meters": self.distance_meters,
            "duration_seconds": self.duration_seconds,
            "mode": self.mode,
            "calculated_at": self.calculated_at,
            "status": self.status,
            "message": self.message,
        }


@dataclass(frozen=True)
class CommuteResult:
    origin_id: str
    destination_id: str
    mode: str
    distance_meters: float | None
    duration_seconds: int | None
    summary: str
    calculated_at: str
    status: str = "ok"
    message: str = ""
    steps: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "origin_id": self.origin_id,
            "destination_id": self.destination_id,
            "mode": self.mode,
            "distance_meters": self.distance_meters,
            "duration_seconds": self.duration_seconds,
            "summary": self.summary,
            "calculated_at": self.calculated_at,
            "status": self.status,
            "message": self.message,
            "steps": [dict(step) for step in self.steps],
        }


class MapProvider(Protocol):
    name: str

    def geocode(self, address: str, city: str = "") -> GeoPoint | None:
        ...

    def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        mode: str,
        city: str = "",
        cityd: str = "",
    ) -> CommuteResult:
        ...


class TTLCache:
    """小型进程内缓存；只保存已脱敏的结构化结果。"""

    def __init__(self, *, max_entries: int = 512) -> None:
        self.max_entries = max(1, max_entries)
        self._values: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._values.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= time.monotonic():
                self._values.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            if len(self._values) >= self.max_entries:
                oldest = min(self._values, key=lambda item: self._values[item][0])
                self._values.pop(oldest, None)
            self._values[key] = (time.monotonic() + max(0.0, ttl_seconds), value)


class RateLimiter:
    def __init__(self, minimum_interval_seconds: float = 0.0) -> None:
        self.minimum_interval_seconds = max(0.0, float(minimum_interval_seconds))
        self._last_at = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.minimum_interval_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            remaining = self.minimum_interval_seconds - (now - self._last_at)
            if remaining > 0:
                time.sleep(remaining)
            self._last_at = time.monotonic()


def _fixture_points(path: Path = MAP_POINTS_FIXTURE) -> list[GeoPoint]:
    if not path.is_file():
        return []
    try:
        raw_items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(raw_items, list):
        return []
    points: list[GeoPoint] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        coords = _point_coordinates(raw)
        name = _text(raw.get("name"), 200)
        if coords is None or not name:
            continue
        points.append(GeoPoint(
            point_id=_text(raw.get("id"), 128) or f"fixture-{index + 1}",
            lng=coords[0], lat=coords[1],
            formatted_address=_text(raw.get("formatted_address") or raw.get("address"), 500),
            adcode=_text(raw.get("adcode"), 20),
            city=_text(raw.get("city"), 100),
            district=_text(raw.get("district") or raw.get("area"), 100),
            confidence="fixture",
            provider="fake",
            name=name,
        ))
    return points


class FakeMapProvider:
    """明确不联网的地图提供方，供离线链路和单元测试使用。"""

    name = "fake"

    def __init__(self, points: Iterable[GeoPoint | dict[str, Any]] | None = None, *, fixture_path: Path = MAP_POINTS_FIXTURE):
        if points is None:
            self.points = _fixture_points(fixture_path)
        else:
            self.points = []
            for index, raw in enumerate(points):
                if isinstance(raw, GeoPoint):
                    self.points.append(raw)
                    continue
                if not isinstance(raw, dict):
                    continue
                coords = _point_coordinates(raw)
                name = _text(raw.get("name") or raw.get("address"), 200)
                if coords is None or not name:
                    continue
                self.points.append(GeoPoint(
                    point_id=_text(raw.get("point_id") or raw.get("id"), 128) or f"fake-{index + 1}",
                    lng=coords[0], lat=coords[1],
                    formatted_address=_text(raw.get("formatted_address") or raw.get("address"), 500),
                    adcode=_text(raw.get("adcode"), 20), city=_text(raw.get("city"), 100),
                    district=_text(raw.get("district") or raw.get("area"), 100),
                    confidence=_text(raw.get("confidence"), 80) or "fixture", provider="fake",
                    name=name,
                ))

    def geocode(self, address: str, city: str = "") -> GeoPoint | None:
        needle = _compact(_text(address, 500))
        if not needle:
            return None
        city_needle = _compact(_text(city, 100))
        candidates: list[tuple[int, GeoPoint]] = []
        for point in self.points:
            if city_needle and city_needle not in _compact(point.city) and city_needle not in _compact(point.formatted_address):
                continue
            terms = [point.name, point.formatted_address, point.city, point.district]
            if hasattr(point, "point_id"):
                terms.append(point.point_id)
            haystack = [_compact(term) for term in terms if term]
            if needle in haystack:
                score = 100
            elif any(needle in term or term in needle for term in haystack if term):
                score = 60
            else:
                continue
            candidates.append((score, point))
        if not candidates:
            return None
        _, best_point = max(
            candidates,
            key=lambda item: (item[0], -len(item[1].formatted_address)),
        )
        return best_point

    def route(self, origin: GeoPoint, destination: GeoPoint, mode: str, city: str = "", cityd: str = "") -> CommuteResult:
        mode = mode if mode in ROUTE_URLS else "transit"
        distance = _haversine_meters((origin.lng, origin.lat), (destination.lng, destination.lat)) * 1.18
        rates = {"transit": 3.2, "driving": 1.8, "walking": 11.5, "riding": 4.2}
        base = {"transit": 8, "driving": 4, "walking": 0, "riding": 2}[mode] * 60
        duration = int(round(distance / 1000 * rates[mode] * 60 + base))
        return CommuteResult(
            origin_id=origin.point_id,
            destination_id=destination.point_id,
            mode=mode,
            distance_meters=round(distance, 1),
            duration_seconds=max(60, duration),
            summary=f"约 {max(1, round(duration / 60))} 分钟（离线估算）",
            calculated_at=_now(),
            status="ok",
            message="离线夹具路线，仅用于回归测试。",
        )


def _timeout_seconds() -> float:
    raw = os.getenv("AMAP_REQUEST_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_TIMEOUT_SECONDS
    return max(1.0, min(value, MAX_TIMEOUT_SECONDS))


def _use_env_proxy() -> bool:
    return os.getenv("AMAP_USE_ENV_PROXY", "false").strip().lower() in {"1", "true", "yes", "on"}


class AmapMapProvider:
    """高德 Web Service 地理编码和路线适配器。"""

    name = "amap"

    def __init__(self, api_key: str, *, timeout_seconds: float | None = None, session: requests.Session | None = None):
        if not _text(api_key, 300):
            raise ValueError("AMap Web Service key is required")
        self._api_key = api_key.strip()
        self._timeout_seconds = max(1.0, min(float(timeout_seconds or _timeout_seconds()), MAX_TIMEOUT_SECONDS))
        self._session = session or requests.Session()
        self._session.trust_env = _use_env_proxy()

    def _request(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._session.get(url, params=params, timeout=self._timeout_seconds)
        except requests.Timeout as exc:
            raise MapProviderError("timeout", "高德地图服务响应超时，请稍后再试。") from exc
        except requests.RequestException as exc:
            raise MapProviderError("provider_error", "高德地图服务暂时不可用，请稍后再试。") from exc
        if response.status_code != 200:
            raise MapProviderError("provider_error", "高德地图服务返回异常，请稍后再试。")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise MapProviderError("provider_error", "高德地图服务返回了无法解析的数据。") from exc
        if not isinstance(payload, dict):
            raise MapProviderError("provider_error", "高德地图服务返回了无法解析的数据。")
        info_code = _text(payload.get("infocode"), 20)
        if str(payload.get("status", "")) not in {"1", "true"} and str(payload.get("errcode", "")) not in {"0", ""}:
            if info_code in AMAP_CONFIGURATION_INFOCODES:
                raise MapProviderError("not_configured", "高德 Web 服务 Key 无效或未开通当前服务。")
            if info_code in AMAP_QUOTA_INFOCODES:
                raise MapProviderError("quota_exceeded", "高德地图服务当前已达到调用限额。")
            raise MapProviderError("provider_error", "高德地图服务未能完成本次请求。")
        if str(payload.get("status", "")) != "1" and "route" not in payload:
            raise MapProviderError("provider_error", "高德地图服务未能完成本次请求。")
        return payload

    @staticmethod
    def _known_city_from_text(*values: Any) -> str:
        text = _compact(" ".join(_text(value, 200) for value in values if isinstance(value, str)))
        if not text:
            return ""
        for city in sorted(KNOWN_CITY_NAMES, key=len, reverse=True):
            compact_city = _compact(city).removesuffix("市")
            if compact_city and compact_city in text:
                return compact_city
        return ""

    @classmethod
    def _city_from_geocode_item(cls, item: dict[str, Any]) -> str:
        """读取高德结果里明确出现的城市，不用请求参数伪造城市字段。"""

        raw_city = _text(item.get("city"), 100)
        if raw_city:
            canonical = _canonical_city(raw_city)
            if canonical:
                return canonical
        return cls._known_city_from_text(
            item.get("province"), item.get("district"),
            item.get("township"), item.get("formatted_address"),
        )

    @classmethod
    def _parse_geocode_item(
        cls, item: dict[str, Any], address: str, city: str, index: int
    ) -> tuple[GeoPoint, int] | None:
        raw_location = _text(item.get("location"), 100)
        parts = raw_location.split(",")
        if len(parts) != 2:
            return None
        lng = _coordinate(parts[0], -180, 180)
        lat = _coordinate(parts[1], -90, 90)
        if lng is None or lat is None:
            return None
        observed_city = cls._city_from_geocode_item(item)
        expected_city = _canonical_city(city)
        all_admin_text = " ".join(
            _text(item.get(key), 300)
            for key in ("province", "city", "district", "township", "formatted_address")
        )
        if expected_city:
            if observed_city == expected_city:
                city_score = 2
            elif cls._known_city_from_text(all_admin_text) and observed_city:
                city_score = -1
            elif expected_city in _compact(all_admin_text):
                city_score = 1
            else:
                city_score = 0
        else:
            city_score = 0
        point = GeoPoint(
            point_id="geo_" + hashlib.sha256(f"{address}|{city}|{index}".encode()).hexdigest()[:16],
            lng=lng,
            lat=lat,
            formatted_address=_text(item.get("formatted_address") or address, 500),
            adcode=_text(item.get("adcode"), 20),
            city=observed_city,
            district=_text(item.get("district"), 100),
            confidence="geocode",
            provider=cls.name,
        )
        return point, city_score

    def geocode(self, address: str, city: str = "") -> GeoPoint | None:
        params = {"key": self._api_key, "address": address, "output": "JSON"}
        if city:
            params["city"] = city
        payload = self._request(GEOCODE_URL, params)
        geocodes = payload.get("geocodes")
        if not isinstance(geocodes, list) or not geocodes:
            return None
        parsed: list[tuple[GeoPoint, int, int]] = []
        for index, item in enumerate(geocodes):
            if not isinstance(item, dict):
                continue
            result = self._parse_geocode_item(item, address, city, index)
            if result is not None:
                point, score = result
                parsed.append((point, score, index))
        if not parsed:
            return None
        # 高德可能返回多个同名结果；优先选择明确属于请求城市的结果。
        # 如果所有结果都指向其他城市，仍返回最优的第一个结果，让 MapService
        # 统一转换成 city_conflict，而不是悄悄接受错误坐标。
        point, _, _ = max(parsed, key=lambda item: (item[1], -item[2]))
        return point

    @staticmethod
    def _route_values(payload: dict[str, Any], mode: str) -> tuple[float | None, int | None]:
        route = payload.get("route") if isinstance(payload.get("route"), dict) else payload
        entries = route.get("transits") if mode == "transit" else route.get("paths")
        if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
            return None, None
        first = entries[0]
        distance = _coordinate(first.get("distance"), 0, 100_000_000)
        duration = _coordinate(first.get("duration"), 0, 10_000_000)
        return distance, int(duration) if duration is not None else None

    def route(self, origin: GeoPoint, destination: GeoPoint, mode: str, city: str = "", cityd: str = "") -> CommuteResult:
        mode = mode if mode in ROUTE_URLS else "transit"
        params = {
            "key": self._api_key,
            "origin": f"{origin.lng},{origin.lat}",
            "destination": f"{destination.lng},{destination.lat}",
        }
        if mode == "transit":
            params.update({"city": city, "cityd": cityd or city, "strategy": "0", "nightflag": "0"})
        payload = self._request(ROUTE_URLS[mode], params)
        distance, duration = self._route_values(payload, mode)
        if distance is None or duration is None:
            raise MapProviderError("route_failed", "高德地图未返回可用的通勤路线。")
        return CommuteResult(
            origin_id=origin.point_id,
            destination_id=destination.point_id,
            mode=mode,
            distance_meters=distance,
            duration_seconds=duration,
            summary=f"约 {max(1, round(duration / 60))} 分钟",
            calculated_at=_now(),
            status="ok",
        )


def _provider_from_environment() -> tuple[MapProvider | None, str]:
    provider_name = os.getenv("MAP_PROVIDER", "").strip().lower()
    if not provider_name:
        provider_name = "amap" if rental_mode() == "live" else "fake"
    if provider_name in {"fake", "offline", "fixture"}:
        return FakeMapProvider(), "fake"
    if provider_name == "amap":
        key = os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()
        if not key:
            return None, "amap"
        return AmapMapProvider(key), "amap"
    return None, provider_name


def _record_value(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


def _meaningful_location(value: Any) -> str:
    text = _text(value, 500)
    return "" if text in PLACEHOLDER_LOCATIONS else text


def _point_from_record(item: dict[str, Any], point_id: str, *, provider: str = "provided") -> GeoPoint | None:
    lng = _coordinate(_record_value(item, "lng", "longitude"), -180, 180)
    lat = _coordinate(_record_value(item, "lat", "latitude"), -90, 90)
    if lng is None or lat is None:
        return None
    return GeoPoint(
        point_id=point_id,
        lng=lng,
        lat=lat,
        formatted_address=_text(_record_value(item, "geocoded_address", "formatted_address", "address"), 500),
        adcode=_text(_record_value(item, "adcode"), 20),
        city=_text(_record_value(item, "city"), 100),
        district=_text(_record_value(item, "district", "area"), 100),
        confidence=_text(_record_value(item, "location_confidence", "confidence"), 80) or "provided",
        provider=provider,
    )


@dataclass(frozen=True)
class MapEnrichmentResult:
    target: dict[str, Any]
    listings: list[dict[str, Any]]
    status: str
    message: str
    geocoded_count: int = 0
    routed_count: int = 0
    route_mode: str = "transit"

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": dict(self.target),
            "listings": [dict(item) for item in self.listings],
            "status": self.status,
            "message": self.message,
            "geocoded_count": self.geocoded_count,
            "routed_count": self.routed_count,
            "route_mode": self.route_mode,
        }


class MapService:
    """地图访问的业务边界，失败时返回结构化状态而不是抛出整轮异常。"""

    def __init__(
        self,
        provider: MapProvider | None = None,
        *,
        provider_name: str = "",
        cache: TTLCache | None = None,
        geocode_ttl_seconds: float = DEFAULT_GEOCODE_TTL_SECONDS,
        route_ttl_seconds: float = DEFAULT_ROUTE_TTL_SECONDS,
        minimum_interval_seconds: float = 0.0,
        max_route_candidates: int = DEFAULT_MAX_ROUTE_CANDIDATES,
    ) -> None:
        self.provider = provider
        self.provider_name = provider_name or getattr(provider, "name", "") or "none"
        self.cache = cache or TTLCache()
        self.geocode_ttl_seconds = max(0.0, geocode_ttl_seconds)
        self.route_ttl_seconds = max(0.0, route_ttl_seconds)
        self.rate_limiter = RateLimiter(minimum_interval_seconds)
        self.max_route_candidates = max(0, int(max_route_candidates))

    @classmethod
    def from_environment(cls) -> "MapService":
        provider, name = _provider_from_environment()
        return cls(provider, provider_name=name)

    @property
    def configured(self) -> bool:
        return self.provider is not None

    def geocode(self, address: str, city: str = "") -> GeoPoint:
        if self.provider is None:
            raise MapProviderError("not_configured", "地图服务未配置，暂时无法核验地址。")
        address = _text(address, 500)
        if not address:
            raise MapProviderError("geocode_failed", "没有可用于地理编码的明确地址。")
        key = "geocode:" + hashlib.sha256(f"{self.provider_name}|{address}|{city}".encode()).hexdigest()
        cached = self.cache.get(key)
        if isinstance(cached, GeoPoint):
            return cached
        self.rate_limiter.wait()
        point = self.provider.geocode(address, city)
        if point is None:
            raise MapProviderError("geocode_failed", "地图服务未能匹配这个地址。")
        if _city_conflicts(city, point):
            raise MapProviderError("city_conflict", "地图服务返回的地址与目标城市不一致。")
        self.cache.set(key, point, self.geocode_ttl_seconds)
        return point

    def batch_geocode(self, addresses: Iterable[str], city: str = "") -> list[GeoPoint | None]:
        result: list[GeoPoint | None] = []
        for address in addresses:
            try:
                result.append(self.geocode(address, city))
            except MapProviderError:
                result.append(None)
        return result

    def distance_matrix(
        self,
        origin: GeoPoint,
        destinations: Iterable[GeoPoint],
        mode: str = "straight",
    ) -> list[DistanceResult]:
        del mode  # 直线距离不依赖提供方；路线由 route() 单独限量请求。
        results: list[DistanceResult] = []
        for destination in destinations:
            distance = _haversine_meters((origin.lng, origin.lat), (destination.lng, destination.lat))
            results.append(DistanceResult(
                origin_id=origin.point_id,
                destination_id=destination.point_id,
                distance_meters=round(distance, 1),
                duration_seconds=None,
                mode="straight",
                calculated_at=_now(),
            ))
        return results

    def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        mode: str = "transit",
        city: str = "",
        cityd: str = "",
    ) -> CommuteResult:
        mode = mode if mode in ROUTE_URLS else "transit"
        if self.provider is None:
            return CommuteResult(
                origin_id=origin.point_id, destination_id=destination.point_id, mode=mode,
                distance_meters=None, duration_seconds=None, summary="通勤待计算",
                calculated_at=_now(), status="not_configured", message="地图服务未配置。",
            )
        key = "route:" + hashlib.sha256(
            f"{self.provider_name}|{origin.lng:.6f},{origin.lat:.6f}|{destination.lng:.6f},{destination.lat:.6f}|{mode}|{city}|{cityd}".encode()
        ).hexdigest()
        cached = self.cache.get(key)
        if isinstance(cached, CommuteResult):
            return cached
        try:
            self.rate_limiter.wait()
            result = self.provider.route(origin, destination, mode, city, cityd)
        except MapProviderError as exc:
            return CommuteResult(
                origin_id=origin.point_id, destination_id=destination.point_id, mode=mode,
                distance_meters=None, duration_seconds=None, summary="通勤暂不可用",
                calculated_at=_now(), status=exc.status, message=exc.public_message,
            )
        self.cache.set(key, result, self.route_ttl_seconds)
        return result

    def enrich_listings(
        self,
        target: dict[str, Any] | None,
        listings: Iterable[dict[str, Any]],
        *,
        commute_mode: str = "transit",
        max_route_candidates: int | None = None,
    ) -> MapEnrichmentResult:
        """批量增强房源；地址不明确时绝不使用小区名猜坐标。"""

        target = dict(target or {})
        source_listings = [dict(item) for item in listings if isinstance(item, dict)]
        target_point = _point_from_record(target, "target", provider="confirmed")
        target_status = "provided" if target_point else "pending"
        target_city = _geocode_city_hint(_record_value(target, "city"))
        if target_point is None:
            address = _meaningful_location(_record_value(target, "formatted_address", "address"))
            if not address:
                address = _meaningful_location(_record_value(target, "name"))
            if not address:
                target_status = "pending"
            elif self.provider is not None:
                try:
                    target_point = self.geocode(address, target_city)
                    target_status = "ok"
                except MapProviderError as exc:
                    target_status = exc.status
            elif self.provider is None:
                target_status = "not_configured"
            else:
                target_status = "geocode_failed"

        if target_point is not None:
            target.update({
                "lng": target_point.lng, "lat": target_point.lat,
                "geocoded_address": target_point.formatted_address,
                "location_confidence": target_point.confidence,
                "geocode_status": target_status,
            })
        else:
            target["geocode_status"] = target_status

        listing_points: dict[str, GeoPoint] = {}
        result_listings: list[dict[str, Any]] = []
        geocoded_count = 0
        for index, raw in enumerate(source_listings):
            item = dict(raw)
            listing_id = _text(_record_value(item, "id", "listing_id"), 128) or f"listing-{index + 1}"
            raw_geocode_status = _text(
                _record_value(item, "geocode_status", "geocodeStatus"), 40
            ).lower()
            # 旧的增强结果可能还带有坐标，但明确的失败状态优先级更高；
            # 否则跨城市结果会因为沿用旧坐标而再次出现在地图上。
            point = None if raw_geocode_status in UNTRUSTED_POINT_STATUSES else _point_from_record(
                item, listing_id, provider="listing"
            )
            geocode_status = "provided" if point else "pending"
            if point is None:
                detail_location = _record_value(item, "detailLocation", "detail_location")
                detail_address = detail_location.get("address") if isinstance(detail_location, dict) else None
                address = _meaningful_location(
                    _record_value(item, "address", "geocoded_address")
                ) or _meaningful_location(detail_address)
                listing_city = _geocode_city_hint(
                    _record_value(item, "city")
                    or (detail_location.get("city") if isinstance(detail_location, dict) else "")
                    or _city_from_url(_record_value(item, "detailUrl", "detail_url", "url", "sourceUrl", "source_url"))
                )
                city_hint = target_city or listing_city
                # 离线夹具中的房源使用平台城市代码（例如 cz），而地址是
                # 合成的“示例市”。在 fake 提供方中放宽城市过滤，避免把
                # 回归数据当成真实的跨城市冲突；真实高德仍严格校验城市。
                if (
                    target_point is None
                    and getattr(self.provider, "name", self.provider_name) == "fake"
                ):
                    city_hint = ""
                if address and self.provider is not None:
                    try:
                        point = self.geocode(address, city_hint)
                        geocode_status = "ok"
                    except MapProviderError as exc:
                        geocode_status = exc.status
                elif not address:
                    geocode_status = "missing_address"
                else:
                    geocode_status = "not_configured"
            if point is not None:
                listing_points[listing_id] = point
                geocoded_count += 1
                item.update({
                    "lng": point.lng, "lat": point.lat,
                    "geocoded_address": point.formatted_address or _text(item.get("address"), 500),
                    "location_confidence": point.confidence,
                    "geocode_status": geocode_status,
                })
            else:
                item.update({
                    "geocode_status": geocode_status,
                    "locationStatus": "unverified",
                    "commute_status": geocode_status if geocode_status in {
                        "not_configured", "geocode_failed", "city_conflict", "timeout",
                        "quota_exceeded", "provider_error", "missing_address",
                    } else "pending",
                    "commute_mode": commute_mode,
                    "commute": "位置待核验",
                })
                # 不把旧的地图增强结果带入本轮。
                for stale_key in (
                    "lng", "lat", "geocoded_address", "location_confidence",
                    "distance_meters", "distance", "distance_status",
                    "commute_duration_seconds", "commute_value",
                    "commute_distance_meters", "commute_summary",
                ):
                    item.pop(stale_key, None)
            if target_point is not None and point is not None:
                distance = _haversine_meters((target_point.lng, target_point.lat), (point.lng, point.lat))
                item.update({
                    "locationStatus": "verified",
                    "distance_meters": round(distance, 1),
                    "distance": round(distance / 1000, 2),
                    "distance_status": "ok",
                    "commute_mode": commute_mode,
                    "commute_status": "pending",
                    "commute": "通勤待计算",
                })
            elif target_point is None:
                item.setdefault("commute_mode", commute_mode)
                if point is not None:
                    # 房源地址已经独立定位，但没有确认的目标点，
                    # 因此只能展示房源点，不能伪造距离或通勤结论。
                    item.update({
                        "locationStatus": "unverified",
                        "distance_meters": None,
                        "distance": None,
                        "distance_status": "target_pending",
                        "commute_status": "target_pending",
                        "commute_duration_seconds": None,
                        "commute_value": None,
                        "commute_distance_meters": None,
                        "commute_summary": "目标地点待确认",
                        "commute": "目标地点待确认",
                    })
            result_listings.append(item)

        route_limit = self.max_route_candidates if max_route_candidates is None else max(0, int(max_route_candidates))
        route_candidates: list[tuple[float, int, str]] = []
        if target_point is not None:
            for index, item in enumerate(result_listings):
                listing_id = _text(_record_value(item, "id", "listing_id"), 128) or f"listing-{index + 1}"
                point = listing_points.get(listing_id)
                distance = _coordinate(item.get("distance_meters"), 0, 100_000_000)
                if point is not None and distance is not None:
                    route_candidates.append((distance, index, listing_id))
        route_candidates.sort()
        selected_ids = {listing_id for _, _, listing_id in route_candidates[:route_limit]}
        routed_count = 0
        for index, item in enumerate(result_listings):
            listing_id = _text(_record_value(item, "id", "listing_id"), 128) or f"listing-{index + 1}"
            if listing_id not in selected_ids:
                if item.get("locationStatus") == "verified":
                    item["commute_status"] = "pending"
                    item["commute"] = "通勤待计算"
                continue
            commute = self.route(target_point, listing_points[listing_id], commute_mode,
                                 target_city,
                                 _geocode_city_hint(_record_value(item, "city")))
            item.update({
                "commute_mode": commute.mode,
                "commute_status": commute.status,
                "commute_duration_seconds": commute.duration_seconds,
                "commute_value": round(commute.duration_seconds / 60, 1) if commute.duration_seconds is not None else None,
                "commute_distance_meters": commute.distance_meters,
                "commute": commute.summary if commute.status == "ok" else commute.message or commute.summary,
                "commute_summary": commute.summary,
            })
            routed_count += int(commute.status == "ok")

        if target_point is None:
            status = target_status
            message = "房源地址已独立定位（如有可用坐标）；目标地点尚未确认，距离和通勤待计算。"
        elif geocoded_count == 0 and result_listings:
            status = "partial"
            message = "房源没有明确可定位地址，距离和通勤待核验。"
        elif self.provider is None:
            status = "not_configured"
            message = "地图服务未配置，已保留房源和目标地点信息。"
        elif any(item.get("commute_status") in {"timeout", "quota_exceeded", "route_failed"} for item in result_listings):
            status = "partial"
            message = "部分通勤路线未能计算，已保留可用的直线距离。"
        else:
            status = "ok"
            message = "已补充可用房源的坐标、距离和有限通勤参考。"
        return MapEnrichmentResult(
            target=target,
            listings=result_listings,
            status=status,
            message=message,
            geocoded_count=geocoded_count,
            routed_count=routed_count,
            route_mode=commute_mode,
        )

    # 领域文档中的简短名称。
    enrich = enrich_listings


__all__ = [
    "AmapMapProvider",
    "CommuteResult",
    "DistanceResult",
    "FakeMapProvider",
    "GeoPoint",
    "MapEnrichmentResult",
    "MapProviderError",
    "MapService",
    "RateLimiter",
    "TTLCache",
]

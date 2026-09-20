from __future__ import annotations

import unittest

from agent_core.map_service import (
    AmapMapProvider,
    CommuteResult,
    FakeMapProvider,
    GeoPoint,
    MapService,
)


class _StaticMapProvider:
    name = "static"

    def __init__(self, point: GeoPoint) -> None:
        self.point = point

    def geocode(self, address: str, city: str = "") -> GeoPoint | None:
        del address, city
        return self.point

    def route(self, origin: GeoPoint, destination: GeoPoint, mode: str,
              city: str = "", cityd: str = "") -> CommuteResult:
        del city, cityd
        return CommuteResult(
            origin_id=origin.point_id,
            destination_id=destination.point_id,
            mode=mode,
            distance_meters=1000,
            duration_seconds=600,
            summary="约 10 分钟",
            calculated_at="now",
        )


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self) -> dict:
        return self.payload


class _FakeSession:
    trust_env = False

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, *, params: dict, timeout: float) -> _FakeResponse:
        self.calls.append((url, params))
        return _FakeResponse(self.payload)


class MapServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MapService(
            FakeMapProvider(),
            provider_name="fake",
            max_route_candidates=2,
        )
        self.target = {
            "name": "示例学院",
            "formatted_address": "江苏省示例市示例区示例路 155 号",
            "city": "示例市",
            "lng": 120.281,
            "lat": 31.981,
        }

    def test_listing_city_code_does_not_block_confirmed_target_city(self) -> None:
        result = self.service.enrich_listings(self.target, [{
            "id": "listing-1",
            "title": "示例房源",
            "city": "cz",
            "address": "江苏省示例市示例区示例路 168 号",
        }])

        listing = result.listings[0]
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.geocoded_count, 1)
        self.assertEqual(result.routed_count, 1)
        self.assertEqual(listing["locationStatus"], "verified")
        self.assertEqual(listing["geocode_status"], "ok")
        self.assertEqual(listing["commute_status"], "ok")
        self.assertIsNotNone(listing["distance_meters"])
        self.assertIsNotNone(listing["commute_value"])

    def test_detail_location_can_supply_address_but_community_alone_cannot(self) -> None:
        with_detail_address = self.service.enrich_listings(self.target, [{
            "id": "listing-detail-address",
            "community": "示例小区",
            "detail_location": {"address": "江苏省示例市示例区示例湖中路 26 号"},
        }]).listings[0]
        without_address = self.service.enrich_listings(self.target, [{
            "id": "listing-community-only",
            "community": "大学城附近小区",
        }]).listings[0]

        self.assertEqual(with_detail_address["locationStatus"], "verified")
        self.assertEqual(with_detail_address["geocode_status"], "ok")
        self.assertEqual(without_address["geocode_status"], "missing_address")
        self.assertEqual(without_address["locationStatus"], "unverified")
        self.assertIsNone(without_address.get("distance_meters"))

    def test_listing_addresses_are_geocoded_without_a_target(self) -> None:
        result = self.service.enrich_listings(None, [{
            "id": "listing-without-target",
            "city": "cz",
            "address": "江苏省示例市示例区示例路 168 号",
        }])

        listing = result.listings[0]
        self.assertEqual(result.status, "pending")
        self.assertEqual(result.geocoded_count, 1)
        self.assertEqual(listing["geocode_status"], "ok")
        self.assertEqual(listing["locationStatus"], "unverified")
        self.assertEqual(listing["commute_status"], "target_pending")
        self.assertEqual(listing["commute"], "目标地点待确认")
        self.assertIsNotNone(listing["lng"])
        self.assertIsNotNone(listing["lat"])
        self.assertIsNone(listing["distance_meters"])
        self.assertIsNone(listing["distance"])
        self.assertIsNone(listing["commute_value"])
        self.assertIsNone(listing["commute_duration_seconds"])

    def test_missing_address_without_a_target_does_not_claim_target_pending(self) -> None:
        listing = self.service.enrich_listings(None, [{
            "id": "community-only",
            "community": "只有小区名称",
        }]).listings[0]

        self.assertEqual(listing["geocode_status"], "missing_address")
        self.assertEqual(listing["commute_status"], "missing_address")
        self.assertEqual(listing["commute"], "位置待核验")
        self.assertNotIn("lng", listing)
        self.assertNotIn("lat", listing)

    def test_old_target_metrics_are_cleared_when_target_is_missing(self) -> None:
        listing = self.service.enrich_listings(None, [{
            "id": "stale-metrics",
            "address": "江苏省示例市示例区示例路 168 号",
            "lng": 120.282,
            "lat": 31.982,
            "locationStatus": "verified",
            "geocode_status": "ok",
            "distance_meters": 850,
            "distance": 0.85,
            "commute_status": "ok",
            "commute_value": 18,
            "commute_duration_seconds": 1080,
        }]).listings[0]

        self.assertEqual(listing["locationStatus"], "unverified")
        self.assertEqual(listing["commute_status"], "target_pending")
        self.assertIsNone(listing["distance_meters"])
        self.assertIsNone(listing["distance"])
        self.assertIsNone(listing["commute_value"])
        self.assertIsNone(listing["commute_duration_seconds"])

    def test_explicit_cross_city_geocode_is_not_used_for_distance(self) -> None:
        provider = _StaticMapProvider(GeoPoint(
            point_id="tianjin-point",
            lng=117.2,
            lat=39.1,
            formatted_address="天津市南开区某路 1 号",
            city="天津",
            district="南开区",
        ))
        service = MapService(provider, provider_name="static")
        result = service.enrich_listings({
            "name": "南京目标",
            "city": "南京",
            "lng": 118.8,
            "lat": 32.1,
        }, [{"id": "cross-city", "address": "某路 1 号"}])

        listing = result.listings[0]
        self.assertEqual(listing["geocode_status"], "city_conflict")
        self.assertEqual(listing["locationStatus"], "unverified")
        self.assertIsNone(listing.get("distance_meters"))
        self.assertEqual(result.status, "partial")

    def test_amap_geocode_prefers_a_result_in_the_requested_city(self) -> None:
        session = _FakeSession({
            "status": "1",
            "infocode": "10000",
            "geocodes": [
                {
                    "location": "117.2,39.1",
                    "formatted_address": "天津市南开区某路 1 号",
                    "city": "天津市",
                    "district": "南开区",
                },
                {
                    "location": "118.8,32.1",
                    "formatted_address": "南京市鼓楼区某路 1 号",
                    "city": "南京市",
                    "district": "鼓楼区",
                },
            ],
        })
        provider = AmapMapProvider("test-key", session=session)

        point = provider.geocode("某路 1 号", "南京")

        self.assertIsNotNone(point)
        self.assertEqual(point.city, "南京")
        self.assertEqual((point.lng, point.lat), (118.8, 32.1))
        self.assertEqual(session.calls[0][1]["city"], "南京")


if __name__ == "__main__":
    unittest.main()

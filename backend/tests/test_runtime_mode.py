from __future__ import annotations

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_core import (
    batch_detail_tool,
    candidate_search,
    human_verification_tool,
    location_tool,
    map_service,
)
from agent_core.runtime_mode import rental_mode


class RuntimeModeTests(unittest.TestCase):
    """所有外部入口均 mock；测试默认 live 不会访问真实平台或弹验证窗口。"""

    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.enterContext(patch.object(sys, "path", list(sys.path)))
        self.enterContext(patch.dict(candidate_search._LAST_PLATFORM_SEARCH_STATE, {}, clear=True))
        self.enterContext(patch.dict(human_verification_tool._LAST_ATTEMPT_AT, {}, clear=True))

    def test_missing_and_blank_modes_default_to_live(self) -> None:
        self.assertEqual(rental_mode(), "live")
        for value in ("", " ", "live", " LIVE "):
            with self.subTest(value=value):
                os.environ["RENTAL_DEMO_MODE"] = value
                self.assertEqual(rental_mode(), "live")

    def test_offline_requires_explicit_selection(self) -> None:
        for value in ("offline", " OFFLINE "):
            with self.subTest(value=value):
                os.environ["RENTAL_DEMO_MODE"] = value
                self.assertEqual(rental_mode(), "offline")

    def test_invalid_modes_do_not_silently_select_fixtures_or_network(self) -> None:
        for value in ("demo", "fixture", "offline_fixture", "lve", "false"):
            with self.subTest(value=value):
                os.environ["RENTAL_DEMO_MODE"] = value
                with self.assertRaisesRegex(ValueError, "RENTAL_DEMO_MODE"):
                    rental_mode()

    def test_search_defaults_to_platform_adapter_and_never_loads_fixture(self) -> None:
        scrape = Mock(return_value={
            "listings": [{
                "title": "平台候选",
                "platform": "58同城",
                "city": "cz",
                "detail_url": "https://cz.58.com/zufang/123.shtml",
            }],
            "platforms": {"58同城": "ok"},
        })
        with patch.dict(sys.modules, {"scrape_all": SimpleNamespace(scrape_all=scrape)}), patch.object(
            candidate_search, "_load_fixture"
        ) as load_fixture:
            result = json.loads(candidate_search.search_rental_candidates.invoke({"city": "cz"}))

        scrape.assert_called_once()
        load_fixture.assert_not_called()
        self.assertEqual(result["mode"], "live_skill")
        self.assertEqual(result["listing_count"], 1)
        self.assertEqual(result["listings"][0]["title"], "平台候选")

    def test_live_platform_failure_does_not_return_fixtures(self) -> None:
        scrape = Mock(return_value={
            "listings": [], "platforms": {"58同城": "error"}, "warnings": ["request failed"],
        })
        with patch.dict(sys.modules, {"scrape_all": SimpleNamespace(scrape_all=scrape)}), patch.object(
            candidate_search, "_load_fixture"
        ) as load_fixture:
            result = json.loads(candidate_search.search_rental_candidates.invoke({"city": "cz"}))
        load_fixture.assert_not_called()
        self.assertEqual(result["mode"], "live_skill")
        self.assertEqual(result["listing_count"], 0)
        self.assertEqual(result["platforms"]["58同城"], "error")
        self.assertNotEqual(result["status"], "ok")

    def test_live_adapter_exception_does_not_fall_back_to_fixture(self) -> None:
        scrape = Mock(side_effect=RuntimeError("adapter unavailable"))
        with patch.dict(sys.modules, {"scrape_all": SimpleNamespace(scrape_all=scrape)}), patch.object(
            candidate_search, "_load_fixture"
        ) as load_fixture:
            with self.assertRaisesRegex(RuntimeError, "adapter unavailable"):
                candidate_search.search_rental_candidates.invoke({"city": "cz"})
        load_fixture.assert_not_called()

    def test_explicit_offline_search_does_not_call_platform_adapter(self) -> None:
        os.environ["RENTAL_DEMO_MODE"] = "offline"
        scrape = Mock()
        with patch.dict(sys.modules, {"scrape_all": SimpleNamespace(scrape_all=scrape)}), patch.object(
            candidate_search, "_load_fixture", return_value=[]
        ) as load_fixture:
            result = json.loads(candidate_search.search_rental_candidates.invoke({"city": "cz"}))
        scrape.assert_not_called()
        load_fixture.assert_called_once()
        self.assertEqual(result["mode"], "offline_fixture")

    def test_detail_tool_defaults_to_live(self) -> None:
        urls = ["https://cz.58.com/zufang/123.shtml"]
        with patch.object(batch_detail_tool, "fetch_detail_batch", return_value={"mode": "live"}) as fetch:
            batch_detail_tool.batch_fetch_listing_details.invoke({"urls": urls})
        fetch.assert_called_once_with(urls, live=True, max_urls=batch_detail_tool.MAX_URLS)

    def test_detail_tool_normalizes_explicit_mode_consistently(self) -> None:
        for value, live in ((" LIVE ", True), ("offline", False), (" OFFLINE ", False)):
            with self.subTest(value=value):
                os.environ["RENTAL_DEMO_MODE"] = value
                with patch.object(batch_detail_tool, "fetch_detail_batch", return_value={}) as fetch:
                    batch_detail_tool.batch_fetch_listing_details.invoke({"urls": []})
                self.assertEqual(fetch.call_args.kwargs["live"], live)

    def test_internal_detail_reader_requires_explicit_mode(self) -> None:
        with self.assertRaises(TypeError):
            batch_detail_tool.fetch_detail_batch([])

    def test_invalid_mode_is_rejected_before_any_search_or_detail_access(self) -> None:
        os.environ["RENTAL_DEMO_MODE"] = "lve"
        with patch.object(candidate_search, "_load_fixture") as fixture, patch.object(
            batch_detail_tool, "fetch_detail_batch"
        ) as fetch:
            with self.assertRaisesRegex(ValueError, "RENTAL_DEMO_MODE"):
                candidate_search.search_rental_candidates.invoke({"city": "cz"})
            with self.assertRaisesRegex(ValueError, "RENTAL_DEMO_MODE"):
                batch_detail_tool.batch_fetch_listing_details.invoke({"urls": []})
        fixture.assert_not_called()
        fetch.assert_not_called()

    def test_missing_amap_key_reports_unavailable_instead_of_fake_locations(self) -> None:
        location = json.loads(location_tool.resolve_target_place.invoke({"query": "常州大学"}))
        provider, name = map_service._provider_from_environment()
        self.assertEqual(location["mode"], "live")
        self.assertEqual(location["provider"], "amap")
        self.assertEqual(location["status"], "not_configured")
        self.assertEqual(location["candidates"], [])
        self.assertEqual(name, "amap")
        self.assertIsNone(provider)

    def test_configured_amap_key_selects_real_providers_by_default(self) -> None:
        os.environ["AMAP_WEB_SERVICE_KEY"] = "test-key-not-used-for-requests"
        place_selection = location_tool._provider_for_current_mode()
        map_provider, name = map_service._provider_from_environment()
        self.assertIsInstance(place_selection.provider, location_tool.AmapPlaceProvider)
        self.assertIsInstance(map_provider, map_service.AmapMapProvider)
        self.assertEqual(place_selection.mode, "live")
        self.assertEqual(name, "amap")

    def test_explicit_offline_mode_selects_fake_map_providers(self) -> None:
        os.environ["RENTAL_DEMO_MODE"] = "offline"
        place_selection = location_tool._provider_for_current_mode()
        map_provider, name = map_service._provider_from_environment()
        self.assertIsInstance(place_selection.provider, location_tool.FakePlaceProvider)
        self.assertIsInstance(map_provider, map_service.FakeMapProvider)
        self.assertEqual(place_selection.mode, "offline_fixture")
        self.assertEqual(name, "fake")

    def test_map_provider_can_be_explicitly_configured_independently(self) -> None:
        os.environ["MAP_PROVIDER"] = "fake"
        self.assertEqual(rental_mode(), "live")
        self.assertIsInstance(
            location_tool._provider_for_current_mode().provider, location_tool.FakePlaceProvider
        )
        self.assertIsInstance(map_service._provider_from_environment()[0], map_service.FakeMapProvider)

    def test_invalid_place_input_is_not_labeled_as_offline_fixture(self) -> None:
        result = json.loads(location_tool.resolve_target_place.invoke({"query": ""}))
        self.assertEqual(result["status"], "invalid_input")
        self.assertNotEqual(result["mode"], "offline_fixture")
        self.assertNotEqual(result["provider"], "fake")

    def test_verification_is_available_by_default_but_still_requires_authorization(self) -> None:
        with patch.object(candidate_search, "consume_platform_verification_request", return_value=False), patch.object(
            human_verification_tool, "_verify_58_session"
        ) as verify:
            result = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                "platform": "58同城", "city": "cz",
            }))
        self.assertEqual(result["status"], "not_needed")
        verify.assert_not_called()

    def test_authorized_verification_uses_live_adapter_when_mode_is_unset(self) -> None:
        with patch.object(candidate_search, "consume_platform_verification_request", return_value=True), patch.object(
            human_verification_tool, "_verify_58_session", return_value={"status": "ok"}
        ) as verify:
            result = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                "platform": "58同城", "city": "cz",
            }))
        self.assertEqual(result["status"], "ok")
        verify.assert_called_once()

    def test_offline_verification_never_opens_a_browser(self) -> None:
        os.environ["RENTAL_DEMO_MODE"] = "offline"
        with patch.object(human_verification_tool, "_verify_58_session") as verify:
            result = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                "platform": "58同城", "city": "cz",
            }))
        self.assertEqual(result["status"], "unsupported")
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()

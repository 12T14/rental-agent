from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent_core.candidate_search import _compact
from agent_core.location_tool import AmapPlaceProvider, _base_result


class LocationToolTests(unittest.TestCase):
    def test_amap_does_not_use_system_proxy_by_default(self) -> None:
        with patch.dict(os.environ, {"AMAP_USE_ENV_PROXY": "false"}, clear=False):
            provider = AmapPlaceProvider("test-key")

        self.assertFalse(provider._session.trust_env)

    def test_amap_can_explicitly_use_system_proxy(self) -> None:
        with patch.dict(os.environ, {"AMAP_USE_ENV_PROXY": "true"}, clear=False):
            provider = AmapPlaceProvider("test-key")

        self.assertTrue(provider._session.trust_env)

    def test_missing_list_location_is_explicit_in_agent_tool_payload(self) -> None:
        result = _compact({"title": "标题线索", "detail_url": "https://example.58.com/zufang/item.html"})

        self.assertEqual(result["community"], "平台列表未提供小区")
        self.assertEqual(result["address"], "平台列表未提供具体地址")

    def test_all_location_selection_statuses_require_confirmation(self) -> None:
        for status in ("needs_confirmation", "candidates_ready", "needs_city_confirmation"):
            with self.subTest(status=status):
                result = _base_result(query="地点", status=status, message="请确认")
                self.assertTrue(result["requires_user_confirmation"])


if __name__ == "__main__":
    unittest.main()

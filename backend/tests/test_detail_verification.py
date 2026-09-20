from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core import batch_detail_tool, candidate_search, human_verification_tool


class _FakeResponse:
    def __init__(self, url: str, *, status_code: int = 200, text: str = "", location: str = "") -> None:
        self.url = url
        self.status_code = status_code
        self.text = text
        self.headers = {"Location": location} if location else {}
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("blocked responses must be handled before raise_for_status")


class DetailVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        batch_detail_tool._DETAIL_VERIFICATION_REQUESTS.clear()
        human_verification_tool._LAST_ATTEMPT_AT.clear()
        candidate_search._LAST_PLATFORM_SEARCH_STATE.clear()

    def test_detail_batch_stops_platform_after_first_block(self) -> None:
        urls = [
            "https://example.58.com/zufang/one.shtml",
            "https://example.58.com/zufang/two.shtml",
            "https://example.58.com/zufang/three.shtml",
        ]
        html = "<html><title>一室一厅</title><body>" + "公开房源说明 " * 20 + "租金 1200 元/月</body></html>"
        responses = [
            _FakeResponse(urls[0], text=html),
            _FakeResponse(urls[1], status_code=403),
        ]

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {
            "RENTAL_58_SESSION_FILE": str(Path(temp_dir) / "missing-session.json"),
            "RENTAL_ANJUKE_SESSION_FILE": str(Path(temp_dir) / "missing-anjuke.json"),
            "RENTAL_FANG_SESSION_FILE": str(Path(temp_dir) / "missing-fang.json"),
        }), patch.object(batch_detail_tool.requests, "get", side_effect=responses) as request_get, patch.object(
            batch_detail_tool, "_artifact_path", return_value=Path(temp_dir) / "details.json"
        ):
            result = batch_detail_tool.fetch_detail_batch(urls, live=True, delay_seconds=0)

        self.assertEqual(request_get.call_count, 2)
        self.assertEqual(result["processed_count"], 2)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual([item["status"] for item in result["results"]], [
            "ok", "blocked", "skipped_after_block",
        ])
        self.assertTrue(result["needs_human_verification"])
        self.assertEqual(result["detail_verification_requests"], [{
            "platform": "58同城",
            "target_url": urls[1],
            "retry_urls": [urls[1]],
            "reason": "detail_access_blocked",
        }])

    def test_detail_authorization_matches_exact_url_and_is_consumed_once(self) -> None:
        target_url = "https://example.58.com/zufang/item.shtml?tracking=drop"
        canonical_target = "https://example.58.com/zufang/item.shtml"
        batch_detail_tool._record_detail_verification_request("58", target_url)

        self.assertFalse(batch_detail_tool.consume_detail_verification_request(
            "58", "https://example.58.com/zufang/other.shtml"
        ))
        self.assertTrue(batch_detail_tool.consume_detail_verification_request("58", canonical_target))
        self.assertFalse(batch_detail_tool.consume_detail_verification_request("58", canonical_target))

    def test_detail_verification_opens_once_and_returns_single_retry_instruction(self) -> None:
        target_url = "https://example.58.com/zufang/item.shtml"
        batch_detail_tool._record_detail_verification_request("58", target_url)

        with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "live"}), patch.object(
            human_verification_tool,
            "_verify_58_session",
            return_value={"status": "ok", "message": "verified", "session_saved": True},
        ) as verify:
            first = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                "platform": "58同城",
                "target_url": target_url,
                "phase": "detail",
            }))
            second = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                "platform": "58同城",
                "target_url": target_url,
                "phase": "detail",
            }))

        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["phase"], "detail")
        self.assertIn("单条", first["next_step"])
        self.assertEqual(second["status"], "cooldown")
        verify.assert_called_once()

    def test_search_verification_still_uses_search_authorization(self) -> None:
        candidate_search._record_platform_search_state(
            "58", blocked=True, city="cz", area="wujin", keyword="西太湖"
        )

        with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "live"}), patch.object(
            human_verification_tool,
            "_verify_58_session",
            return_value={"status": "ok", "message": "verified", "session_saved": True},
        ) as verify:
            result = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                "platform": "58同城",
                "city": "cz",
                "area": "wujin",
                "keyword": "西太湖",
                "phase": "search",
            }))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["phase"], "search")
        verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()

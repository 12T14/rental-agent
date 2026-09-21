from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        }), patch.object(batch_detail_tool.requests.Session, "get", side_effect=responses) as request_get, patch.object(
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
            "continuation_urls": [urls[2]],
            "reason": "detail_access_blocked",
        }])

    def test_detail_batch_round_robins_platforms_before_global_limit(self) -> None:
        urls = [
            "https://cz.58.com/zufang/1.shtml",
            "https://cz.58.com/zufang/2.shtml",
            "https://cz.58.com/zufang/3.shtml",
            "https://cz.zu.anjuke.com/fangyuan/4",
            "https://cz.zu.anjuke.com/fangyuan/5",
            "https://zu.fang.com/cz/chuzu/6_6_1.htm",
        ]
        html = "<html><title>一室一厅</title><body>" + "公开房源说明 " * 20 + "租金 1200 元/月</body></html>"
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(os.environ, {
            "RENTAL_58_SESSION_FILE": str(Path(temp_dir) / "missing-58.json"),
            "RENTAL_ANJUKE_SESSION_FILE": str(Path(temp_dir) / "missing-anjuke.json"),
            "RENTAL_FANG_SESSION_FILE": str(Path(temp_dir) / "missing-fang.json"),
        }), patch.object(
            batch_detail_tool.requests.Session, "get",
            side_effect=[_FakeResponse(url, text=html) for url in urls],
        ) as request_get, patch.object(
            batch_detail_tool, "_artifact_path", return_value=Path(temp_dir) / "details.json"
        ):
            result = batch_detail_tool.fetch_detail_batch(urls, live=True, max_urls=5, delay_seconds=0)

        requested = [call.args[0] for call in request_get.call_args_list]
        self.assertEqual(requested, [urls[0], urls[3], urls[5], urls[1], urls[4]])
        self.assertEqual(result["omitted_count"], 1)
        self.assertEqual(result["counts"]["ok"], 5)

    def test_safe_same_city_redirect_preserves_query_and_requested_identity(self):
        url = "https://cz.zu.anjuke.com/fangyuan/123"
        target = url + "?spi_ajkd=signed"
        html = "<title>房源</title><body>" + "公开房源说明 " * 20 + "租金 1000 元/月</body>"
        with patch.object(batch_detail_tool.requests.Session, "get", side_effect=[
            _FakeResponse(url, status_code=302, location="?spi_ajkd=signed"),
            _FakeResponse(target, text=html),
        ]) as get:
            response, rejected = batch_detail_tool._read_http_detail(url)
        self.assertIsNone(rejected)
        self.assertEqual(get.call_args_list[1].args[0], target)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        result = batch_detail_tool._detail_result_for_requested_url(response.text, url, response.url)
        self.assertEqual(result["url"], url)
        self.assertEqual(result["status"], "ok")

    def test_redirects_reject_unsafe_hops_and_bound_loops(self):
        url = "https://cz.zu.anjuke.com/fangyuan/123"
        for target in (
            "http://cz.zu.anjuke.com/fangyuan/123",
            "https://tj.zu.anjuke.com/fangyuan/123",
            "https://cz.58.com/zufang/123.shtml",
            "https://evil.example/123",
            "https://user:secret@cz.zu.anjuke.com/fangyuan/123",
            "https://cz.zu.anjuke.com:444/fangyuan/123",
            "https://cz.zu.anjuke.com/fangyuan/456",
            "https://cz.zu.anjuke.com/login",
            "/fangyuan/",
        ):
            with self.subTest(target=target), patch.object(batch_detail_tool.requests.Session, "get", return_value=
                _FakeResponse(url, status_code=302, location=target)
            ) as get:
                _, result = batch_detail_tool._read_http_detail(url)
                self.assertEqual(result["status"], "rejected_redirect")
                get.assert_called_once()
        with patch.object(batch_detail_tool.requests.Session, "get", return_value=
            _FakeResponse(url, status_code=302, location=url)
        ) as get:
            _, result = batch_detail_tool._read_http_detail(url)
            self.assertEqual(result["status"], "redirect_not_followed")
            get.assert_called_once()
        with patch.object(batch_detail_tool.requests.Session, "get", side_effect=[
            _FakeResponse(url, status_code=302, location=f"?hop={n}") for n in range(4)
        ]) as get:
            _, result = batch_detail_tool._read_http_detail(url)
            self.assertEqual(result["status"], "redirect_not_followed")
            self.assertEqual(get.call_count, 4)

    def test_challenge_redirect_stops_without_fetching_verification_page(self):
        url = "https://cz.58.com/zufang/123.shtml"
        with patch.object(batch_detail_tool.requests.Session, "get", return_value=_FakeResponse(
            url, status_code=302, location="https://callback.58.com/antibot/verifycode?token=private"
        )) as get:
            _, result = batch_detail_tool._read_http_detail(url)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("private", json.dumps(result))
        get.assert_called_once()

    def test_multi_platform_batch_uses_one_playwright_with_isolated_contexts(self):
        from playwright import sync_api
        import sys
        sys.path.insert(0, str(batch_detail_tool.SKILL_ROOT))
        import scrape_58

        urls = ["https://cz.58.com/zufang/1.shtml", "https://cz.zu.anjuke.com/fangyuan/2",
                "https://zu.fang.com/cz/chuzu/3_3_1.htm"]
        contexts = [MagicMock() for _ in urls]
        for context, url in zip(contexts, urls):
            page = context.new_page.return_value
            page.url = url
            page.goto.return_value.status = 200
            page.content.return_value = "<body>" + "公开房源说明 " * 20 + "租金 1000 元/月</body>"
        browser = MagicMock()
        browser.new_context.side_effect = contexts
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.touch()
            env = {f"RENTAL_{name}_SESSION_FILE": str(state) for name in ("58", "ANJUKE", "FANG")}
            with patch.dict(os.environ, env), patch.object(sync_api, "sync_playwright") as start, patch.object(
                scrape_58, "_launch_browser", return_value=browser
            ), patch.object(batch_detail_tool, "_artifact_path", return_value=Path(directory) / "out.json"):
                result = batch_detail_tool.fetch_detail_batch(urls, live=True, delay_seconds=0)
        self.assertEqual(result["browser_session_platforms"], ["58", "anjuke", "fang"])
        self.assertEqual(result["counts"]["ok"], 3)
        start.assert_called_once()
        start.return_value.start.return_value.stop.assert_called_once()
        browser.close.assert_called_once()
        self.assertEqual(browser.new_context.call_count, 3)
        for context in contexts:
            context.close.assert_called_once()
            context.new_page.return_value.close.assert_called_once()

    def test_cleanup_failure_does_not_discard_successful_detail(self):
        url = "https://cz.58.com/zufang/1.shtml"
        playwright, browser, context = MagicMock(), MagicMock(), MagicMock()
        page = context.new_page.return_value
        page.url = url
        page.goto.return_value.status = 200
        page.content.return_value = "<body>" + "公开房源说明 " * 20 + "租金 1000 元/月</body>"
        context.close.side_effect = RuntimeError("already closed")
        browser.close.side_effect = RuntimeError("already closed")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.touch()
            with patch.dict(os.environ, {"RENTAL_58_SESSION_FILE": str(state)}), patch.object(
                batch_detail_tool, "_open_browser_session", return_value=(playwright, browser, context)
            ), patch.object(batch_detail_tool, "_artifact_path", return_value=Path(directory) / "out.json"):
                result = batch_detail_tool.fetch_detail_batch([url], live=True, delay_seconds=0)
        self.assertEqual(result["counts"]["ok"], 1)
        playwright.stop.assert_called_once()

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
        continuation_url = "https://example.58.com/zufang/next.shtml"
        batch_detail_tool._record_detail_verification_request("58", target_url, [continuation_url])

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
        self.assertEqual(first["retry_urls"], [target_url])
        self.assertEqual(first["continuation_urls"], [continuation_url])
        self.assertEqual(first["continuation_count"], 1)
        self.assertIn("retry_urls", first["next_step"])
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

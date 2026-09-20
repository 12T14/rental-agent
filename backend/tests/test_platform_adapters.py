from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_core import batch_detail_tool, candidate_search, human_verification_tool
from app.agent_runtime import _public_probable_list_url, _safe_platform_statuses

SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "rental-scraper"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import platform_pages
import scrape_58
import scrape_all
import scrape_anjuke
import scrape_fang


FANG_CITY = "https://zu.fang.com/cz/house/"
FANG_REGION = "https://zu.fang.com/cz/house-a0342/"
ANJUKE_REGION = "https://cz.zu.anjuke.com/fangyuan/wujin/"
CHALLENGE = '<script>window.location.href="https://callback.58.com/antibot/verifycode?secret=private";</script>'


def fang_card(item: str = "1", *, title: str = "新城樾隽中央", rent: int = 7500,
              community: str = "新城樾隽中央", region: str | None = "0342", city: str = "cz") -> str:
    region_link = f'<a href="//zu.fang.com/{city}/house-a{region}-b022885/">武进</a>' if region else ""
    return f"""
    <dl class="list"><dd class="info">
      <p class="title"><a href="/{city}/chuzu/3_{item}_1.htm">{title}</a></p>
      <p>整租 1室1厅 36平米</p>
      <p><a href="/{city}/house-xm123/">{community}</a>{region_link}</p>
    </dd><dd class="moreInfo"><span class="price">{rent}</span>元/月</dd></dl>
    """


def anjuke_card(item: str = "1", *, metadata: str = "", city: str = "cz", rent: str = "1200") -> str:
    return f"""
    <div class="zu-itemmod"><div class="zu-info">
      <h3><a href="https://{city}.zu.anjuke.com/fangyuan/{item}.html">公开房源</a></h3>
      <p>{metadata}</p>
    </div><div class="zu-side">{rent} 元/月</div></div>
    """


class PlatformAdapterTests(unittest.TestCase):
    def test_fang_card_fields_and_real_relative_details_stay_together(self) -> None:
        html = fang_card(title="新城樾隽中央 2000元广告", rent=7500) + fang_card(
            "2", title="第二套房源", rent=2000, community="世茂香槟湖"
        )
        rows = scrape_fang.parse_fang_html(html, FANG_REGION, "cz", "wujin")
        self.assertEqual([(row["title"], row["price"], row["community"]) for row in rows], [
            ("新城樾隽中央 2000元广告", 7500, "新城樾隽中央"), ("第二套房源", 2000, "世茂香槟湖"),
        ])
        self.assertEqual(rows[0]["detail_url"], "https://zu.fang.com/cz/chuzu/3_1_1.htm")
        self.assertEqual(rows[0]["source_page"], FANG_REGION)
        self.assertEqual(rows[0]["region_url"], "https://zu.fang.com/cz/house-a0342-b022885/")

    def test_fang_cross_city_detail_is_rejected(self) -> None:
        rows = scrape_fang.parse_fang_html(fang_card(city="tj") + fang_card("2"), FANG_CITY, "cz")
        self.assertEqual(len(rows), 1)
        self.assertIn("/cz/chuzu/", rows[0]["detail_url"])

    def fang_search(self, responses: list[tuple[str, str, int]], area: str = "wujin") -> tuple[dict, list]:
        with patch.object(scrape_fang, "_fetch_with_session", side_effect=responses) as fetch, patch.object(
            scrape_fang, "save_page_snapshot"
        ):
            result = scrape_fang.scrape_fang("cz", area, "完整目标地名", status=True)
        return result, fetch.call_args_list

    def test_fang_region_is_discovered_and_recommendations_are_removed(self) -> None:
        city_html = '<a href="/cz/house-a0342/">武进区</a>' + fang_card()
        region_html = fang_card() + fang_card("2", region="0355", rent=2000)
        result, calls = self.fang_search([(city_html, FANG_CITY, 200), (region_html, FANG_REGION, 200)])
        self.assertEqual([call.args[0] for call in calls], [FANG_CITY, FANG_REGION])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["listing_count"], 1)
        self.assertEqual(result["listings"][0]["price"], 7500)
        self.assertEqual(result["raw_card_count"], 2)

    def test_fang_official_region_page_keeps_cards_without_card_region_link(self) -> None:
        city_html = '<a href="/cz/house-a0342/">武进区</a>'
        region_html = fang_card(region=None) + fang_card("2", region=None, community="未带区域链接的小区")
        result, _ = self.fang_search([(city_html, FANG_CITY, 200), (region_html, FANG_REGION, 200)])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["listing_count"], 2)
        self.assertTrue(all(item["region_evidence"] == "official_filter_page" for item in result["listings"]))
        self.assertTrue(any("具体地址仍需详情和地图核验" in warning for warning in result["warnings"]))

    def test_fang_region_suffix_is_same_administrative_region(self) -> None:
        html = fang_card(region="0342") + fang_card("2", region="0355")
        rows = scrape_fang.parse_fang_html(html, FANG_REGION, "cz", "wujin")
        self.assertEqual(rows[0]["region_url"], "https://zu.fang.com/cz/house-a0342-b022885/")
        self.assertEqual(rows[1]["region_url"], "https://zu.fang.com/cz/house-a0355-b022885/")
        city_html = '<a href="/cz/house-a0342/">武进区</a>'
        result, _ = self.fang_search([(city_html, FANG_CITY, 200), (html, FANG_REGION, 200)])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["listing_count"], 1)
        self.assertEqual(result["listings"][0]["region_evidence"], "card_region_link")
        self.assertEqual(result["listings"][0]["region_scope"], "administrative_region")
        self.assertEqual(result["listings"][0]["region_scope_name"], "武进")

    def test_fang_conflicting_cards_are_not_reported_as_empty_inventory(self) -> None:
        city_html = '<a href="/cz/house-a0342/">武进区</a>'
        result, _ = self.fang_search([(city_html, FANG_CITY, 200), (fang_card(region="0355"), FANG_REGION, 200)])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["listing_count"], 0)
        self.assertTrue(result["warnings"])

    def test_fang_region_discovery_does_not_select_neighborhood_scope(self) -> None:
        html = '<a href="/cz/house-a0342-b022885/">武进区</a><a href="/cz/house-a0342/">武进区</a>'
        self.assertEqual(scrape_fang._resolve_region_url(html, FANG_CITY, "cz", "wujin"), FANG_REGION)
        self.assertEqual(scrape_fang._resolve_region_url(fang_card(), FANG_CITY, "cz", "wujin"), "")

    def test_fang_does_not_invent_unknown_region_or_expand_scope(self) -> None:
        result, calls = self.fang_search([(fang_card(), FANG_CITY, 200)], area="unknown")
        self.assertEqual(result["status"], "region_unavailable")
        self.assertEqual(result["listing_count"], 0)
        self.assertEqual(len(calls), 1)

    def test_fang_lost_region_and_cross_city_redirects_are_rejected(self) -> None:
        city_html = '<a href="/cz/house-a0342/">武进</a>'
        for final, expected in ((FANG_CITY, "region_mismatch"), ("https://zu.fang.com/tj/house/", "city_mismatch")):
            with self.subTest(final=final):
                result, _ = self.fang_search([(city_html, FANG_CITY, 200), (fang_card(), final, 200)])
                self.assertEqual(result["status"], expected)
                self.assertFalse(result["listings"])

    def test_fang_region_block_preserves_original_verification_target(self) -> None:
        city_html = '<a href="/cz/house-a0342/">武进</a>'
        result, calls = self.fang_search([(city_html, FANG_CITY, 200), (CHALLENGE, FANG_REGION, 200)])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["source_page"], FANG_REGION)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("secret", json.dumps(result))

    def test_verification_page_detects_fang_slider_challenge(self) -> None:
        slider = '<p>请完成以下验证后继续</p><div class="drag_text">拖动滑块验证</div><script src="https://js.soufunimg.com/fangue/libs/check/checkyzm.min.js"></script>'
        self.assertTrue(platform_pages.is_verification_page(slider, FANG_CITY))

    def test_fang_slider_redirect_is_blocked_before_city_check_and_not_retried(self) -> None:
        slider = '<p>请完成下列验证后继续：</p><div class="drag_text">拖动滑块验证</div>'
        result, calls = self.fang_search([(slider, "https://zu.fang.com/verify?secret=private", 200)])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["source_page"], FANG_CITY)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["listings"])
        self.assertNotIn("secret", json.dumps(result))

    def test_normal_gallery_slider_is_not_a_verification_challenge(self) -> None:
        html = fang_card() + '<div class="slider">房源图片</div><script>new GallerySlider()</script>'
        self.assertFalse(platform_pages.is_verification_page(html, FANG_REGION))

    def test_anjuke_optional_metadata_and_full_target_keyword_do_not_drop_cards(self) -> None:
        rows = scrape_anjuke.parse_anjuke_html(anjuke_card() + anjuke_card("2", metadata="合租 2室1厅 20㎡"),
                                             ANJUKE_REGION, "cz", "wujin", "常州大学科教城校区")
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["listing_type"])
        self.assertIsNone(rows[0]["room"])
        self.assertIsNone(rows[0]["area_sqm"])
        self.assertEqual(rows[1]["listing_type"], "合租")
        self.assertEqual(rows[1]["area_sqm"], 20)

    def test_anjuke_invalid_rent_and_cross_city_cards_are_dropped(self) -> None:
        html = anjuke_card(city="tj") + anjuke_card("2", rent="面议") + anjuke_card("3")
        rows = scrape_anjuke.parse_anjuke_html(html, ANJUKE_REGION, "cz")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["detail_url"].endswith("/3.html"))

    def test_anjuke_200_js_challenge_is_blocked_once_not_empty(self) -> None:
        with patch.object(scrape_anjuke, "_fetch_with_session", return_value=(CHALLENGE, ANJUKE_REGION, 200)) as fetch, patch.object(
            scrape_anjuke, "save_page_snapshot"
        ):
            result = scrape_anjuke.scrape_anjuke_status("cz", "wujin")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["source_page"], ANJUKE_REGION)
        fetch.assert_called_once()

    def test_unknown_page_is_not_evidence_of_no_inventory(self) -> None:
        for html, expected in (("<body>unexpected shell</body>", "parse_error"), ("<body>暂无房源</body>", "empty")):
            with self.subTest(expected=expected):
                with patch.object(scrape_anjuke, "_fetch_with_session", return_value=(html, ANJUKE_REGION, 200)), patch.object(
                    scrape_anjuke, "save_page_snapshot"
                ):
                    self.assertEqual(scrape_anjuke.scrape_anjuke_status("cz", "wujin")["status"], expected)
                result, _ = self.fang_search([(html, FANG_CITY, 200)], area="")
                self.assertEqual(result["status"], expected)

    def test_anjuke_lost_region_is_not_marked_as_region_inventory(self) -> None:
        with patch.object(scrape_anjuke, "_fetch_with_session", return_value=(anjuke_card(), "https://cz.zu.anjuke.com/fangyuan/", 200)), patch.object(
            scrape_anjuke, "save_page_snapshot"
        ):
            result = scrape_anjuke.scrape_anjuke_status("cz", "wujin")
        self.assertEqual(result["status"], "region_mismatch")
        self.assertFalse(result["listings"])

    def test_canonical_fang_urls_are_classified_consistently(self) -> None:
        for url, is_list in ((FANG_CITY, True), (FANG_REGION, True), ("https://zu.fang.com/cz/hezu/", True),
                             ("https://zu.fang.com/cz/chuzu/3_123_1.htm", False)):
            with self.subTest(url=url):
                self.assertEqual(batch_detail_tool._is_probable_list_page(url), is_list)
                self.assertEqual(_public_probable_list_url(url), is_list)
                self.assertEqual(candidate_search._platform_url_city(url), "cz")
                self.assertEqual(batch_detail_tool._city_name_from_url(url), "常州")

    def test_projection_distinguishes_parse_failure_from_explicit_empty(self) -> None:
        rows = _safe_platform_statuses({"安居客": "parse_error (0 条)", "房天下": "empty (0 条)"}, [],
                                      search_status="partial")
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["安居客"]["status"], "failed")
        self.assertEqual(by_name["房天下"]["status"], "ok")


class SharedVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        candidate_search._LAST_PLATFORM_SEARCH_STATE.clear()
        batch_detail_tool._DETAIL_VERIFICATION_REQUESTS.clear()
        human_verification_tool._LAST_ATTEMPT_AT.clear()

    def test_58_cli_verification_delegates_to_shared_implementation_and_returns_save_state(self) -> None:
        with patch.object(scrape_58, "create_platform_session", return_value={
            "status": "ok", "session_saved": True, "message": "verified"
        }) as shared:
            result = scrape_58.create_58_session("cz", "wujin", "校区", "local-state.json")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["session_saved"])
        self.assertEqual(shared.call_args.args[0], "58")

    def test_aggregator_keeps_adapter_diagnostics_and_independent_session_inputs(self) -> None:
        p58 = {"status": "empty", "message": "empty", "listings": [], "source_page": "https://cz.zf.58.com/wujin/"}
        pa = {"status": "blocked", "message": "blocked", "listings": [], "source_page": ANJUKE_REGION}
        pf = {"status": "ok", "message": "ok", "listings": scrape_fang.parse_fang_html(fang_card(), FANG_REGION, "cz"),
              "source_page": FANG_REGION, "raw_card_count": 1, "parsed_count": 1}
        with tempfile.TemporaryDirectory() as directory, patch.object(scrape_all, "scrape_58_status", return_value=p58) as v58, patch.object(
            scrape_all, "scrape_anjuke_status", return_value=pa
        ) as va, patch.object(scrape_all, "scrape_fang", return_value=pf) as vf, patch.object(scrape_all.time, "sleep"):
            result = scrape_all.scrape_all("cz", "wujin", status=True, output=str(Path(directory) / "result.json"),
                                          session_file="58.json", platform_sessions={"anjuke": "anjuke.json", "fang": "fang.json"})
        self.assertEqual(result["listing_count"], 1)
        self.assertEqual(result["listings"][0]["detail_url"], "https://zu.fang.com/cz/chuzu/3_1_1.htm")
        self.assertEqual(result["platform_diagnostics"]["房天下"]["raw_card_count"], 1)
        self.assertEqual(result["platform_diagnostics"]["安居客"]["source_page"], ANJUKE_REGION)
        self.assertEqual([v.call_args.kwargs["session_file"] for v in (v58, va, vf)], ["58.json", "anjuke.json", "fang.json"])
        self.assertTrue(result["blocked_hints"])

    def test_search_verification_opens_blocked_adapter_page_and_platforms_are_independent(self) -> None:
        targets = {"58": "https://cz.zf.58.com/wujin/", "anjuke": ANJUKE_REGION, "fang": FANG_REGION}
        for platform, target in targets.items():
            candidate_search._record_platform_search_state(platform, blocked=True, city="cz", area="wujin",
                                                           keyword="校区", target_url=target)
        with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "live"}), patch.object(
            human_verification_tool, "_verify_58_session", return_value={"status": "ok"}
        ) as v58, patch.object(human_verification_tool, "_verify_anjuke_session", return_value={"status": "ok"}) as va, patch.object(
            human_verification_tool, "_verify_fang_session", return_value={"status": "ok"}
        ) as vf:
            for platform, verifier in (("58", v58), ("anjuke", va), ("fang", vf)):
                result = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                    "platform": platform, "city": "cz", "area": "wujin", "keyword": "校区",
                }))
                self.assertEqual(result["status"], "ok")
                self.assertEqual(verifier.call_args.args[-1], targets[platform])
            self.assertEqual(len({v.call_args.args[3] for v in (v58, va, vf)}), 3)
            self.assertEqual(len(human_verification_tool._LAST_ATTEMPT_AT), 3)

    def test_conflicting_search_target_does_not_consume_authorization(self) -> None:
        candidate_search._record_platform_search_state("fang", blocked=True, city="cz", area="wujin",
                                                       keyword="", target_url=FANG_REGION)
        with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "live"}), patch.object(human_verification_tool, "_verify_fang_session") as verify:
            result = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                "platform": "fang", "city": "cz", "area": "wujin", "target_url": FANG_CITY,
            }))
        self.assertEqual(result["status"], "rejected")
        self.assertTrue(candidate_search._LAST_PLATFORM_SEARCH_STATE["fang"]["blocked"])
        verify.assert_not_called()

    def test_expired_or_wrong_query_search_authorization_cannot_open_browser(self) -> None:
        candidate_search._record_platform_search_state("anjuke", blocked=True, city="cz", area="wujin",
                                                       keyword="", target_url=ANJUKE_REGION)
        self.assertFalse(candidate_search.consume_platform_verification_request("anjuke", "nj", "wujin", ""))
        candidate_search._LAST_PLATFORM_SEARCH_STATE["anjuke"]["recorded_at"] -= 301
        with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "live"}), patch.object(human_verification_tool, "_verify_anjuke_session") as verify:
            result = json.loads(human_verification_tool.human_verify_rental_platform.invoke({
                "platform": "anjuke", "city": "cz", "area": "wujin",
            }))
        self.assertEqual(result["status"], "not_needed")
        verify.assert_not_called()

    def test_all_platform_detail_authorizations_and_cooldowns_are_independent(self) -> None:
        targets = {"58": "https://cz.58.com/zufang/one.shtml", "anjuke": "https://cz.zu.anjuke.com/fangyuan/1.html",
                   "fang": "https://zu.fang.com/cz/chuzu/3_1_1.htm"}
        for platform, target in targets.items():
            batch_detail_tool._record_detail_verification_request(platform, target)
            with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "live"}), patch.object(
                human_verification_tool, f"_verify_{platform}_session", return_value={"status": "ok"}
            ) as verify:
                args = {"platform": platform, "phase": "detail", "target_url": target}
                first = json.loads(human_verification_tool.human_verify_rental_platform.invoke(args))
                second = json.loads(human_verification_tool.human_verify_rental_platform.invoke(args))
            self.assertEqual(first["status"], "ok")
            self.assertEqual(second["status"], "cooldown")
            verify.assert_called_once()

    def test_completion_requires_valid_platform_city_and_page_evidence(self) -> None:
        self.assertFalse(platform_pages.verification_ready("anjuke", CHALLENGE, ANJUKE_REGION))
        self.assertFalse(platform_pages.verification_ready("fang", "<body>首页文字</body>" * 100, FANG_CITY))
        self.assertTrue(platform_pages.verification_ready("fang", fang_card(), FANG_REGION, expected_city="cz"))
        self.assertFalse(platform_pages.verification_ready("fang", fang_card(), "https://zu.fang.com/tj/house/", expected_city="cz"))
        detail = "<body>" + "公开房源说明 " * 20 + "租金 1200 元/月</body>"
        target = "https://zu.fang.com/cz/chuzu/3_1_1.htm"
        self.assertTrue(platform_pages.verification_ready("fang", detail, target, phase="detail", target_url=target))
        self.assertFalse(platform_pages.verification_ready("fang", detail, FANG_CITY, phase="detail", target_url=target))
        self.assertFalse(platform_pages.allowed_platform_url("fang", "https://user:secret@zu.fang.com/cz/house/"))

    def test_shared_browser_verification_saves_only_after_challenge_and_always_closes(self) -> None:
        from playwright import sync_api

        browser = MagicMock()
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = ANJUKE_REGION
        page.content.side_effect = [CHALLENGE, anjuke_card()]
        with tempfile.TemporaryDirectory() as directory, patch.object(sync_api, "sync_playwright") as playwright, patch.object(
            scrape_58, "_launch_browser", return_value=browser
        ), patch.object(platform_pages.time, "monotonic", side_effect=[0, 1, 2]):
            result = platform_pages.create_platform_session("anjuke", ANJUKE_REGION, str(Path(directory) / "state.json"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(page.content.call_count, 2)
        context.storage_state.assert_called_once()
        browser.close.assert_called_once()
        playwright.assert_called_once()

    def test_shared_browser_read_reuses_given_state_without_network_in_test(self) -> None:
        from playwright import sync_api

        browser = MagicMock()
        page = browser.new_context.return_value.new_page.return_value
        page.content.return_value = fang_card()
        page.url = FANG_REGION
        page.goto.return_value.status = 200
        with tempfile.TemporaryDirectory() as directory, patch.object(sync_api, "sync_playwright"), patch.object(
            scrape_58, "_launch_browser", return_value=browser
        ):
            state = Path(directory) / "state.json"
            state.touch()
            result = platform_pages.read_public_page(FANG_REGION, str(state), delay_seconds=0)
            self.assertEqual(browser.new_context.call_args.kwargs["storage_state"], str(state))
        self.assertEqual(result[1:], (FANG_REGION, 200))
        browser.close.assert_called_once()

    def test_shared_verification_timeout_does_not_save_and_closes_browser(self) -> None:
        from playwright import sync_api

        browser = MagicMock()
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = ANJUKE_REGION
        page.content.return_value = CHALLENGE
        with tempfile.TemporaryDirectory() as directory, patch.object(sync_api, "sync_playwright"), patch.object(
            scrape_58, "_launch_browser", return_value=browser
        ), patch.object(platform_pages.time, "monotonic", side_effect=[0, 1, 301]):
            result = platform_pages.create_platform_session("anjuke", ANJUKE_REGION, str(Path(directory) / "state.json"))
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["session_saved"])
        context.storage_state.assert_not_called()
        browser.close.assert_called_once()

    def test_shared_verification_exception_closes_browser_without_leaking_challenge_url(self) -> None:
        from playwright import sync_api

        browser = MagicMock()
        page = browser.new_context.return_value.new_page.return_value
        page.wait_for_timeout.side_effect = RuntimeError("https://callback.58.com/antibot/verifycode?secret=private")
        with tempfile.TemporaryDirectory() as directory, patch.object(sync_api, "sync_playwright"), patch.object(
            scrape_58, "_launch_browser", return_value=browser
        ):
            result = platform_pages.create_platform_session("anjuke", ANJUKE_REGION, str(Path(directory) / "state.json"))
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret", json.dumps(result))
        browser.close.assert_called_once()

    def test_search_tool_preserves_diagnostics_and_does_not_call_empty_blocked_inventory_ok(self) -> None:
        result = {"listings": [], "platforms": {"58同城": "empty (0 条)", "安居客": "blocked (0 条)", "房天下": "parse_error (0 条)"},
                  "platform_diagnostics": {"安居客": {"status": "blocked", "source_page": ANJUKE_REGION}}, "blocked_hints": []}
        with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "live"}), patch.object(scrape_all, "scrape_all", return_value=result):
            payload = json.loads(candidate_search.search_rental_candidates.invoke({"city": "cz", "area": "wujin"}))
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["human_verification_platforms"], ["安居客"])
        self.assertEqual(candidate_search.platform_verification_target("anjuke"), ANJUKE_REGION)


if __name__ == "__main__":
    unittest.main()

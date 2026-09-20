from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core.candidate_search import _area_code, _compact, search_rental_candidates


SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "rental-scraper"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import scrape_all
from scrape_all import _balanced_platform_limit
from scrape_58 import _58_url_matches_city, parse_58_html


def _card(url: str, title: str = "测试房源") -> str:
    return f"""
<a class="house-link" href="{url}">
  <div class="house-title">{title}</div>
  <div class="house-type">1室1厅 36㎡</div>
  <div class="price-val">2000 元/月</div>
</a>
"""


class ScraperCityBoundaryTests(unittest.TestCase):
    def test_normalizer_does_not_promote_source_list_to_detail_url(self) -> None:
        listing = scrape_all._normalize_listing(
            {
                "platform": "房天下",
                "title": "列表候选",
                "price": 1200,
                "url": "https://zu.fang.com/cz/house/",
            },
            "2026-01-01T00:00:00+08:00",
        )

        self.assertNotIn("detail_url", listing)
        self.assertEqual(listing["source_page"], "https://zu.fang.com/cz/house/")

        listing = scrape_all._normalize_listing(
            {
                "platform": "房天下",
                "title": "列表候选",
                "price": 1200,
                "url": "https://zu.fang.com/cz/house/",
                "detail_url": "https://zu.fang.com/cz/chuzu/old.htm",
                "url_type": "source_list",
            },
            "2026-01-01T00:00:00+08:00",
        )
        self.assertNotIn("detail_url", listing)

    def test_compact_clears_stale_detail_url_when_source_is_a_list(self) -> None:
        compact = _compact({
            "platform": "房天下",
            "title": "列表候选",
            "price": 1200,
            "url": "https://zu.fang.com/cz/house/",
            "detail_url": "https://zu.fang.com/cz/chuzu/old.htm",
            "url_type": "source_list",
        })

        self.assertIsNone(compact["detail_url"])
        self.assertEqual(compact["url_type"], "source_list")
        self.assertEqual(compact["listing_url"], "https://zu.fang.com/cz/house/")

    def test_merged_pool_is_balanced_across_platforms(self) -> None:
        listings = []
        for platform in ("58同城", "安居客", "房天下"):
            listings.extend({
                "platform": platform,
                "title": f"{platform}-{index}",
                "price": 3000 - index,
            } for index in range(25))

        selected = _balanced_platform_limit(listings, 60)

        self.assertEqual(len(selected), 60)
        self.assertEqual([item["platform"] for item in selected[:3]], ["58同城", "安居客", "房天下"])
        self.assertEqual(
            {platform: sum(item["platform"] == platform for item in selected) for platform in ("58同城", "安居客", "房天下")},
            {"58同城": 20, "安居客": 20, "房天下": 20},
        )
        for platform in ("58同城", "安居客", "房天下"):
            prices = [item["price"] for item in selected if item["platform"] == platform]
            self.assertEqual(prices, sorted(prices))

    def test_nanjing_gulou_uses_platform_specific_area_slug(self) -> None:
        self.assertEqual(_area_code("鼓楼区", "南京市"), "gulouqu")
        self.assertEqual(_area_code("gulou", "nj"), "gulouqu")

    def test_58_parser_drops_cross_city_detail_links(self) -> None:
        html = _card("https://tj.58.com/zufang/tianjin.shtml", "天津房源") + _card(
            "https://nj.58.com/zufang/nanjing.shtml", "南京房源"
        )

        listings = parse_58_html(html, "https://nj.zf.58.com/gulouqu/", "nj", "gulouqu")

        self.assertEqual([item["title"] for item in listings], ["南京房源"])
        self.assertEqual(listings[0]["listing_type"], "整租")
        self.assertFalse(_58_url_matches_city("https://tj.zf.58.com/gulou/", "nj"))

    def test_agent_tool_defensively_rejects_cross_city_candidates(self) -> None:
        platform_result = {
            "listings": [
                {
                    "title": "天津房源", "price": 1900, "room": "1室1厅", "area_sqm": 36,
                    "platform": "58同城", "detail_url": "https://tj.58.com/zufang/tianjin.shtml",
                },
                {
                    "title": "南京房源", "price": 2100, "room": "1室1厅", "area_sqm": 40,
                    "platform": "58同城", "detail_url": "https://nj.58.com/zufang/nanjing.shtml",
                },
            ],
            "platforms": {"58同城": "ok (2 条)"},
            "blocked_hints": [],
            "warnings": [],
        }
        with patch.dict(os.environ, {"RENTAL_DEMO_MODE": "live"}), patch.object(
            scrape_all, "scrape_all", return_value=platform_result
        ) as scrape_mock:
            result = json.loads(search_rental_candidates.invoke({
                "city": "南京市", "area": "鼓楼区", "keyword": "南京大学鼓楼校区",
            }))

        self.assertEqual(result["listing_count"], 1)
        self.assertEqual(result["listings"][0]["title"], "南京房源")
        self.assertEqual(result["detail_urls"], ["https://nj.58.com/zufang/nanjing.shtml"])
        self.assertTrue(result["platforms"]["58同城"].startswith("partial"))
        self.assertIn("已丢弃 1 条跨城市链接", result["platforms"]["58同城"])
        self.assertTrue(result["warnings"])
        self.assertEqual(scrape_mock.call_args.kwargs["max_listings"], 60)
        self.assertEqual(scrape_mock.call_args.kwargs["per_platform_limit"], 30)


if __name__ == "__main__":
    unittest.main()

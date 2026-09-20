from __future__ import annotations

import unittest

from agent_core.ranking import (
    SearchCriteria,
    evaluate_listing,
    parse_search_criteria,
    rank_listings,
    update_search_criteria,
)


class RankingTests(unittest.TestCase):
    def test_budget_parser_does_not_capture_other_numeric_limits(self) -> None:
        cases = (
            ("通勤不超过30分钟，预算3000元", 3000),
            ("预算3000元，步行不超过20分钟", 3000),
            ("面积不超过50㎡，预算3000元", 3000),
            ("距离目标3公里，预算3000元", 3000),
        )

        for text, expected_budget in cases:
            with self.subTest(text=text):
                criteria = parse_search_criteria(text)
                self.assertEqual(criteria.budget_max, expected_budget)

    def test_budget_parser_leaves_unlabeled_non_budget_limits_unset(self) -> None:
        for text in ("通勤不超过30分钟", "面积不超过50㎡", "距离目标3公里"):
            with self.subTest(text=text):
                self.assertIsNone(parse_search_criteria(text).budget_max)

    def test_parses_common_rental_constraints(self) -> None:
        criteria = parse_search_criteria("预算不超过2500元，整租一室，面积不小于30㎡，公交通勤45分钟以内，距离目标5公里")

        self.assertEqual(criteria.budget_max, 2500)
        self.assertEqual(criteria.layout, "whole")
        self.assertEqual(criteria.room_count, 1)
        self.assertEqual(criteria.area_min_sqm, 30)
        self.assertEqual(criteria.commute_max_minutes, 45)
        self.assertEqual(criteria.radius_km, 5)
        self.assertEqual(criteria.commute_mode, "transit")

    def test_hard_filter_and_ranking_keep_unknown_candidates_visible(self) -> None:
        criteria = SearchCriteria(
            budget_max=2500,
            layout="whole",
            room_count=1,
            radius_km=5,
            commute_max_minutes=45,
        )
        listings = [
            {
                "id": "unknown",
                "rent": 1800,
                "room": "1室1厅",
            },
            {
                "id": "excluded",
                "rent": 3200,
                "room": "1室1厅",
                "distance_meters": 1000,
                "commute_duration_seconds": 1200,
            },
            {
                "id": "passed",
                "rent": 1600,
                "room": "1室1厅（整租）",
                "distance_meters": 1000,
                "commute_duration_seconds": 1200,
            },
        ]

        ranked = rank_listings(listings, criteria)

        self.assertEqual([item["id"] for item in ranked], ["passed", "unknown", "excluded"])
        self.assertEqual(ranked[0]["filter_status"], "passed")
        self.assertEqual(ranked[1]["filter_status"], "unknown")
        self.assertEqual(ranked[2]["filter_status"], "excluded")
        self.assertTrue(ranked[0]["ranking_reasons"])
        self.assertIn("整租/合租类型未说明", ranked[1]["filter_reasons"])
        self.assertIn("超过预算上限", "".join(ranked[2]["filter_reasons"]))

    def test_evaluate_listing_does_not_treat_missing_data_as_failure(self) -> None:
        result = evaluate_listing({"rent": 1800, "room": "1室1厅"}, SearchCriteria(budget_max=2500, layout="whole"))

        self.assertEqual(result["filter_status"], "unknown")
        self.assertIsNone(result["hard_filter_pass"])
        self.assertIn("整租/合租类型未说明", result["filter_reasons"])

    def test_preferred_layout_is_ranked_but_not_hard_filtered(self) -> None:
        criteria = parse_search_criteria("预算不超过2500元，优先整租一室")

        self.assertEqual(criteria.budget_max, 2500)
        self.assertIsNone(criteria.layout)
        self.assertIsNone(criteria.room_count)
        self.assertEqual(criteria.preferred_layout, "whole")
        self.assertEqual(criteria.preferred_room_count, 1)

        ranked = rank_listings([
            {"id": "shared", "rent": 1000, "listingType": "合租", "room": "3室1厅"},
            {"id": "whole", "rent": 1800, "listingType": "整租", "room": "1室1厅"},
        ], criteria)
        self.assertEqual([item["id"] for item in ranked], ["whole", "shared"])
        self.assertTrue(all(item["filter_status"] == "passed" for item in ranked))

    def test_conversational_update_keeps_budget_and_relaxes_layout(self) -> None:
        criteria = update_search_criteria(None, "预算不超过2500元，优先整租一室")
        criteria = update_search_criteria(criteria, "1")
        criteria = update_search_criteria(criteria, "不要求整租的，都可以")

        self.assertEqual(criteria.budget_max, 2500)
        self.assertIsNone(criteria.layout)
        self.assertIsNone(criteria.room_count)
        self.assertIsNone(criteria.preferred_layout)
        self.assertIsNone(criteria.preferred_room_count)


if __name__ == "__main__":
    unittest.main()

import unittest
from tiktok_product_hunter import (
    is_fitness,
    matches_major_brand,
    parse_price_number,
    product_type_token,
    score_product,
    diversify_top_n,
)


class TestTikTokProductHunter(unittest.TestCase):
    def test_is_fitness_filters_accessories(self):
        self.assertTrue(is_fitness("Pure Creatine Monohydrate Powder 500g"))
        self.assertTrue(is_fitness("Organic Ashwagandha Capsules 120 Count"))
        self.assertFalse(is_fitness("Protein Shaker Bottle with Wire Whisk Ball"))
        self.assertFalse(is_fitness("Weekly Pill Organizer Box for Supplements"))

    def test_major_brand_detection(self):
        self.assertEqual(matches_major_brand("Optimum Nutrition Gold Standard Whey"), "optimum nutrition")
        self.assertIsNone(matches_major_brand("NovaPure Grass Fed Collagen Peptides"))

    def test_parse_price_and_scoring(self):
        self.assertEqual(parse_price_number("$24.99"), 24.99)
        self.assertEqual(parse_price_number("£19.50"), 19.50)
        p = {
            "name": "Pure Creatine Monohydrate Powder 500g",
            "rating": 4.6,
            "review_count": 850,
            "bought_past_month": "2K+ bought in past month",
            "price_value": 24.99,
            "ingestion_hits": 4,
        }
        sc = score_product(p, ["Creatine Monohydrate Powder"], {"ddg_tiktok_hits": 3})
        self.assertGreater(sc["total"], 70.0)

    def test_diversify_top_n(self):
        items = [
            {"name": "Collagen Peptides A", "score": {"total": 90}},
            {"name": "Collagen Peptides B", "score": {"total": 89}},
            {"name": "Creatine Monohydrate", "score": {"total": 85}},
            {"name": "Magnesium Glycinate", "score": {"total": 80}},
        ]
        top3 = diversify_top_n(items, 3)
        types = {product_type_token(x["name"]) for x in top3}
        self.assertEqual(types, {"collagen", "creatine", "magnesium"})


if __name__ == "__main__":
    unittest.main()

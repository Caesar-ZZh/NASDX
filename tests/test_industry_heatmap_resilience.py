from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
STOCK_SERVER = ROOT / "server" / "stock"
if str(STOCK_SERVER) not in sys.path:
    sys.path.insert(0, str(STOCK_SERVER))

import astock  # noqa: E402


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def json(self) -> dict:
        return self.payload


class IndustryHeatmapResilienceTests(unittest.TestCase):
    def test_industry_ranking_falls_back_when_primary_host_is_empty(self):
        item = {
            "f3": 1.25,
            "f12": "BK0001",
            "f14": "测试板块",
            "f104": 8,
            "f105": 2,
        }
        responses = [
            _Response({"data": None}),
            _Response({"data": {"total": 1, "diff": [item]}}),
        ]

        with patch.object(astock, "em_get", side_effect=responses) as request:
            result = astock.industry_comparison(top_n=5)

        self.assertEqual(2, request.call_count)
        self.assertEqual(1, result["total"])
        self.assertEqual("测试板块", result["top"][0]["name"])

    def test_industry_endpoint_does_not_cache_empty_results(self):
        source = (STOCK_SERVER / "base_app.py").read_text(encoding="utf-8")

        self.assertIn('if data.get("top") and data.get("bottom"):', source)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class RuntimePathsContractsTest(unittest.TestCase):
    def test_runtime_helpers_honor_report_and_data_env(self):
        from nasdx.paths import get_market_data_dir, get_reports_dir, get_runtime_dir

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_dir = root / "runtime"
            reports_dir = root / "custom_reports"
            data_dir = root / "custom_data"

            with patch.dict(
                os.environ,
                {
                    "NASDX_RUNTIME_DIR": str(runtime_dir),
                    "NASDX_REPORTS_DIR": str(reports_dir),
                    "NASDX_DATA_DIR": str(data_dir),
                },
            ):
                self.assertEqual(runtime_dir.resolve(), get_runtime_dir(create=True))
                self.assertEqual(reports_dir.resolve(), get_reports_dir(create=True))
                self.assertEqual(data_dir.resolve(), get_market_data_dir(create=True))

    def test_data_loader_reads_runtime_market_snapshot(self):
        from nasdx.data_loader import load_latest_data

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            snapshot = data_dir / "stock_data_20260708.json"
            snapshot.write_text(
                json.dumps({"date": "20260708", "sectors": []}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"NASDX_DATA_DIR": str(data_dir)}):
                self.assertEqual("20260708", load_latest_data()["date"])

    def test_portfolio_plan_saves_to_runtime_reports_dir(self):
        from nasdx.portfolio import save_portfolio_plan

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports_dir = root / "reports"
            db_path = root / "history.db"
            plan = {
                "generated_at": "2026-07-08T10:00:00",
                "risk_profile_label": "均衡",
                "posture": "结构性轮动",
                "allocation": {
                    "max_total": "35%-60%",
                    "etf_budget": "20%-35%",
                    "stock_budget": "10%-25%",
                    "single_stock_cap": "5%-10%",
                    "cash_buffer": "40%-65%",
                    "mode": "测试",
                },
                "core_candidates": [],
                "satellite_candidates": [],
                "watchlist": [],
                "trim_or_avoid": [],
                "next_actions": [],
                "future_scenarios": [],
                "decision_rules": [],
                "monitoring_checklist": [],
                "review_cadence": [],
                "data_quality": {},
                "disclaimer": "测试",
            }

            with patch.dict(
                os.environ,
                {
                    "NASDX_REPORTS_DIR": str(reports_dir),
                    "NASDX_HISTORY_DB": str(db_path),
                },
            ):
                paths = save_portfolio_plan(plan)

            self.assertEqual(reports_dir.resolve(), Path(paths["json"]).parent.resolve())
            self.assertTrue((reports_dir / "portfolio_plan_latest.json").exists())
            self.assertTrue(db_path.exists())

    def test_report_history_lists_runtime_reports_and_skips_latest_aliases(self):
        from nasdx.report_history import list_report_history

        with tempfile.TemporaryDirectory() as tmp:
            reports_dir = Path(tmp)
            dated = reports_dir / "investment_brief_20260708_1000.json"
            latest = reports_dir / "investment_brief_latest.json"
            dated.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-07-08T10:00:00",
                        "primary_bias": "ETF主线优先。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            latest.write_text(dated.read_text(encoding="utf-8"), encoding="utf-8")

            rows = list_report_history(reports_dir=reports_dir)

            self.assertEqual(1, len(rows))
            self.assertEqual("最终简报", rows[0]["label"])
            self.assertEqual(str(dated), rows[0]["path"])


if __name__ == "__main__":
    unittest.main()

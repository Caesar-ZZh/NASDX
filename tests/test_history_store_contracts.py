import os
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


class HistoryStoreContractsTest(unittest.TestCase):
    def test_specialized_write_rolls_back_when_second_insert_fails(self):
        from nasdx.history_store import record_daily_scan

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nasdx_history.db"
            with patch("nasdx.history_store._insert_specialized_row", side_effect=sqlite3.OperationalError("injected")):
                with self.assertRaises(sqlite3.OperationalError):
                    record_daily_scan("etf50", "20260713", {"datetime": "2026-07-13"}, db_path=db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(0, conn.execute("select count(*) from artifacts").fetchone()[0])
                self.assertEqual(0, conn.execute("select count(*) from daily_scans").fetchone()[0])

    def test_specialized_tables_reference_one_canonical_payload(self):
        from nasdx.history_store import init_history_db, record_report_history

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nasdx_history.db"
            init_history_db(db_path)
            record_report_history("603501", "20260713", {"final_signal": "bullish"}, db_path=db_path)
            with closing(sqlite3.connect(db_path)) as conn:
                columns = {row[1] for row in conn.execute("pragma table_info(report_history)")}
                foreign_keys = conn.execute("pragma foreign_key_list(report_history)").fetchall()
                self.assertIn("artifact_id", columns)
                self.assertNotIn("payload_json", columns)
                self.assertTrue(any(row[2] == "artifacts" and row[3] == "artifact_id" for row in foreign_keys))
                self.assertEqual(1, conn.execute("select count(*) from artifacts").fetchone()[0])

    def test_concurrent_writers_complete_without_orphans(self):
        from nasdx.history_store import audit_history_consistency, record_daily_scan

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nasdx_history.db"
            def write(index):
                return record_daily_scan("etf50", f"202607{index:02d}", {"index": index}, db_path=db_path)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(write, range(1, 13)))
            audit = audit_history_consistency(db_path)
            self.assertEqual(12, audit["artifact_count"])
            self.assertEqual([], audit["orphans"])

    def test_legacy_database_migrates_without_losing_payload(self):
        from nasdx.history_store import init_history_db, latest_artifact

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            payload = json.dumps({"final_signal": "bullish"}, ensure_ascii=False)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    create table report_history (
                        id integer primary key autoincrement, stock_code text not null,
                        report_date text not null, generated_at text, source_path text,
                        payload_json text not null, payload_hash text not null, created_at text not null
                    );
                    """
                )
                conn.execute(
                    "insert into report_history (stock_code, report_date, generated_at, source_path, payload_json, payload_hash, created_at) values (?, ?, ?, ?, ?, ?, ?)",
                    ("603501", "20260713", "2026-07-13", "reports/legacy.json", payload, "legacy-hash", "2026-07-13"),
                )
                conn.commit()

            init_history_db(db_path)
            latest = latest_artifact("report_history", "603501", db_path=db_path)
            self.assertEqual("bullish", latest["payload"]["final_signal"])
            with closing(sqlite3.connect(db_path)) as conn:
                self.assertEqual(1, conn.execute("select count(*) from report_history").fetchone()[0])
                self.assertNotIn("payload_json", {row[1] for row in conn.execute("pragma table_info(report_history)")})
    def test_records_generic_artifact_and_reads_latest_payload(self):
        from nasdx.history_store import artifact_counts, init_history_db, latest_artifact, record_artifact

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nasdx_history.db"
            init_history_db(db_path)

            first = record_artifact(
                artifact_type="investment_brief",
                artifact_key="latest",
                payload={"generated_at": "2026-06-18T09:00:00", "action_gate": "normal"},
                source_path="reports/investment_brief_20260618_0900.json",
                generated_at="2026-06-18T09:00:00",
                db_path=db_path,
            )
            second = record_artifact(
                artifact_type="investment_brief",
                artifact_key="latest",
                payload={"generated_at": "2026-06-18T10:00:00", "action_gate": "position_cap"},
                source_path="reports/investment_brief_20260618_1000.json",
                generated_at="2026-06-18T10:00:00",
                db_path=db_path,
            )

            self.assertNotEqual(first["payload_hash"], second["payload_hash"])
            self.assertEqual(artifact_counts(db_path)["investment_brief"], 2)
            latest = latest_artifact("investment_brief", "latest", db_path=db_path)
            self.assertEqual(latest["payload"]["action_gate"], "position_cap")
            self.assertEqual(latest["source_path"], "reports/investment_brief_20260618_1000.json")

    def test_records_reports_scans_and_etf_pools_with_named_tables(self):
        from nasdx.history_store import (
            init_history_db,
            record_daily_scan,
            record_etf_pool,
            record_report_history,
        )

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nasdx_history.db"
            init_history_db(db_path)

            report_row = record_report_history(
                stock_code="603501",
                report_date="20260618",
                payload={"final_signal": "bullish"},
                source_path="reports/report_603501_20260618.json",
                db_path=db_path,
            )
            scan_row = record_daily_scan(
                scan_type="stocks60",
                scan_date="20260618",
                payload={"valid_count": 58, "expected_total": 60},
                source_path="reports/stocks60_20260618_1500.json",
                db_path=db_path,
            )
            pool_row = record_etf_pool(
                pool_name="etf50",
                payload={"etfs": [{"code": "510300", "name": "沪深300ETF"}]},
                source_path="etf50_pool.json",
                db_path=db_path,
            )

            self.assertGreater(report_row["id"], 0)
            self.assertGreater(scan_row["id"], 0)
            self.assertGreater(pool_row["id"], 0)

            with closing(sqlite3.connect(db_path)) as conn:
                report_count = conn.execute("select count(*) from report_history").fetchone()[0]
                scan_count = conn.execute("select count(*) from daily_scans").fetchone()[0]
                pool_count = conn.execute("select count(*) from etf_pools").fetchone()[0]

            self.assertEqual(report_count, 1)
            self.assertEqual(scan_count, 1)
            self.assertEqual(pool_count, 1)

    def test_save_investment_brief_records_sqlite_history(self):
        from nasdx.history_store import latest_artifact
        from nasdx.investment_brief import save_investment_brief

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "nasdx_history.db"
            out_dir = root / "reports"
            brief = {
                "generated_at": "2026-06-18T11:00:00",
                "risk_profile": "balanced",
                "action_gate": "normal",
                "posture": "顺势偏多",
                "primary_bias": "ETF主线优先。",
                "markdown": "# NASDX 最终投资简报\n",
            }

            with patch.dict(os.environ, {"NASDX_HISTORY_DB": str(db_path)}):
                paths = save_investment_brief(brief, output_dir=out_dir)

            latest = latest_artifact("investment_brief", "latest", db_path=db_path)
            self.assertEqual(latest["payload"]["action_gate"], "normal")
            self.assertEqual(latest["generated_at"], "2026-06-18T11:00:00")
            self.assertEqual(latest["source_path"], Path(paths["json"]).as_posix())


if __name__ == "__main__":
    unittest.main()

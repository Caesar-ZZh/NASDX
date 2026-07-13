import csv
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


def _valid_brief():
    return {
        "generated_at": "2026-07-13T10:00:00",
        "risk_profile": "balanced",
        "candidate_audits": [{"candidate": "510300 沪深300ETF"}],
        "execution_queue": [{"action": "观察"}],
        "external_review_pack": [],
        "source_files": {},
    }


def _valid_plan():
    return {
        "generated_at": "2026-07-13T09:59:00",
        "source_files": {},
    }


class ReviewSnapshotSecurityContractsTest(unittest.TestCase):
    def test_csv_cells_neutralize_formula_prefixes_after_hidden_whitespace(self):
        from nasdx.review_snapshot import _safe_csv_cell

        for value in ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t =HYPERLINK(\"x\")"]:
            self.assertTrue(_safe_csv_cell(value).startswith("'"), value)
        self.assertEqual("510300", _safe_csv_cell("510300"))
        self.assertEqual("https://example.com", _safe_csv_cell("https://example.com"))

    def test_every_csv_member_is_spreadsheet_safe(self):
        from nasdx.review_snapshot import _external_review_csv, _table_csv

        malicious = '=HYPERLINK("https://example.invalid","click")'
        outputs = [
            _table_csv([{"candidate": malicious, "manual_checks": [malicious]}], ["candidate", "manual_checks"]),
            _table_csv([{"action": malicious}], ["action"]),
            _external_review_csv(
                [{"candidate": malicious, "source_links": [{"label": malicious, "url": malicious}]}]
            ),
        ]
        for text in outputs:
            for row in csv.reader(io.StringIO(text)):
                for cell in row:
                    self.assertFalse(cell.lstrip("\t\r\n ").startswith(("=", "+", "-", "@")), cell)

    def test_required_json_is_validated_before_final_zip_is_created(self):
        from nasdx.review_snapshot import SnapshotValidationError, build_review_snapshot

        invalid_cases = [None, "{broken", "[]", json.dumps({"generated_at": "2026-07-13"})]
        for raw in invalid_cases:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                reports = root / "reports"
                snapshots = root / "snapshots"
                reports.mkdir()
                if raw is not None:
                    (reports / "investment_brief_latest.json").write_text(raw, encoding="utf-8")
                (reports / "portfolio_plan_latest.json").write_text(
                    json.dumps(_valid_plan(), ensure_ascii=False), encoding="utf-8"
                )
                with (
                    patch("nasdx.review_snapshot.get_reports_dir", return_value=reports),
                    patch("nasdx.review_snapshot.build_recommendation_tracker", return_value={}),
                    patch("nasdx.review_snapshot.build_recommendation_review", return_value={}),
                ):
                    with self.assertRaises(SnapshotValidationError):
                        build_review_snapshot(output_dir=snapshots)
                self.assertEqual([], list(snapshots.glob("*.zip")) if snapshots.exists() else [])

    def test_write_failure_leaves_no_final_or_temporary_zip(self):
        from nasdx.review_snapshot import build_review_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            snapshots = root / "snapshots"
            reports.mkdir()
            (reports / "investment_brief_latest.json").write_text(
                json.dumps(_valid_brief(), ensure_ascii=False), encoding="utf-8"
            )
            (reports / "portfolio_plan_latest.json").write_text(
                json.dumps(_valid_plan(), ensure_ascii=False), encoding="utf-8"
            )
            with (
                patch("nasdx.review_snapshot.get_reports_dir", return_value=reports),
                patch("nasdx.review_snapshot.build_recommendation_tracker", return_value={}),
                patch("nasdx.review_snapshot.build_recommendation_review", return_value={}),
                patch.object(zipfile.ZipFile, "writestr", side_effect=OSError("disk full")),
            ):
                with self.assertRaises(OSError):
                    build_review_snapshot(output_dir=snapshots)
            self.assertEqual([], list(snapshots.glob("*")))

    def test_valid_snapshot_has_versioned_valid_manifest(self):
        from nasdx.review_snapshot import build_review_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            (reports / "investment_brief_latest.json").write_text(
                json.dumps(_valid_brief(), ensure_ascii=False), encoding="utf-8"
            )
            (reports / "portfolio_plan_latest.json").write_text(
                json.dumps(_valid_plan(), ensure_ascii=False), encoding="utf-8"
            )
            with (
                patch("nasdx.review_snapshot.get_reports_dir", return_value=reports),
                patch("nasdx.review_snapshot.build_recommendation_tracker", return_value={}),
                patch("nasdx.review_snapshot.build_recommendation_review", return_value={}),
            ):
                result = build_review_snapshot(output_dir=root / "snapshots")
            self.assertEqual("nasdx_review_snapshot.v2", result["manifest"]["schema"])
            self.assertEqual("valid", result["manifest"]["validation_status"])
            self.assertTrue(Path(result["zip_path"]).exists())

    def test_cli_returns_nonzero_without_success_banner_on_validation_failure(self):
        import run_review_snapshot
        from nasdx.review_snapshot import SnapshotValidationError

        output = io.StringIO()
        with (
            patch.object(run_review_snapshot, "build_review_snapshot", side_effect=SnapshotValidationError("brief invalid")),
            patch("sys.argv", ["run_review_snapshot.py"]),
            redirect_stdout(output),
        ):
            self.assertNotEqual(0, run_review_snapshot.main())
        self.assertNotIn("已生成", output.getvalue())
        self.assertIn("brief invalid", output.getvalue())


if __name__ == "__main__":
    unittest.main()

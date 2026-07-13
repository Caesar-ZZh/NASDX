import json
import multiprocessing
import os
import stat
import subprocess
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOUD_SYNC = ROOT / "nasdx" / "cloud_sync.py"


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )


def _hold_lock(lock_path: str, ready_path: str, release_path: str) -> None:
    from nasdx.cloud_sync import exclusive_sync_lock

    with exclusive_sync_lock(Path(lock_path)):
        Path(ready_path).write_text("ready", encoding="utf-8")
        while not Path(release_path).exists():
            time.sleep(0.05)


class CloudSyncContractsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.repo = self.temp_path / "source"
        self.remote = self.temp_path / "remote.git"
        self.repo.mkdir()
        _run_git(self.temp_path, "init", "--bare", str(self.remote))
        _run_git(self.repo, "init", "-b", "main")
        _run_git(self.repo, "config", "user.name", "NASDX Test")
        _run_git(self.repo, "config", "user.email", "nasdx-test@example.invalid")
        (self.repo / ".gitignore").write_text("reports/\n", encoding="utf-8")
        (self.repo / "README.md").write_text("test\n", encoding="utf-8")
        _run_git(self.repo, "add", ".gitignore", "README.md")
        _run_git(self.repo, "commit", "-m", "initial")
        _run_git(self.repo, "remote", "add", "origin", str(self.remote))
        _run_git(self.repo, "push", "-u", "origin", "main")
        _run_git(self.repo, "switch", "-c", "deploy")
        _run_git(self.repo, "push", "-u", "origin", "deploy")
        _run_git(self.repo, "switch", "main")
        self.now = datetime(2026, 7, 10, 15, 30)
        self.reports = self.repo / "reports"
        self.reports.mkdir()
        self.lock_path = self.temp_path / "sync.lock"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _module(self):
        self.assertTrue(CLOUD_SYNC.exists(), "nasdx/cloud_sync.py is missing")
        from nasdx import cloud_sync

        return cloud_sync

    def _write_report(self, *, extra=None) -> Path:
        payload = {
            "datetime": self.now.isoformat(),
            "pool_total": 2,
            "success_count": 2,
            "no_data_count": 0,
            "scan_status": "success",
            "total": 2,
            "bullish": 1,
            "neutral": 1,
            "bearish": 0,
            "top3": [],
            "results": [
                {"code": "510300", "name": "沪深300ETF", "score": 70, "signal": "bullish"},
                {"code": "510500", "name": "中证500ETF", "score": 55, "signal": "neutral"},
            ],
        }
        if extra:
            payload.update(extra)
        path = self.reports / f"etf50_{self.now:%Y%m%d_%H%M}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_empty_or_low_coverage_reports_are_not_publishable(self):
        module = self._module()
        cases = [
            {"pool_total": 50, "success_count": 0, "no_data_count": 50, "scan_status": "failed", "total": 0, "bullish": 0, "neutral": 0, "bearish": 0, "results": [], "top3": []},
            {"pool_total": 50, "success_count": 5, "no_data_count": 45, "scan_status": "partial", "total": 5, "bullish": 1, "neutral": 4, "bearish": 0, "results": [{"code": f"51{i:04d}", "signal": "neutral"} for i in range(5)], "top3": []},
        ]
        for extra in cases:
            with self.subTest(status=extra["scan_status"]):
                report = self._write_report(extra=extra)
                with self.assertRaises(module.ArtifactValidationError):
                    module.validate_publishable_report(report, now=self.now)

    def test_publishable_report_rejects_inconsistent_counts_and_top3(self):
        module = self._module()
        report = self._write_report(extra={"bullish": 2, "neutral": 1})
        with self.assertRaises(module.ArtifactValidationError):
            module.validate_publishable_report(report, now=self.now)

        report = self._write_report(extra={"top3": [{"code": "599999"}]})
        with self.assertRaises(module.ArtifactValidationError):
            module.validate_publishable_report(report, now=self.now)

    def test_publish_uses_isolated_clone_and_keeps_non_main_branch_unchanged(self):
        module = self._module()
        report = self._write_report()
        (self.reports / "portfolio_plan_latest.json").write_text("{}", encoding="utf-8")
        _run_git(self.repo, "switch", "-c", "feature/local-work")

        result = module.publish_latest_etf_report(
            self.repo,
            reports_dir=self.reports,
            lock_path=self.lock_path,
            now=self.now,
        )

        self.assertEqual(result.status, "published")
        self.assertEqual(_run_git(self.repo, "branch", "--show-current").stdout.strip(), "feature/local-work")
        names = _run_git(self.temp_path, f"--git-dir={self.remote}", "ls-tree", "-r", "--name-only", "deploy").stdout
        self.assertIn(f"reports/{report.name}", names)
        self.assertNotIn("portfolio_plan_latest.json", names)

    def test_dirty_source_worktree_fails_without_changing_branch(self):
        module = self._module()
        self._write_report()
        _run_git(self.repo, "switch", "-c", "feature/dirty")
        (self.repo / "README.md").write_text("dirty\n", encoding="utf-8")

        with self.assertRaises(module.DirtyWorktreeError):
            module.publish_latest_etf_report(
                self.repo,
                reports_dir=self.reports,
                lock_path=self.lock_path,
                now=self.now,
            )

        self.assertEqual(_run_git(self.repo, "branch", "--show-current").stdout.strip(), "feature/dirty")
        self.assertEqual((self.repo / "README.md").read_text(encoding="utf-8"), "dirty\n")

    def test_sensitive_nested_field_is_rejected_before_publish(self):
        module = self._module()
        leaked_value = "s" + "k-" + "sensitivevaluefortestonly"
        self._write_report(extra={"metadata": {"api_key": leaked_value}})

        with self.assertRaises(module.ArtifactValidationError):
            module.publish_latest_etf_report(
                self.repo,
                reports_dir=self.reports,
                lock_path=self.lock_path,
                now=self.now,
            )

        names = _run_git(self.temp_path, f"--git-dir={self.remote}", "ls-tree", "-r", "--name-only", "deploy").stdout
        self.assertNotIn("reports/", names)

    def test_sensitive_field_alias_is_rejected_even_without_secret_pattern(self):
        module = self._module()
        self._write_report(extra={"metadata": {"access_token": "masked-value"}})

        with self.assertRaises(module.ArtifactValidationError):
            module.publish_latest_etf_report(
                self.repo,
                reports_dir=self.reports,
                lock_path=self.lock_path,
                now=self.now,
            )

    def test_stale_report_is_rejected(self):
        module = self._module()
        self._write_report()

        with self.assertRaisesRegex(module.ArtifactValidationError, "generation time"):
            module.publish_latest_etf_report(
                self.repo,
                reports_dir=self.reports,
                lock_path=self.lock_path,
                now=self.now + module.MAX_REPORT_AGE + module.MAX_FUTURE_SKEW,
            )

    def test_oversized_report_is_rejected(self):
        module = self._module()
        self._write_report(extra={"padding": "x" * module.MAX_REPORT_BYTES})

        with self.assertRaisesRegex(module.ArtifactValidationError, "exceeds"):
            module.publish_latest_etf_report(
                self.repo,
                reports_dir=self.reports,
                lock_path=self.lock_path,
                now=self.now,
            )

    def test_cross_process_lock_rejects_overlapping_sync(self):
        module = self._module()
        ready = self.temp_path / "ready"
        release = self.temp_path / "release"
        process = multiprocessing.get_context("spawn").Process(
            target=_hold_lock,
            args=(str(self.lock_path), str(ready), str(release)),
        )
        process.start()
        try:
            deadline = time.time() + 10
            while not ready.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "lock holder did not start")
            with self.assertRaises(module.SyncAlreadyRunningError):
                with module.exclusive_sync_lock(self.lock_path):
                    pass
        finally:
            release.write_text("release", encoding="utf-8")
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()

    def test_rejected_push_is_reported_as_failure(self):
        module = self._module()
        self._write_report()
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho publish-rejected >&2\nexit 1\n", encoding="utf-8", newline="\n")
        os.chmod(hook, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        with self.assertRaisesRegex(module.CloudSyncError, "push"):
            module.publish_latest_etf_report(
                self.repo,
                reports_dir=self.reports,
                lock_path=self.lock_path,
                now=self.now,
            )

    def test_legacy_script_contains_no_checkout_or_report_wildcard(self):
        source = (ROOT / "scan_and_sync.py").read_text(encoding="utf-8")

        self.assertNotIn('"checkout"', source)
        self.assertNotIn("reports/*.json", source)
        self.assertNotIn('"add", "-f"', source)
        self.assertIn("publish_latest_etf_report", source)
        self.assertIn("get_reports_dir(create=True)", source)


if __name__ == "__main__":
    unittest.main()

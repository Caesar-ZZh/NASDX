import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from desktop.config import CONFIG_FILE_ENV
from desktop.control import (
    CONTROL_ACTIONS,
    DesktopSession,
    data_refresh_command,
    ensure_user_config,
    resolve_log_dir,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class DesktopControlContractsTest(unittest.TestCase):
    def test_control_panel_exposes_required_desktop_actions(self):
        self.assertEqual(("Start", "Stop", "Open App", "Settings", "Logs", "Data Refresh"), CONTROL_ACTIONS)

    def test_data_refresh_command_uses_existing_cli_without_auto_scan_or_trading(self):
        command = data_refresh_command(ROOT)
        joined = " ".join(command)

        self.assertEqual(sys.executable, command[0])
        self.assertIn("-B", command)
        self.assertIn("fetch_stock_data.py", joined)
        self.assertNotIn("scan_and_sync.py", joined)
        self.assertNotIn("ths", joined.lower())

    def test_settings_button_creates_user_config_from_safe_template_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            appdata = Path(temp_dir) / "roaming"
            config_path = ensure_user_config(ROOT, {"APPDATA": str(appdata)})

            self.assertEqual(appdata / "NASDX" / "config.toml", config_path)
            self.assertTrue(config_path.exists())
            self.assertIn("[llm]", config_path.read_text(encoding="utf-8"))
            self.assertNotIn("sk-", config_path.read_text(encoding="utf-8"))
            self.assertFalse((ROOT / "config.toml").exists())

    def test_log_button_uses_runtime_desktop_logs_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            log_dir = resolve_log_dir(ROOT, {"NASDX_RUNTIME_DIR": str(runtime_dir)}, create=True)

            self.assertEqual(runtime_dir / "desktop_logs", log_dir)
            self.assertTrue(log_dir.exists())

    def test_control_session_start_open_stop_with_fake_process(self):
        captured = []
        opened_urls = []

        def fake_popen(command, **kwargs):
            process = FakeProcess()
            captured.append((command, kwargs, process))
            return process

        with tempfile.TemporaryDirectory() as temp_dir:
            session = DesktopSession(
                root=ROOT,
                port=8765,
                page="plan",
                parent_env={"NASDX_RUNTIME_DIR": str(Path(temp_dir) / "runtime")},
                opener=lambda url: opened_urls.append(url),
                path_opener=lambda path: captured.append(("path", {"path": path}, FakeProcess())),
                popen_factory=fake_popen,
                ready_probe=lambda host, port, timeout: True,
            )

            result = session.start_app(wait=True)
            self.assertTrue(result.ok)
            self.assertIn("?page=plan", result.url)
            self.assertTrue(session.app_running)
            self.assertIn("streamlit", " ".join(captured[0][0]))
            self.assertIn("app.py", " ".join(captured[0][0]))
            self.assertTrue(str(captured[0][2].returncode) == "None")

            open_result = session.open_app()
            self.assertTrue(open_result.ok)
            self.assertEqual([result.url], opened_urls)

            stop_result = session.stop_app()
            self.assertTrue(stop_result.ok)
            self.assertTrue(captured[0][2].terminated)
            self.assertFalse(session.app_running)

    def test_control_panel_dry_run_is_non_destructive(self):
        config_path = ROOT / "config.toml"
        before_config_exists = config_path.exists()

        proc = subprocess.run(
            [sys.executable, "-B", "desktop/control_panel.py", "--dry-run", "--page", "plan"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(proc.stdout)

        self.assertEqual(list(CONTROL_ACTIONS), payload["actions"])
        self.assertEqual("plan", payload["page"])
        self.assertIn("desktop_logs", payload["log_dir"])
        self.assertIn("fetch_stock_data.py", " ".join(payload["data_refresh_command"]))
        self.assertEqual(before_config_exists, config_path.exists())

    def test_explicit_config_path_is_used_for_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "secure" / "nasdx.toml"

            created = ensure_user_config(ROOT, {CONFIG_FILE_ENV: str(config_path)})

            self.assertEqual(config_path, created)
            self.assertTrue(created.exists())


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from desktop.config import CONFIG_FILE_ENV, absolute_path, load_desktop_config
from desktop.paths import (
    APP_ROOT_ENV,
    HISTORY_DB_ENV,
    REPORTS_DIR_ENV,
    RUNTIME_DIR_ENV,
    build_desktop_env,
    is_source_checkout,
    resolve_app_root,
    resolve_runtime_dir,
)
from desktop.runtime import (
    DEFAULT_HOST,
    build_streamlit_command,
    build_streamlit_env,
    build_url,
    create_launch_plan,
    find_free_port,
    find_project_root,
    wait_for_http_ok,
)
from desktop.webview_shell import open_webview


ROOT = Path(__file__).resolve().parents[1]


class DesktopLauncherContractsTest(unittest.TestCase):
    def test_absolute_path_preserves_existing_absolute_path_spelling(self):
        configured = Path(tempfile.gettempdir()) / "not-created" / "config.toml"

        with patch("desktop.config.os.path.abspath", side_effect=AssertionError("must not resolve aliases")):
            resolved = absolute_path(configured)

        self.assertEqual(configured, resolved)

    def test_project_root_detection_uses_existing_streamlit_entry(self):
        root = find_project_root(ROOT / "desktop" / "launcher.py")

        self.assertEqual(ROOT, root)
        self.assertTrue((root / "app.py").exists())
        self.assertTrue((root / "启动网页.bat").exists())

    def test_streamlit_command_launches_existing_app_without_importing_it(self):
        command = build_streamlit_command(ROOT, DEFAULT_HOST, 8501)
        joined = " ".join(command)

        self.assertEqual(sys.executable, command[0])
        self.assertIn("-m", command)
        self.assertIn("streamlit", command)
        self.assertIn("run", command)
        self.assertIn(str(ROOT / "app.py"), command)
        self.assertIn("--server.address", command)
        self.assertIn(DEFAULT_HOST, command)
        self.assertIn("--server.port", command)
        self.assertIn("8501", command)
        self.assertIn("--server.headless", command)
        self.assertNotIn("scan_etf50.py", joined)
        self.assertNotIn("scan_stocks_full.py", joined)
        self.assertNotIn("run_investment_workflow.py", joined)

    def test_page_url_uses_existing_query_param_routing(self):
        self.assertEqual("http://127.0.0.1:8501/", build_url("127.0.0.1", 8501))
        self.assertEqual("http://127.0.0.1:8501/?page=plan", build_url("127.0.0.1", 8501, "plan"))

    def test_environment_keeps_secrets_in_parent_env_only(self):
        env = build_streamlit_env(
            {
                "PATH": "example-path",
                "NASDX_API_KEY": "sk-example-placeholder",
                "NASDX_BASE_URL": "https://example.invalid",
                "NASDX_MODEL": "example-model",
                "NASDX_HISTORY_DB": "history.db",
            }
        )

        self.assertEqual("utf-8", env["PYTHONIOENCODING"])
        self.assertEqual(str(ROOT), env[APP_ROOT_ENV])
        self.assertIn(RUNTIME_DIR_ENV, env)
        self.assertEqual("sk-example-placeholder", env["NASDX_API_KEY"])
        self.assertEqual("https://example.invalid", env["NASDX_BASE_URL"])
        self.assertEqual("example-model", env["NASDX_MODEL"])
        self.assertEqual("history.db", env["NASDX_HISTORY_DB"])

    def test_missing_local_config_uses_user_config_path_without_creating_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir) / "app"
            app_root.mkdir()
            app_data = Path(temp_dir) / "roaming"

            config = load_desktop_config(app_root, {"APPDATA": str(app_data)})

            self.assertFalse(config.exists)
            self.assertEqual(app_data / "NASDX" / "config.toml", config.path)
            self.assertFalse(config.path.exists())
            self.assertEqual([], config.loaded_keys)

    def test_local_config_maps_allowed_values_to_launcher_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "user-config.toml"
            config_path.write_text(
                """
[llm]
api_key = "nasdx-test-token"
base_url = "https://example.invalid/v1"
model = "example-model"

[paths]
runtime_dir = "runtime"
history_db = "runtime/history.db"
reports_dir = "runtime/reports"
""".strip(),
                encoding="utf-8",
            )

            env = build_desktop_env(ROOT, {CONFIG_FILE_ENV: str(config_path)})

            self.assertEqual("nasdx-test-token", env["NASDX_API_KEY"])
            self.assertEqual("https://example.invalid/v1", env["NASDX_BASE_URL"])
            self.assertEqual("example-model", env["NASDX_MODEL"])
            self.assertEqual(str((temp_path / "runtime").resolve()), env[RUNTIME_DIR_ENV])
            self.assertEqual(str((temp_path / "runtime" / "history.db").resolve()), env[HISTORY_DB_ENV])
            self.assertEqual(str((temp_path / "runtime" / "reports").resolve()), env[REPORTS_DIR_ENV])

    def test_parent_environment_wins_over_local_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                """
[llm]
api_key = "config-token"
base_url = "https://config.invalid"
model = "config-model"
""".strip(),
                encoding="utf-8",
            )

            env = build_desktop_env(
                ROOT,
                {
                    CONFIG_FILE_ENV: str(config_path),
                    "NASDX_API_KEY": "parent-token",
                    "NASDX_MODEL": "parent-model",
                },
            )

            self.assertEqual("parent-token", env["NASDX_API_KEY"])
            self.assertEqual("parent-model", env["NASDX_MODEL"])
            self.assertEqual("https://config.invalid", env["NASDX_BASE_URL"])

    def test_placeholder_api_key_is_not_exported_from_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                """
[llm]
api_key = "your-api-key-placeholder"
base_url = "https://example.invalid"
model = "example-model"
""".strip(),
                encoding="utf-8",
            )

            env = build_desktop_env(ROOT, {CONFIG_FILE_ENV: str(config_path)})

            self.assertNotIn("NASDX_API_KEY", env)
            self.assertEqual("https://example.invalid", env["NASDX_BASE_URL"])

    def test_invalid_config_fails_before_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                """
[llm]
base_url = "example.invalid"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                build_desktop_env(ROOT, {CONFIG_FILE_ENV: str(config_path)})

    def test_free_port_selection_returns_preferred_when_available(self):
        port = find_free_port(DEFAULT_HOST, preferred=0)

        self.assertIsInstance(port, int)
        self.assertGreater(port, 0)

    def test_dry_run_is_non_destructive_and_mentions_no_config_write(self):
        config_path = ROOT / "config.toml"
        before_config_exists = config_path.exists()
        cli_scripts = [
            ROOT / "scan_etf50.py",
            ROOT / "scan_stocks_full.py",
            ROOT / "run_investment_workflow.py",
            ROOT / "启动网页.bat",
        ]

        proc = subprocess.run(
            [sys.executable, "-B", "desktop/launcher.py", "--dry-run", "--page", "plan"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(proc.stdout)

        self.assertEqual("plan", payload["page"])
        self.assertIn("app.py", " ".join(payload["command"]))
        self.assertIn("runtime_dir", payload)
        self.assertIn("history_db", payload)
        self.assertIn("reports_dir", payload)
        self.assertEqual(before_config_exists, config_path.exists())
        for script in cli_scripts:
            self.assertTrue(script.exists(), f"{script.name} should remain available")

    def test_dry_run_reports_config_metadata_without_secret_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                """
[llm]
api_key = "nasdx-dry-run-token"
base_url = "https://example.invalid"
model = "example-model"
""".strip(),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env[CONFIG_FILE_ENV] = str(config_path)

            proc = subprocess.run(
                [sys.executable, "-B", "desktop/launcher.py", "--dry-run", "--page", "plan"],
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(proc.stdout)

            self.assertTrue(payload["config_exists"])
            self.assertEqual(str(config_path), payload["config_file"])
            self.assertIn("NASDX_API_KEY", payload["config_loaded_keys"])
            self.assertNotIn("nasdx-dry-run-token", proc.stdout)

    def test_launch_plan_defaults_to_localhost_and_existing_app(self):
        plan = create_launch_plan(root=ROOT, page="quant")

        self.assertEqual(DEFAULT_HOST, plan.host)
        self.assertIn("app.py", " ".join(plan.command))
        self.assertIn("?page=quant", plan.url)

    def test_webview_unavailable_returns_false_for_browser_fallback(self):
        fake_missing_webview = SimpleNamespace(create_window=lambda *args, **kwargs: (_ for _ in ()).throw(ImportError()))

        self.assertFalse(open_webview("http://127.0.0.1:8501/", webview_module=fake_missing_webview))

    def test_webview_failure_does_not_run_close_callback_before_browser_fallback(self):
        calls = []

        fake_webview = SimpleNamespace(
            create_window=lambda *args, **kwargs: calls.append(("create_window", args, kwargs)),
            start=lambda: (_ for _ in ()).throw(RuntimeError("webview unavailable")),
        )

        opened = open_webview(
            "http://127.0.0.1:8501/",
            title="NASDX",
            webview_module=fake_webview,
            on_closed=lambda: calls.append(("closed", (), {})),
        )

        self.assertFalse(opened)
        self.assertEqual("create_window", calls[0][0])
        self.assertNotIn(("closed", (), {}), calls)

    def test_webview_success_runs_close_callback(self):
        calls = []

        fake_webview = SimpleNamespace(
            create_window=lambda *args, **kwargs: calls.append(("create_window", args, kwargs)),
            start=lambda: calls.append(("start", (), {})),
        )

        opened = open_webview(
            "http://127.0.0.1:8501/",
            title="NASDX",
            webview_module=fake_webview,
            on_closed=lambda: calls.append(("closed", (), {})),
        )

        self.assertTrue(opened)
        self.assertEqual(["create_window", "start", "closed"], [item[0] for item in calls])

    def test_launcher_exposes_optional_webview_flag(self):
        proc = subprocess.run(
            [sys.executable, "-B", "desktop/launcher.py", "--help"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertIn("--webview", proc.stdout)
        self.assertIn("--window-title", proc.stdout)
        self.assertIn("--headless-smoke", proc.stdout)

    def test_exe_launcher_delegates_to_control_panel_dry_run(self):
        proc = subprocess.run(
            [sys.executable, "-B", "desktop/exe_launcher.py", "--dry-run", "--page", "plan"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )
        payload = json.loads(proc.stdout)

        self.assertEqual(str(ROOT), payload["root"])
        self.assertEqual("plan", payload["page"])
        self.assertIn("Start", payload["actions"])
        self.assertIn("Data Refresh", payload["actions"])

    def test_exe_launcher_is_only_a_thin_delegating_wrapper(self):
        text = (ROOT / "desktop" / "exe_launcher.py").read_text(encoding="utf-8")

        for marker in [
            ".venv",
            "Scripts",
            "python.exe",
            "desktop",
            "control_panel.py",
            "launcher.py",
            "--webview",
            "--page",
            "subprocess.run",
        ]:
            self.assertIn(marker, text)

        self.assertNotIn("streamlit", text)
        self.assertNotIn("app.py", text)
        self.assertNotIn("scan_etf50.py", text)
        self.assertNotIn("sk-", text)

    def test_http_probe_fails_cleanly_for_closed_port(self):
        closed_port = find_free_port(DEFAULT_HOST, preferred=0)

        self.assertFalse(wait_for_http_ok(f"http://{DEFAULT_HOST}:{closed_port}/", timeout=0.1, interval=0.05))

    def test_source_checkout_runtime_defaults_to_project_root(self):
        self.assertTrue(is_source_checkout(ROOT))
        self.assertEqual(ROOT, resolve_runtime_dir(ROOT, {}))

    def test_runtime_dir_can_be_overridden_for_portable_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"

            resolved = resolve_runtime_dir(ROOT, {RUNTIME_DIR_ENV: str(runtime_dir)})
            env = build_desktop_env(ROOT, {RUNTIME_DIR_ENV: str(runtime_dir)})

            self.assertEqual(runtime_dir.resolve(), resolved)
            self.assertEqual(str(runtime_dir.resolve()), env[RUNTIME_DIR_ENV])
            self.assertEqual(str(runtime_dir.resolve() / "nasdx_history.db"), env[HISTORY_DB_ENV])
            self.assertEqual(str(runtime_dir.resolve() / "reports"), env[REPORTS_DIR_ENV])

    def test_existing_history_db_env_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "runtime"
            custom_db = Path(temp_dir) / "custom.db"

            env = build_desktop_env(ROOT, {RUNTIME_DIR_ENV: str(runtime_dir), HISTORY_DB_ENV: str(custom_db)})

            self.assertEqual(str(custom_db), env[HISTORY_DB_ENV])

    def test_configured_app_root_must_look_like_nasdx_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(FileNotFoundError):
                resolve_app_root(env={APP_ROOT_ENV: temp_dir})

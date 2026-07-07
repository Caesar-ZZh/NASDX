import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from desktop.config import CONFIG_FILE_ENV
from desktop.doctor import FAIL, PASS, WARN, run_doctor


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "run_desktop_doctor.py"


class DesktopDoctorContractsTest(unittest.TestCase):
    def test_desktop_doctor_script_exists_and_is_safe_by_default(self):
        self.assertTrue(SCRIPT.exists(), "run_desktop_doctor.py is missing")
        doctor_source = (ROOT / "desktop" / "doctor.py").read_text(encoding="utf-8")
        script_source = SCRIPT.read_text(encoding="utf-8")

        for marker in [
            "run_doctor",
            "CORE_MODULES",
            "FEATURE_MODULES",
            "optional_webview",
            "inno_setup",
            "ISCC.exe",
            "--check-write",
            "--json",
            "loaded keys",
        ]:
            self.assertIn(marker, doctor_source)

        self.assertIn("desktop.doctor", script_source)
        self.assertNotIn("Start-Process", doctor_source)
        self.assertNotIn("streamlit run app.py", doctor_source)
        self.assertNotIn("sk-", doctor_source)

    def test_doctor_reports_required_desktop_entries_without_starting_app(self):
        checks = run_doctor(root=ROOT)
        by_label = {item.label: item for item in checks}

        self.assertEqual(PASS, by_label["required_files"].status)
        self.assertEqual(PASS, by_label["config"].status)
        self.assertEqual(PASS, by_label["desktop_env"].status)
        self.assertEqual(PASS, by_label["launch_plan"].status)
        self.assertIn(by_label["inno_setup"].status, {PASS, WARN})

    def test_doctor_cli_json_does_not_print_secret_values(self):
        secret = "sk-" + "desktopdoctortesttoken"
        env = dict(os.environ)
        env["NASDX_API_KEY"] = secret

        proc = subprocess.run(
            [sys.executable, "-B", "run_desktop_doctor.py", "--json"],
            cwd=str(ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        labels = {item["label"] for item in payload}

        self.assertIn("required_files", labels)
        self.assertIn("desktop_env", labels)
        self.assertNotIn(secret, proc.stdout)
        self.assertNotIn("desktopdoctortesttoken", proc.stdout)

    def test_doctor_fails_invalid_config_before_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                """
[llm]
base_url = "example.invalid"
""".strip(),
                encoding="utf-8",
            )
            checks = run_doctor(root=ROOT, env={CONFIG_FILE_ENV: str(config_path)})

        by_label = {item.label: item for item in checks}
        self.assertEqual(FAIL, by_label["config"].status)
        self.assertIn("base_url", by_label["config"].detail)

    def test_doctor_text_summary_succeeds_with_warnings(self):
        proc = subprocess.run(
            [sys.executable, "-B", "run_desktop_doctor.py"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )

        self.assertIn("summary:", proc.stdout)
        self.assertIn("required_files", proc.stdout)
        self.assertNotIn("sk-", proc.stdout)


if __name__ == "__main__":
    unittest.main()

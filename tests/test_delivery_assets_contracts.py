import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeliveryAssetsContractsTest(unittest.TestCase):
    def test_requirements_manifest_is_versionable(self):
        requirements_path = ROOT / "requirements_nasdx.txt"
        self.assertTrue(requirements_path.exists(), "requirements_nasdx.txt is missing")
        text = requirements_path.read_text(encoding="utf-8")
        self.assertIn("akshare", text)
        self.assertIn("openai", text)
        self.assertIn("streamlit", text)

        proc = subprocess.run(
            ["git", "check-ignore", "-q", "requirements_nasdx.txt"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertNotEqual(
            proc.returncode,
            0,
            "requirements_nasdx.txt is ignored by git even though README documents it as an install input",
        )

    def test_final_audit_checks_delivery_assets(self):
        audit_source = (ROOT / "run_final_audit.py").read_text(encoding="utf-8")
        self.assertIn("check_delivery_assets", audit_source)
        self.assertIn("依赖清单", audit_source)


if __name__ == "__main__":
    unittest.main()

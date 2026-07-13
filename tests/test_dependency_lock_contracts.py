import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "packaging" / "windows"


class DependencyLockContractsTest(unittest.TestCase):
    def test_windows_lockfiles_pin_every_package_with_hashes(self):
        for name in ["requirements-win-core.lock", "requirements-win-webview.lock"]:
            path = WINDOWS / name
            self.assertTrue(path.exists(), name)
            text = path.read_text(encoding="utf-8")
            package_lines = [
                line for line in text.splitlines()
                if line and not line.startswith(("#", " ", "-", "\\"))
            ]
            self.assertGreater(len(package_lines), 20, name)
            self.assertTrue(all("==" in line for line in package_lines), name)
            self.assertGreaterEqual(text.count("--hash=sha256:"), len(package_lines), name)

    def test_release_build_uses_pinned_toolchain_and_hash_locked_install(self):
        toolchain = json.loads((WINDOWS / "toolchain-win.json").read_text(encoding="utf-8"))
        self.assertRegex(toolchain["python"], r"^3\.11\.")
        self.assertRegex(toolchain["pip"], r"^\d+\.\d+(?:\.\d+)?$")
        script = (WINDOWS / "build_portable.ps1").read_text(encoding="utf-8")
        for marker in ["--require-hashes", "requirements-win-core.lock", "requirements-win-webview.lock", "lockfile_sha256", "resolved_packages"]:
            self.assertIn(marker, script)
        self.assertNotIn('@("-U", "pip")', script)


if __name__ == "__main__":
    unittest.main()

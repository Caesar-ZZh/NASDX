"""Contracts for the multi-provider secret scanner (Issue #71).

Every fake credential in this file is assembled at runtime from fragments, so
no literal token is ever committed. Do not inline a full token here: the
repository's own gate scans this file.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nasdx import secret_scan  # noqa: E402

SECURITY_WORKFLOW = ROOT / ".github" / "workflows" / "security.yml"
GITLEAKS_CONFIG = ROOT / ".gitleaks.toml"
GITLEAKS_IGNORE = ROOT / ".gitleaksignore"
ALLOWLIST_FILE = ROOT / "security" / "secret_scan_allowlist.toml"
SECURITY_SCRIPT = ROOT / "run_security_checks.py"

# Fragments -> fake credentials. Kept split so the literals never form a token.
_BODY = "Kq7fT2mZ9wB4nD8xR1vC5hJ3pL6yG0sA"
_BODY36 = _BODY + "2bQ7"
FAKE = {
    "openai-style-api-key": "sk-" + _BODY,
    "github-token": "ghp" + "_" + _BODY36,
    "github-fine-grained-pat": "github" + "_pat_" + "11ABCDE7Q0" + _BODY36 + "zXcVbNmQ",
    "aws-access-key-id": "AKI" + "A" + "Q7F2MZ9WB4ND8XR1",
    "slack-token": "xox" + "b-2847392018-4829103847-" + _BODY[:20],
    "google-api-key": "AIz" + "a" + _BODY + "2bQ",
    "npm-token": "npm" + "_" + _BODY36,
    "jwt": "eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + _BODY,
}
PRIVATE_KEY_HEADER = "-----BEGIN" + " RSA PRIVATE KEY" + "-----"


def _git() -> str | None:
    return shutil.which("git")


def load_security_module():
    spec = importlib.util.spec_from_file_location("run_security_checks", SECURITY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RuleCoverageTest(unittest.TestCase):
    """Acceptance criterion 1: cover the credential shapes that matter."""

    def test_required_providers_are_detected(self):
        for rule_id, token in FAKE.items():
            with self.subTest(rule=rule_id):
                findings = secret_scan.scan_text(f'value = "{token}"', "probe.py")
                self.assertIn(rule_id, {f.rule_id for f in findings})

    def test_private_key_block_is_detected(self):
        findings = secret_scan.scan_text(PRIVATE_KEY_HEADER, "id_rsa")
        self.assertIn("private-key-block", {f.rule_id for f in findings})

    def test_aws_secret_access_key_is_detected(self):
        line = "aws_secret_access_key = " + '"' + _BODY + "2bQ7zXc/" + '"'
        findings = secret_scan.scan_text(line, "probe.py")
        self.assertIn("aws-secret-access-key", {f.rule_id for f in findings})

    def test_generic_high_entropy_assignment_is_detected(self):
        findings = secret_scan.scan_text(f'client_secret = "{_BODY}"', "probe.py")
        self.assertIn("generic-assigned-secret", {f.rule_id for f in findings})

    def test_scanner_covers_more_than_the_legacy_single_regex(self):
        self.assertGreaterEqual(len(secret_scan.RULES), 10)
        self.assertEqual(len(secret_scan.RULE_IDS), len(secret_scan.RULES))


class FalsePositiveTest(unittest.TestCase):
    """Acceptance criterion 4: placeholders and env lookups stay quiet."""

    def test_placeholders_and_env_lookups_do_not_fire(self):
        benign = [
            'API_KEY = os.getenv("DEEPSEEK_API_KEY")',
            'api_key = "your-api-key-here"',
            'api_key = "sk-' + "x" * 24 + '"',
            'password = "' + "x" * 20 + '"',
            'token = "placeholder-token-value-1"',
            'client_secret = "${DEEPSEEK_CLIENT_SECRET}"',
            'api_key = "changeme-changeme-changeme"',
            "api_key: <redacted-in-report>",
        ]
        for line in benign:
            with self.subTest(line=line[:40]):
                self.assertEqual([], secret_scan.scan_text(line, "probe.py"))

    def test_repository_working_tree_is_clean(self):
        findings, scanned = secret_scan.scan_worktree(ROOT)
        self.assertGreater(scanned, 100)
        self.assertEqual([], [f.redacted() for f in findings])


class RedactionTest(unittest.TestCase):
    """Acceptance criterion 3: only rule / path / line / fingerprint escape."""

    def test_finding_output_never_contains_the_secret(self):
        token = FAKE["openai-style-api-key"]
        findings = secret_scan.scan_text(f'k = "{token}"', "probe.py")
        self.assertTrue(findings)
        rendered = secret_scan.format_findings(findings)
        self.assertNotIn(token, rendered)
        self.assertNotIn(token[:8], rendered)
        self.assertIn("rule=openai-style-api-key", rendered)
        self.assertIn("path=probe.py", rendered)
        self.assertIn("line=1", rendered)
        self.assertIn("fingerprint=", rendered)

    def test_fingerprint_is_stable_and_not_reversible(self):
        digest_a = secret_scan.fingerprint(FAKE["github-token"])
        digest_b = secret_scan.fingerprint(FAKE["github-token"])
        self.assertEqual(digest_a, digest_b)
        self.assertRegex(digest_a, r"^[0-9a-f]{16}$")
        self.assertNotIn(FAKE["github-token"][:8], digest_a)

    def test_scan_is_deterministic(self):
        text = f'a = "{FAKE["github-token"]}"\nb = "{FAKE["npm-token"]}"\n'
        first = [f.redacted() for f in secret_scan.scan_text(text, "probe.py")]
        second = [f.redacted() for f in secret_scan.scan_text(text, "probe.py")]
        self.assertEqual(first, second)
        self.assertEqual(2, len(first))


@unittest.skipUnless(_git(), "git is required for history scanning")
class HistoryScanTest(unittest.TestCase):
    """Acceptance criterion 2: deleted-but-committed secrets still fail."""

    def _init_repo(self, root: Path) -> None:
        git = _git()
        for argv in (
            [git, "init", "--quiet"],
            [git, "config", "user.email", "gate@example.invalid"],
            [git, "config", "user.name", "gate"],
            [git, "config", "commit.gpgsign", "false"],
        ):
            subprocess.run(argv, cwd=str(root), check=True, capture_output=True)

    def _commit(self, root: Path, message: str) -> None:
        git = _git()
        subprocess.run([git, "add", "-A"], cwd=str(root), check=True, capture_output=True)
        subprocess.run(
            [git, "commit", "--quiet", "--no-verify", "-m", message],
            cwd=str(root),
            check=True,
            capture_output=True,
        )

    def test_secret_added_then_deleted_still_fails_history_scan(self):
        token = FAKE["github-token"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            leaked = root / "leaked.py"
            leaked.write_text(f'TOKEN = "{token}"\n', encoding="utf-8")
            self._commit(root, "add config")

            leaked.unlink()
            (root / "clean.py").write_text("TOKEN = None\n", encoding="utf-8")
            self._commit(root, "remove config")

            tree_findings, _ = secret_scan.scan_worktree(root)
            history_findings, blobs = secret_scan.scan_history(root)

        self.assertEqual([], tree_findings, "deleted file must not appear in the tree scan")
        self.assertGreater(blobs, 0)
        self.assertIn("github-token", {f.rule_id for f in history_findings})
        hit = next(f for f in history_findings if f.rule_id == "github-token")
        self.assertEqual("history", hit.source)
        self.assertEqual("leaked.py", hit.path)
        self.assertTrue(hit.blob)
        self.assertNotIn(token[:8], hit.redacted())

    def test_repository_history_is_clean(self):
        findings, blobs = secret_scan.scan_history(ROOT)
        self.assertGreater(blobs, 0)
        self.assertEqual([], [f.redacted() for f in findings])


class AllowlistTest(unittest.TestCase):
    """Acceptance criterion 4: exemptions stay minimal and auditable."""

    def _write(self, root: Path, body: str) -> None:
        target = root / "security"
        target.mkdir(parents=True, exist_ok=True)
        (target / "secret_scan_allowlist.toml").write_text(body, encoding="utf-8")

    def test_committed_allowlist_is_valid(self):
        self.assertTrue(ALLOWLIST_FILE.is_file())
        entries = secret_scan.load_allowlist(ROOT)
        for entry in entries:
            self.assertTrue(entry.reason)
            self.assertTrue(entry.path or entry.fingerprint)

    def test_path_scoped_entry_suppresses_only_that_path(self):
        finding = secret_scan.Finding(
            rule_id="github-token", path="tests/fixture.py", line=3, fingerprint="a" * 16
        )
        other = secret_scan.Finding(
            rule_id="github-token", path="nasdx/live.py", line=3, fingerprint="a" * 16
        )
        entry = secret_scan.AllowEntry(
            reason="fixture", rule="github-token", path="tests/fixture.py"
        )
        self.assertEqual([other], secret_scan.apply_allowlist([finding, other], [entry]))

    def test_fingerprint_scoped_entry_suppresses_only_that_secret(self):
        keep = secret_scan.Finding(
            rule_id="github-token", path="a.py", line=1, fingerprint="b" * 16
        )
        drop = secret_scan.Finding(
            rule_id="github-token", path="a.py", line=2, fingerprint="c" * 16
        )
        entry = secret_scan.AllowEntry(reason="revoked", fingerprint="c" * 16)
        self.assertEqual([keep], secret_scan.apply_allowlist([keep, drop], [entry]))

    def test_bare_rule_exemption_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, '[[allow]]\nrule = "github-token"\nreason = "too broad"\n')
            with self.assertRaises(secret_scan.AllowlistError):
                secret_scan.load_allowlist(root)

    def test_wildcard_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, '[[allow]]\npath = "**"\nreason = "too broad"\n')
            with self.assertRaises(secret_scan.AllowlistError):
                secret_scan.load_allowlist(root)

    def test_missing_reason_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, '[[allow]]\npath = "tests/fixture.py"\n')
            with self.assertRaises(secret_scan.AllowlistError):
                secret_scan.load_allowlist(root)

    def test_unknown_rule_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                '[[allow]]\nrule = "no-such-rule"\npath = "tests/fixture.py"\nreason = "typo"\n',
            )
            with self.assertRaises(secret_scan.AllowlistError):
                secret_scan.load_allowlist(root)

    def test_missing_allowlist_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], secret_scan.load_allowlist(Path(tmp)))


class SecurityGateWiringTest(unittest.TestCase):
    """Acceptance criteria 5 & 6: the gate cannot regress to one regex."""

    def test_legacy_single_regex_gate_is_gone(self):
        for script in (SECURITY_SCRIPT, ROOT / "run_final_audit.py"):
            text = script.read_text(encoding="utf-8")
            with self.subTest(script=script.name):
                self.assertNotIn('SECRET_RE = re.compile(r"sk-', text)
                self.assertIn("secret_scan", text)

    def test_cli_exposes_history_mode(self):
        module = load_security_module()
        self.assertTrue(hasattr(module, "scan_history_for_secrets"))
        text = SECURITY_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--history", text)
        self.assertIn("secret_history_scan", text)

    def test_security_workflow_gates_pr_push_and_schedule(self):
        self.assertTrue(SECURITY_WORKFLOW.is_file(), "security workflow is missing")
        text = SECURITY_WORKFLOW.read_text(encoding="utf-8")
        for marker in [
            "pull_request:",
            "push:",
            "schedule:",
            "cron:",
            "fetch-depth: 0",
            "permissions:",
            "contents: read",
            "run_security_checks.py --skip-optional --history",
            "gitleaks",
            "sha256sum --check",
            "--redact",
            "tests.test_secret_scan_contracts",
        ]:
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_workflow_pins_tool_versions(self):
        text = SECURITY_WORKFLOW.read_text(encoding="utf-8")
        uses = re.findall(r"uses:\s*(\S+)", text)
        self.assertTrue(uses)
        for ref in uses:
            with self.subTest(ref=ref):
                self.assertRegex(ref, r"@[0-9a-f]{40}$", "actions must be pinned to a commit SHA")
        self.assertRegex(text, r'GITLEAKS_VERSION:\s*"\d+\.\d+\.\d+"')
        self.assertRegex(text, r'GITLEAKS_SHA256:\s*"[0-9a-f]{64}"')

    def test_gitleaks_config_is_committed_and_narrow(self):
        self.assertTrue(GITLEAKS_CONFIG.is_file())
        text = GITLEAKS_CONFIG.read_text(encoding="utf-8")
        self.assertIn("useDefault = true", text)
        self.assertNotIn("stopwords", text.lower().replace("#", ""))
        for forbidden in ("'''.*'''", '"""^.*$"""'):
            self.assertNotIn(forbidden, text)

    def test_worktree_scan_reports_redacted_hits_only(self):
        module = load_security_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token = FAKE["openai-style-api-key"]
            (root / "dist").mkdir()
            (root / "safe.py").write_text(f'KEY = "{token}"\n', encoding="utf-8")
            (root / "dist" / "ignored.py").write_text(f'KEY = "{token}"\n', encoding="utf-8")

            hits, scanned = module.scan_for_secrets(root)

        self.assertEqual(1, scanned)
        self.assertEqual(1, len(hits))
        self.assertIn("rule=openai-style-api-key", hits[0])
        self.assertIn("path=safe.py", hits[0])
        self.assertNotIn(token[:8], hits[0])


class GitleaksIgnoreTest(unittest.TestCase):
    """Acceptance criterion 4: gitleaks exemptions stay finding-scoped.

    ``.gitleaksignore`` is the only place where a gitleaks hit can be waived.
    A bare path or rule id there would silently hide every future leak in the
    same file, which is exactly the blind spot this issue is closing.
    """

    @staticmethod
    def _entries_with_reasons():
        if not GITLEAKS_IGNORE.is_file():
            return []
        pairs = []
        pending_reason = None
        for raw in GITLEAKS_IGNORE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                pending_reason = None
                continue
            if line.startswith("#"):
                lowered = line.lstrip("#").strip().lower()
                if lowered.startswith("reason:"):
                    pending_reason = lowered[len("reason:"):].strip()
                continue
            pairs.append((line, pending_reason))
            pending_reason = None
        return pairs

    def test_every_entry_is_a_fully_qualified_fingerprint(self):
        entries = self._entries_with_reasons()
        self.assertTrue(entries, "expected at least one documented exemption")
        for entry, _reason in entries:
            with self.subTest(entry=entry):
                parts = entry.split(":")
                self.assertEqual(
                    4,
                    len(parts),
                    "entry must be <commit>:<path>:<rule>:<line>",
                )
                commit, path, rule, line = parts
                self.assertRegex(commit, r"^[0-9a-f]{40}$")
                self.assertTrue(path)
                self.assertIn(rule.split("-")[0], rule)
                self.assertTrue(line.isdigit())
                for wildcard in ("*", "?", "["):
                    self.assertNotIn(wildcard, entry)

    def test_every_entry_documents_a_reason(self):
        for entry, reason in self._entries_with_reasons():
            with self.subTest(entry=entry):
                self.assertTrue(
                    reason,
                    "each fingerprint needs a '# reason:' comment above it",
                )

    def test_exemptions_are_scoped_to_known_test_fixtures(self):
        for entry, _reason in self._entries_with_reasons():
            path = entry.split(":")[1]
            with self.subTest(entry=entry):
                self.assertTrue(
                    path.startswith("tests/"),
                    "only deliberately fake test fixtures may be waived",
                )

    def test_workflow_passes_the_ignore_file_explicitly(self):
        text = SECURITY_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--gitleaks-ignore-path .gitleaksignore", text)


if __name__ == "__main__":
    unittest.main()

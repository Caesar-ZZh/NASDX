"""Release-gate integrity for ``scripts/run_final_audit.py`` (#88).

These tests enforce the contract described in issue #88:

* The final delivery audit MUST pass on a clean ``master`` (exit code 0).
* A synthetically removed required documentation marker MUST still make the
  gate fail (non-zero exit), so the gate can never be silently normalized to
  "21/22 except a known failure".
"""

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import run_final_audit  # noqa: E402


def _read_real_readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def _read_real_framework() -> str:
    return (REPO_ROOT / "docs" / "INVESTMENT_DECISION_FRAMEWORK.md").read_text(
        encoding="utf-8"
    )


def test_check_documentation_passes_on_current_tree():
    """Sanity anchor: current README + framework satisfy the doc gate."""
    detail = run_final_audit.check_documentation()
    assert isinstance(detail, str) and detail


def test_missing_readme_marker_raises():
    """Synthetic removal of a required README marker must fail the check."""
    real = _read_real_readme()
    marker = "风险画像"
    assert marker in real, "marker under test must be genuinely required"
    stripped = real.replace(marker, "RISK_PROFILE_PLACEHOLDER")

    original = run_final_audit._read_text

    def fake_read(path):
        p = Path(path)
        if p.name == "README.md":
            return stripped
        return original(path)

    with mock.patch.object(run_final_audit, "_read_text", side_effect=fake_read):
        with pytest.raises(AssertionError):
            run_final_audit.check_documentation()


def test_missing_framework_marker_raises():
    """Synthetic removal of a required framework marker must fail the check."""
    real = _read_real_framework()
    marker = "nasdx_history.db"
    assert marker in real, "marker under test must be genuinely required"
    stripped = real.replace(marker, "HISTORY_DB_PLACEHOLDER")

    original = run_final_audit._read_text

    def fake_read(path):
        p = Path(path)
        if p.name == "INVESTMENT_DECISION_FRAMEWORK.md":
            return stripped
        return original(path)

    with mock.patch.object(run_final_audit, "_read_text", side_effect=fake_read):
        with pytest.raises(AssertionError):
            run_final_audit.check_documentation()


def test_final_audit_exit_zero_on_clean_tree():
    """The whole gate must be green (exit 0) on a clean master."""
    assert run_final_audit.main() == 0


def test_final_audit_nonzero_when_marker_missing():
    """A missing required marker must produce a non-zero release-gate exit."""
    real = _read_real_readme()
    marker = "风险画像"
    stripped = real.replace(marker, "RISK_PROFILE_PLACEHOLDER")

    original = run_final_audit._read_text

    def fake_read(path):
        p = Path(path)
        if p.name == "README.md":
            return stripped
        return original(path)

    with mock.patch.object(run_final_audit, "_read_text", side_effect=fake_read):
        assert run_final_audit.main() == 1

"""Run lightweight NASDX security checks without adding required dependencies.

The required gate is a multi-provider secret scan (see ``nasdx/secret_scan.py``)
rather than the old single ``sk-*`` working-tree regex.  Two modes are exposed:

* default: scan the current versionable tree (fast pre-check, runs everywhere);
* ``--history``: additionally scan every text blob reachable from any ref, so a
  credential that was committed and later deleted still fails the gate.

Findings are always printed redacted (rule / path / line / fingerprint) so CI
logs never echo the credential itself.
"""
from __future__ import annotations

# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nasdx.secret_scan import (  # noqa: E402  (path bootstrap must run first)
    FALLBACK_IGNORE_DIRS,
    SOURCE_SUFFIXES,
    AllowlistError,
    Finding,
    format_findings,
    iter_candidate_files,
    scan_history,
    scan_worktree,
)

# ``git ls-files --cached --others --exclude-standard`` selects the scanned set;
# the implementation lives in nasdx/secret_scan.py so the tree scan, the history
# scan and run_final_audit.py all share one rule table.

__all__ = [
    "FALLBACK_IGNORE_DIRS",
    "SOURCE_SUFFIXES",
    "SecurityResult",
    "iter_candidate_files",
    "run_checks",
    "scan_for_secrets",
    "scan_history_for_secrets",
]


@dataclass(frozen=True)
class SecurityResult:
    label: str
    status: str
    detail: str


def scan_for_secrets(root: Path = ROOT) -> tuple[list[str], int]:
    """Scan the working tree; return ``(redacted_hits, files_scanned)``."""
    findings, scanned = scan_worktree(root)
    return [finding.redacted() for finding in findings], scanned


def scan_history_for_secrets(root: Path = ROOT) -> tuple[list[str], int]:
    """Scan all reachable git blobs; return ``(redacted_hits, blobs_scanned)``."""
    findings, scanned = scan_history(root)
    return [finding.redacted() for finding in findings], scanned


def run_checks(*, run_optional: bool = False, include_history: bool = False) -> list[SecurityResult]:
    results = [_worktree_result()]
    if include_history:
        results.append(_history_result())
    results.extend(_optional_tool_results(run_optional=run_optional))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NASDX lightweight security checks.")
    parser.add_argument(
        "--run-optional",
        action="store_true",
        help="run optional pip-audit, bandit, and detect-secrets if installed",
    )
    parser.add_argument(
        "--skip-optional",
        action="store_true",
        help="explicitly skip optional external security tools",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="also scan every text blob reachable from any git ref (needs full clone)",
    )
    args = parser.parse_args()

    results = run_checks(
        run_optional=args.run_optional and not args.skip_optional,
        include_history=args.history,
    )
    failed = 0
    skipped = 0
    for result in results:
        print(f"[{result.status}] {result.label}: {result.detail}")
        if result.status == "FAIL":
            failed += 1
        elif result.status == "SKIP":
            skipped += 1

    passed = sum(1 for item in results if item.status == "PASS")
    print(f"summary: passed={passed} skipped={skipped} failed={failed}")
    return 1 if failed else 0


def _worktree_result() -> SecurityResult:
    try:
        findings, scanned = scan_worktree(ROOT)
    except AllowlistError as exc:
        return SecurityResult(label="secret_scan", status="FAIL", detail=str(exc))
    return SecurityResult(
        label="secret_scan",
        status="FAIL" if findings else "PASS",
        detail=_detail(findings, f"scanned {scanned} versionable text files"),
    )


def _history_result() -> SecurityResult:
    try:
        findings, scanned = scan_history(ROOT)
    except AllowlistError as exc:
        return SecurityResult(label="secret_history_scan", status="FAIL", detail=str(exc))
    except RuntimeError as exc:
        return SecurityResult(label="secret_history_scan", status="FAIL", detail=str(exc))
    return SecurityResult(
        label="secret_history_scan",
        status="FAIL" if findings else "PASS",
        detail=_detail(findings, f"scanned {scanned} reachable git blobs"),
    )


def _detail(findings: list[Finding], ok_detail: str) -> str:
    return format_findings(findings) if findings else ok_detail


def _optional_tool_results(*, run_optional: bool) -> Iterable[SecurityResult]:
    tools = [
        (
            "pip-audit",
            ["pip-audit", "-r", "requirements_nasdx.txt"],
            "dependency vulnerability scan for the runtime requirements",
        ),
        (
            "bandit",
            ["bandit", "-q", "-r", "nasdx", "quant", "desktop"],
            "Python security lint for source packages",
        ),
        (
            "detect-secrets",
            ["detect-secrets", "scan", "--all-files"],
            "full repository secret scanner",
        ),
    ]
    for label, argv, purpose in tools:
        if not run_optional:
            yield SecurityResult(label=label, status="SKIP", detail=f"{purpose}; pass --run-optional to execute")
            continue
        executable = shutil.which(argv[0])
        if not executable:
            yield SecurityResult(label=label, status="SKIP", detail="not installed")
            continue
        command = [executable, *argv[1:]]
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=300,
        )
        output = _tail((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else ""))
        status = "PASS" if proc.returncode == 0 else "FAIL"
        yield SecurityResult(label=label, status=status, detail=output or "completed")


def _tail(text: str, max_lines: int = 20) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-max_lines:])


if __name__ == "__main__":
    raise SystemExit(main())

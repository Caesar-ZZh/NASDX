"""Run lightweight NASDX security checks without adding required dependencies."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).parent
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]{20,}")
SOURCE_SUFFIXES = (
    ".py",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".ps1",
    ".bat",
    ".txt",
    ".json",
)
FALLBACK_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "wheelhouse",
    "reports",
    "desktop_logs",
    "htmlcov",
}


@dataclass(frozen=True)
class SecurityResult:
    label: str
    status: str
    detail: str


def iter_candidate_files(root: Path = ROOT) -> list[Path]:
    """Return versionable text-like files, excluding ignored generated output."""
    git_files = _git_candidate_files(root)
    if git_files is not None:
        return git_files

    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in FALLBACK_IGNORE_DIRS for part in path.relative_to(root).parts):
            continue
        if _is_candidate(path):
            files.append(path)
    return sorted(files)


def scan_for_secrets(root: Path = ROOT) -> tuple[list[str], int]:
    hits: list[str] = []
    files = iter_candidate_files(root)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            hits.append(f"{_rel(path, root)}: read failed: {exc}")
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in SECRET_RE.finditer(line):
                token = match.group(0)
                if token.lower().startswith("sk-xxxx"):
                    continue
                hits.append(f"{_rel(path, root)}:{lineno}:{token[:8]}...")
    return hits, len(files)


def run_checks(*, run_optional: bool = False) -> list[SecurityResult]:
    hits, scanned = scan_for_secrets()
    results = [
        SecurityResult(
            label="secret_scan",
            status="FAIL" if hits else "PASS",
            detail="; ".join(hits[:5]) if hits else f"scanned {scanned} versionable text files",
        )
    ]
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
    args = parser.parse_args()

    results = run_checks(run_optional=args.run_optional and not args.skip_optional)
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


def _git_candidate_files(root: Path) -> list[Path] | None:
    git = shutil.which("git")
    if not git:
        return None
    proc = subprocess.run(
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=str(root),
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return None
    files: list[Path] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        path = root / rel
        if path.is_file() and _is_candidate(path):
            files.append(path)
    return sorted(files)


def _is_candidate(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_SUFFIXES


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


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())

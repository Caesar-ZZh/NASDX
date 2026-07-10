"""Collect NASDX desktop release evidence without running the app."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from desktop.doctor import FAIL as DOCTOR_FAIL
from desktop.doctor import run_doctor
from run_desktop_completion_audit import FAIL, INCOMPLETE, run_completion_audit


ROOT = Path(__file__).parent
SCHEMA = "nasdx_desktop_release_evidence.v1"
FORBIDDEN_PACKAGE_PATTERNS = [
    ".env",
    "config.toml",
    "reports",
    "reports/**",
    "stock_data_*.json",
    "nasdx_history.db",
    "desktop_logs",
    "desktop_logs/**",
    "*.log",
    "**/*.log",
    "*_log*.txt",
    "**/*_log*.txt",
    "fetch_log.txt",
    "pip_install.txt",
    "__pycache__",
    "**/__pycache__",
    "*.pyc",
    "**/*.pyc",
    "*.pyo",
    "**/*.pyo",
    "models/signal_confidence.json",
    ".pytest_cache",
    ".pytest_cache/**",
    ".ruff_cache",
    ".ruff_cache/**",
    ".git",
    ".git/**",
    "dist",
    "dist/**",
    "build",
    "build/**",
    "wheelhouse",
    "wheelhouse/**",
]


def build_release_evidence(
    root: Path = ROOT,
    *,
    package_dir: Path | None = None,
    include_zip: bool = True,
    zip_path: Path | None = None,
    zip_manifest: Path | None = None,
    installer_path: Path | None = None,
    installer_proof: Path | None = None,
) -> dict[str, Any]:
    completion_items = run_completion_audit(root)
    doctor_items = run_doctor(root=root, page="plan", check_write=False)
    evidence = {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "root": str(root.resolve()),
        "completion_audit": [asdict(item) for item in completion_items],
        "desktop_doctor": [asdict(item) for item in doctor_items],
        "artifacts": _artifact_evidence(
            root,
            package_dir=package_dir,
            include_zip=include_zip,
            zip_path=zip_path,
            zip_manifest=zip_manifest,
            installer_path=installer_path,
            installer_proof=installer_proof,
        ),
        "ignored_paths": _ignored_path_evidence(root),
        "next_commands": _next_commands(),
    }
    evidence["summary"] = _summary(evidence)
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect NASDX desktop release evidence.")
    parser.add_argument("--json", action="store_true", help="Print JSON evidence to stdout.")
    parser.add_argument("--write", action="store_true", help="Write JSON evidence to --output.")
    parser.add_argument(
        "--output",
        default=str(ROOT / "dist" / "release-evidence" / "NASDX-desktop-release-evidence.json"),
        help="Output path used with --write.",
    )
    parser.add_argument(
        "--package-dir",
        default=str(ROOT / "dist" / "NASDX-Desktop"),
        help="Portable package directory to summarize.",
    )
    parser.add_argument("--skip-zip", action="store_true", help="Skip portable zip artifact evidence.")
    parser.add_argument(
        "--zip-path",
        default=str(ROOT / "dist" / "NASDX-Desktop-portable.zip"),
        help="Portable zip path to summarize.",
    )
    parser.add_argument(
        "--zip-manifest",
        default=str(ROOT / "dist" / "NASDX-Desktop-portable.manifest.json"),
        help="Portable zip release manifest path to summarize.",
    )
    parser.add_argument(
        "--installer-path",
        default=str(ROOT / "dist" / "installer" / "NASDX-Desktop-Setup.exe"),
        help="Installer executable path to summarize.",
    )
    parser.add_argument(
        "--installer-proof",
        default=str(ROOT / "dist" / "installer" / "NASDX-Desktop-roundtrip-proof.json"),
        help="Installer roundtrip proof path to summarize.",
    )
    parser.add_argument("--strict", action="store_true", help="Treat INCOMPLETE completion-audit items as a failure.")
    args = parser.parse_args(argv)

    evidence = build_release_evidence(
        ROOT,
        package_dir=Path(args.package_dir),
        include_zip=not args.skip_zip,
        zip_path=Path(args.zip_path),
        zip_manifest=Path(args.zip_manifest),
        installer_path=Path(args.installer_path),
        installer_proof=Path(args.installer_proof),
    )
    payload = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.write:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
        print(f"NASDX desktop release evidence written: {output}")
    if args.json or not args.write:
        print(payload)

    summary = evidence["summary"]
    if summary["failed"]:
        return 1
    if args.strict and summary["incomplete"]:
        return 1
    return 0


def _artifact_evidence(
    root: Path,
    *,
    package_dir: Path | None,
    include_zip: bool,
    zip_path: Path | None,
    zip_manifest: Path | None,
    installer_path: Path | None,
    installer_proof: Path | None,
) -> dict[str, Any]:
    package_dir = _resolve_artifact_path(root, package_dir or root / "dist" / "NASDX-Desktop")
    zip_path = _resolve_artifact_path(root, zip_path or root / "dist" / "NASDX-Desktop-portable.zip")
    zip_manifest = _resolve_artifact_path(root, zip_manifest or root / "dist" / "NASDX-Desktop-portable.manifest.json")
    installer = _resolve_artifact_path(root, installer_path or root / "dist" / "installer" / "NASDX-Desktop-Setup.exe")
    installer_proof = _resolve_artifact_path(
        root,
        installer_proof or root / "dist" / "installer" / "NASDX-Desktop-roundtrip-proof.json",
    )
    return {
        "portable_package": _portable_package_evidence(package_dir),
        "portable_zip": _portable_zip_evidence(zip_path, zip_manifest, include_zip=include_zip),
        "installer": _file_hash_evidence(installer) | {"roundtrip_proof": _read_json_summary(installer_proof)},
    }


def _portable_zip_evidence(zip_path: Path, zip_manifest: Path, *, include_zip: bool) -> dict[str, Any]:
    if not include_zip:
        return {
            "exists": False,
            "path": str(zip_path),
            "skipped": True,
            "manifest": {"exists": False, "path": str(zip_manifest), "skipped": True},
            "forbidden_present": [],
        }
    return _file_hash_evidence(zip_path) | {
        "manifest": _read_json_summary(zip_manifest),
        "forbidden_present": _zip_forbidden_entries(zip_path),
    }


def _portable_package_evidence(package_dir: Path) -> dict[str, Any]:
    manifest = _read_json_summary(package_dir / "PACKAGING_MANIFEST.json")
    return {
        "exists": package_dir.exists(),
        "path": str(package_dir),
        "manifest": manifest,
        "forbidden_present": _forbidden_package_paths(package_dir),
        "bundled_python": (package_dir / ".venv" / "Scripts" / "python.exe").exists(),
        "desktop_entry": (package_dir / "启动NASDX桌面.bat").exists(),
        "launcher_exe_entry": (package_dir / "desktop" / "exe_launcher.py").exists(),
    }


def _forbidden_package_paths(package_dir: Path) -> list[str]:
    if not package_dir.exists():
        return []
    matches = set()
    for path in package_dir.rglob("*"):
        try:
            relative = str(path.relative_to(package_dir)).replace("\\", "/")
        except ValueError:
            continue
        if _is_forbidden_release_path(relative):
            matches.add(relative)
    return sorted(matches)


def _zip_forbidden_entries(zip_path: Path) -> list[str]:
    if not zip_path.exists():
        return []
    matches = set()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                normalized = name.strip("/").replace("\\", "/")
                if normalized and _is_forbidden_release_path(normalized):
                    matches.add(normalized)
    except zipfile.BadZipFile:
        return ["<invalid-zip>"]
    return sorted(matches)


def _is_forbidden_release_path(relative_path: str) -> bool:
    normalized = relative_path.strip("/").replace("\\", "/")
    if not normalized:
        return False
    candidates = [normalized]
    parts = normalized.split("/")
    if len(parts) > 1:
        candidates.append("/".join(parts[1:]))
    forbidden_dirs = {
        "__pycache__",
        "reports",
        "desktop_logs",
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "wheelhouse",
    }
    if any(part in forbidden_dirs for part in parts):
        return True
    for candidate in candidates:
        basename = candidate.rsplit("/", 1)[-1]
        for pattern in FORBIDDEN_PACKAGE_PATTERNS:
            clean_pattern = pattern.strip("/")
            if fnmatch(candidate, clean_pattern) or fnmatch(basename, clean_pattern):
                return True
    return False


def _resolve_artifact_path(root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return root / path


def _file_hash_evidence(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    return {
        "exists": True,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _read_json_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"exists": True, "path": str(path), "json_error": str(exc)}
    keys = [
        "schema",
        "name",
        "path_policy",
        "source_root",
        "package_root",
        "skip_dependency_install",
        "include_webview",
        "require_venv",
        "zip_sha256",
        "zip_size_bytes",
        "installer_sha256",
        "installed_smoke",
        "uninstall",
        "check_shortcuts",
    ]
    summary = {key: payload[key] for key in keys if key in payload}
    summary["exists"] = True
    summary["path"] = str(path)
    return summary


def _ignored_path_evidence(root: Path) -> list[dict[str, Any]]:
    paths = [
        "dist/release-evidence/NASDX-desktop-release-evidence.json",
        "dist/NASDX-Desktop",
        "dist/NASDX-Desktop-check",
        "dist/NASDX-Desktop-portable.zip",
        "dist/installer/NASDX-Desktop-Setup.exe",
        "dist/installer/NASDX-Desktop-roundtrip-proof.json",
        "reports/example.json",
        "config.toml",
        ".env",
        "nasdx_history.db",
        "desktop_logs/launcher.log",
    ]
    return [{"path": path, "ignored": _is_git_ignored(root, path)} for path in paths]


def _is_git_ignored(root: Path, path: str) -> bool:
    proc = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def _next_commands() -> list[str]:
    return [
        "python -B run_desktop_release_check.py",
        "powershell -ExecutionPolicy Bypass -File packaging\\windows\\preflight_installer_release.ps1 -RequireVenv",
        "powershell -ExecutionPolicy Bypass -File packaging\\windows\\build_installer.ps1 -SkipPortableBuild",
        "powershell -ExecutionPolicy Bypass -File packaging\\windows\\smoke_installer_roundtrip.ps1 -InstallerPath dist\\installer\\NASDX-Desktop-Setup.exe -AllowInstall -CheckShortcuts -RequireVenv -Timeout 60",
    ]


def _summary(evidence: dict[str, Any]) -> dict[str, int]:
    completion = evidence["completion_audit"]
    doctor = evidence["desktop_doctor"]
    failed = sum(1 for item in completion if item["status"] == FAIL)
    failed += sum(1 for item in doctor if item["status"] == DOCTOR_FAIL)
    incomplete = sum(1 for item in completion if item["status"] == INCOMPLETE)
    warned = sum(1 for item in completion if item["status"] == "WARN")
    warned += sum(1 for item in doctor if item["status"] == "WARN")
    ignored_failures = sum(1 for item in evidence["ignored_paths"] if not item["ignored"])
    package_forbidden_failures = len(evidence["artifacts"]["portable_package"].get("forbidden_present", []))
    zip_forbidden_failures = len(evidence["artifacts"]["portable_zip"].get("forbidden_present", []))
    return {
        "failed": failed + ignored_failures + package_forbidden_failures + zip_forbidden_failures,
        "incomplete": incomplete,
        "warned": warned,
        "ignored_failures": ignored_failures,
        "package_forbidden_failures": package_forbidden_failures,
        "zip_forbidden_failures": zip_forbidden_failures,
    }


if __name__ == "__main__":
    raise SystemExit(main())

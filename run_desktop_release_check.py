"""Run NASDX Windows desktop release checks without installing anything."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parent


@dataclass(frozen=True)
class CommandSpec:
    label: str
    argv: list[str]
    timeout: int


@dataclass(frozen=True)
class CommandResult:
    label: str
    returncode: int
    output_tail: str


def build_commands(
    *,
    full_package: bool = False,
    include_webview: bool = False,
    compile_installer: bool = False,
    zip_package: bool = False,
    include_final_audit: bool = True,
    package_timeout: int | None = None,
    smoke_timeout: int = 90,
    zip_timeout: int = 900,
    audit_timeout: int = 900,
    pip_timeout: int | None = None,
    pip_retries: int | None = None,
    write_evidence: bool = False,
    evidence_output: str = "dist\\release-evidence\\NASDX-desktop-release-evidence.json",
) -> list[CommandSpec]:
    if package_timeout is None:
        package_timeout = 900 if full_package else 240
    package_dir = "dist\\NASDX-Desktop" if full_package else "dist\\NASDX-Desktop-check"

    package_args = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "packaging\\windows\\build_portable.ps1",
        "-OutputDir",
        package_dir,
    ]
    if not full_package:
        package_args.append("-SkipDependencyInstall")
    if include_webview:
        package_args.append("-IncludeWebView")
    if full_package and pip_timeout is not None:
        package_args.extend(["-PipTimeout", str(pip_timeout)])
    if full_package and pip_retries is not None:
        package_args.extend(["-PipRetries", str(pip_retries)])

    smoke_package_args = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "packaging\\windows\\smoke_portable.ps1",
        "-PackageDir",
        package_dir,
        "-Timeout",
        str(smoke_timeout),
    ]
    installed_smoke_args = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "packaging\\windows\\smoke_installed.ps1",
        "-InstallDir",
        package_dir,
        "-Timeout",
        str(smoke_timeout),
    ]
    if full_package:
        smoke_package_args.append("-RequireVenv")
        installed_smoke_args.append("-RequireVenv")

    zip_package_args = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "packaging\\windows\\build_portable_zip.ps1",
        "-PackageDir",
        package_dir,
        "-OutputZip",
        "dist\\NASDX-Desktop-portable.zip",
        "-ChecksumPath",
        "dist\\NASDX-Desktop-portable.zip.sha256",
        "-ManifestPath",
        "dist\\NASDX-Desktop-portable.manifest.json",
    ]
    zip_smoke_args = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "packaging\\windows\\smoke_portable_zip.ps1",
        "-ZipPath",
        "dist\\NASDX-Desktop-portable.zip",
        "-ChecksumPath",
        "dist\\NASDX-Desktop-portable.zip.sha256",
        "-ManifestPath",
        "dist\\NASDX-Desktop-portable.manifest.json",
        "-Timeout",
        str(smoke_timeout),
    ]
    if full_package:
        zip_package_args.append("-RequireVenv")
        zip_smoke_args.append("-RequireVenv")

    installer_args = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "packaging\\windows\\build_installer.ps1",
        "-PackageDir",
        package_dir,
        "-SkipPortableBuild",
    ]
    if not compile_installer:
        installer_args.append("-SkipCompile")

    release_evidence_args = [
        sys.executable,
        "-B",
        "run_desktop_release_evidence.py",
    ]
    if write_evidence:
        release_evidence_args.extend(["--write", "--output", evidence_output])
    else:
        release_evidence_args.append("--json")
    release_evidence_args.extend(["--package-dir", package_dir])
    if zip_package:
        release_evidence_args.extend(
            [
                "--zip-path",
                "dist\\NASDX-Desktop-portable.zip",
                "--zip-manifest",
                "dist\\NASDX-Desktop-portable.manifest.json",
            ]
        )
    else:
        release_evidence_args.append("--skip-zip")

    commands = [
        CommandSpec(
            label="ruff",
            argv=[sys.executable, "-m", "ruff", "check", "--no-cache", "."],
            timeout=120,
        ),
        CommandSpec(
            label="desktop_contracts",
            argv=[
                sys.executable,
                "-m",
                "pytest",
                "tests/test_desktop_launcher_contracts.py",
                "tests/test_desktop_control_contracts.py",
                "tests/test_desktop_packaging_contracts.py",
                "tests/test_desktop_completion_audit_contracts.py",
                "tests/test_desktop_release_evidence_contracts.py",
                "tests/test_delivery_assets_contracts.py",
            ],
            timeout=180,
        ),
        CommandSpec(
            label="security_checks",
            argv=[sys.executable, "-B", "run_security_checks.py", "--skip-optional"],
            timeout=120,
        ),
        CommandSpec(
            label="desktop_doctor",
            argv=[sys.executable, "-B", "run_desktop_doctor.py", "--json"],
            timeout=120,
        ),
        CommandSpec(
            label="desktop_completion_audit",
            argv=[sys.executable, "-B", "run_desktop_completion_audit.py"],
            timeout=120,
        ),
        CommandSpec(label="portable_package", argv=package_args, timeout=package_timeout),
        CommandSpec(
            label="portable_smoke",
            argv=smoke_package_args,
            timeout=smoke_timeout + 60,
        ),
        CommandSpec(
            label="installed_layout_smoke",
            argv=installed_smoke_args,
            timeout=smoke_timeout + 60,
        ),
    ]
    if zip_package:
        commands.extend(
            [
                CommandSpec(label="portable_zip", argv=zip_package_args, timeout=zip_timeout),
                CommandSpec(label="portable_zip_smoke", argv=zip_smoke_args, timeout=zip_timeout),
            ]
        )
    commands.append(CommandSpec(label="installer_inputs", argv=installer_args, timeout=120 if not compile_installer else 300))
    commands.append(
        CommandSpec(
            label="release_evidence",
            argv=release_evidence_args,
            timeout=120,
        )
    )
    if include_final_audit:
        commands.append(
            CommandSpec(
                label="final_audit",
                argv=[sys.executable, "-B", "run_final_audit.py"],
                timeout=audit_timeout,
            )
        )
    return commands


def run_command(spec: CommandSpec) -> CommandResult:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            spec.argv,
            cwd=str(ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=spec.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = _coerce_timeout_output(exc.stdout) + ("\n" + _coerce_timeout_output(exc.stderr) if exc.stderr else "")
        detail = f"timed out after {spec.timeout}s: {' '.join(spec.argv)}"
        return CommandResult(label=spec.label, returncode=124, output_tail=_tail(detail + "\n" + output))
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return CommandResult(label=spec.label, returncode=proc.returncode, output_tail=_tail(output))


def run_release_check(
    *,
    full_package: bool = False,
    include_webview: bool = False,
    compile_installer: bool = False,
    zip_package: bool = False,
    include_final_audit: bool = True,
    fail_fast: bool = False,
    package_timeout: int | None = None,
    smoke_timeout: int = 90,
    zip_timeout: int = 900,
    audit_timeout: int = 900,
    pip_timeout: int | None = None,
    pip_retries: int | None = None,
    write_evidence: bool = False,
    evidence_output: str = "dist\\release-evidence\\NASDX-desktop-release-evidence.json",
) -> list[CommandResult]:
    results: list[CommandResult] = []
    for spec in build_commands(
        full_package=full_package,
        include_webview=include_webview,
        compile_installer=compile_installer,
        zip_package=zip_package,
        include_final_audit=include_final_audit,
        package_timeout=package_timeout,
        smoke_timeout=smoke_timeout,
        zip_timeout=zip_timeout,
        audit_timeout=audit_timeout,
        pip_timeout=pip_timeout,
        pip_retries=pip_retries,
        write_evidence=write_evidence,
        evidence_output=evidence_output,
    ):
        result = run_command(spec)
        results.append(result)
        if fail_fast and result.returncode != 0:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NASDX Windows desktop release checks.")
    parser.add_argument("--full-package", action="store_true", help="install dependencies into dist\\NASDX-Desktop\\.venv")
    parser.add_argument("--include-webview", action="store_true", help="include optional pywebview dependency during package build")
    parser.add_argument("--compile-installer", action="store_true", help="compile installer with Inno Setup; never runs the installer")
    parser.add_argument("--zip-package", action="store_true", help="create and smoke-test dist\\NASDX-Desktop-portable.zip")
    parser.add_argument("--skip-final-audit", action="store_true", help="skip run_final_audit.py")
    parser.add_argument("--fail-fast", action="store_true", help="stop after the first failed check")
    parser.add_argument("--package-timeout", type=int, default=None, help="portable package build timeout in seconds")
    parser.add_argument("--smoke-timeout", type=int, default=90, help="headless desktop smoke timeout in seconds")
    parser.add_argument("--zip-timeout", type=int, default=900, help="portable zip build and zip smoke timeout in seconds")
    parser.add_argument("--audit-timeout", type=int, default=900, help="final audit timeout in seconds")
    parser.add_argument("--pip-timeout", type=int, default=None, help="pip network timeout passed to build_portable.ps1")
    parser.add_argument("--pip-retries", type=int, default=None, help="pip retry count passed to build_portable.ps1")
    parser.add_argument(
        "--write-evidence",
        action="store_true",
        help="write release evidence JSON after release artifacts instead of printing it",
    )
    parser.add_argument(
        "--evidence-output",
        default="dist\\release-evidence\\NASDX-desktop-release-evidence.json",
        help="output path used with --write-evidence",
    )
    args = parser.parse_args()

    results = run_release_check(
        full_package=args.full_package,
        include_webview=args.include_webview,
        compile_installer=args.compile_installer,
        zip_package=args.zip_package,
        include_final_audit=not args.skip_final_audit,
        fail_fast=args.fail_fast,
        package_timeout=args.package_timeout,
        smoke_timeout=args.smoke_timeout,
        zip_timeout=args.zip_timeout,
        audit_timeout=args.audit_timeout,
        pip_timeout=args.pip_timeout,
        pip_retries=args.pip_retries,
        write_evidence=args.write_evidence,
        evidence_output=args.evidence_output,
    )
    failed = 0
    for result in results:
        status = "PASS" if result.returncode == 0 else "FAIL"
        if result.returncode != 0:
            failed += 1
        print(f"[{status}] {result.label}")
        if result.output_tail:
            print(result.output_tail)
            print()

    passed = sum(1 for item in results if item.returncode == 0)
    print(f"summary: passed={passed} failed={failed}")
    return 1 if failed else 0


def _tail(text: str, max_lines: int = 80) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def _coerce_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


if __name__ == "__main__":
    raise SystemExit(main())

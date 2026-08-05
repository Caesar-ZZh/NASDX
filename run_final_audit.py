"""
NASDX final delivery audit.

This script checks the investment-facing delivery chain without requiring a
live LLM call or a fresh market-data pull. It is meant to be run before handing
the project to a user as the "final version" for research guidance.
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import requests
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nasdx.secret_scan import format_findings, scan_text  # noqa: E402


def main() -> int:
    checks: list[tuple[str, Callable[[], str]]] = [
        ("Python 语法", check_python_syntax),
        ("硬编码 API Key", check_no_hardcoded_api_keys),
        ("依赖清单", check_delivery_assets),
        ("桌面交付资产", check_desktop_delivery_assets),
        ("行情文件指标覆盖", check_market_data_contract),
        ("Serenity 五维 Agent", check_serenity_agent),
        ("研究阶段并发/HTTP隔离", check_architecture_optimization_contract),
        ("LLM结构化输出契约", check_llm_structured_output_contract),
        ("单票决策契约", check_single_name_contract),
        ("无API规则深度报告", check_rule_based_deep_report),
        ("组合路线契约", check_portfolio_contract),
        ("最终投资简报契约", check_investment_brief_contract),
        ("资金仓位换算契约", check_position_sizing_contract),
        ("建议漂移追踪契约", check_recommendation_tracker_contract),
        ("建议结果复盘契约", check_recommendation_review_contract),
        ("真实账户复盘契约", check_account_review_contract),
        ("复盘快照包契约", check_review_snapshot_contract),
        ("SQLite历史库契约", check_history_store_contract),
        ("一键工作流 Dry-run", check_workflow_dry_run),
        ("Streamlit状态边界", check_streamlit_state_boundaries),
        ("网页投资路线入口", check_streamlit_markers),
        ("README/决策文档", check_documentation),
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    print("NASDX final audit")
    print("=" * 72)
    for name, func in checks:
        try:
            detail = func()
        except Exception as exc:  # noqa: BLE001 - audit should report context.
            failed.append((name, str(exc)))
            print(f"[FAIL] {name}: {exc}")
        else:
            passed += 1
            print(f"[PASS] {name}: {detail}")

    print("=" * 72)
    print(f"通过: {passed}  失败: {len(failed)}")
    if failed:
        print("失败项:")
        for name, detail in failed:
            print(f"- {name}: {detail}")
        return 1
    return 0


def check_python_syntax() -> str:
    files = [path for path in _project_files((".py",)) if not _is_ignored(path)]
    failures = []
    for path in files:
        try:
            ast.parse(_read_text(path), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{_rel(path)}:{exc.lineno}:{exc.msg}")
    if failures:
        raise AssertionError("; ".join(failures[:5]))
    return f"{len(files)} 个 Python 文件可解析"


def check_no_hardcoded_api_keys() -> str:
    """Multi-provider secret scan sharing nasdx/secret_scan.py rules.

    Findings are reported redacted (rule / path / line / fingerprint) so the
    audit log never echoes a credential.
    """
    suffixes = (".py", ".md", ".toml", ".bat", ".json", ".yml", ".yaml", ".ps1")
    findings = []
    scanned = 0
    for path in _project_files(suffixes):
        if _is_ignored(path):
            continue
        scanned += 1
        findings.extend(scan_text(_read_text(path), _rel(path)))
    if findings:
        raise AssertionError("发现疑似真实密钥: " + format_findings(findings))
    return f"{scanned} 个文件未发现疑似真实密钥（多 Provider 规则）"


def check_delivery_assets() -> str:
    requirements_path = ROOT / "requirements_nasdx.txt"
    if not requirements_path.exists():
        raise AssertionError("缺少 requirements_nasdx.txt")
    text = _read_text(requirements_path)
    required_packages = [
        "akshare",
        "pandas",
        "numpy",
        "requests",
        "openai",
        "pydantic",
        "streamlit",
        "tdxrs",
    ]
    missing = [name for name in required_packages if name not in text]
    if missing:
        raise AssertionError("requirements_nasdx.txt 缺少依赖: " + ", ".join(missing))

    proc = subprocess.run(
        ["git", "check-ignore", "-q", "requirements_nasdx.txt"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode == 0:
        raise AssertionError("requirements_nasdx.txt 被 .gitignore 忽略")
    return f"requirements_nasdx.txt 包含 {len(required_packages)} 项核心依赖且可入库"


def check_desktop_delivery_assets() -> str:
    required_files = [
        "desktop/launcher.py",
        "desktop/control.py",
        "desktop/control_panel.py",
        "desktop/exe_launcher.py",
        "desktop/runtime.py",
        "desktop/paths.py",
        "desktop/config.py",
        "desktop/doctor.py",
        "desktop/webview_shell.py",
        "requirements_desktop.txt",
        "启动NASDX桌面.bat",
        "packaging/windows/build_launcher_exe.ps1",
        "packaging/windows/build_portable.ps1",
        "packaging/windows/build_portable_zip.ps1",
        "packaging/windows/hash_utils.ps1",
        "packaging/windows/create_shortcuts.ps1",
        "packaging/windows/smoke_portable.ps1",
        "packaging/windows/smoke_portable_zip.ps1",
        "packaging/windows/smoke_installed.ps1",
        "packaging/windows/smoke_installer_roundtrip.ps1",
        "packaging/windows/install_inno_setup.ps1",
        "packaging/windows/preflight_installer_release.ps1",
        "packaging/windows/build_wheelhouse.ps1",
        "packaging/windows/build_installer.ps1",
        "packaging/windows/NASDX-Desktop.iss",
        "packaging/windows/constraints-win.txt",
        "packaging/windows/requirements-win-core.lock",
        "packaging/windows/requirements-win-webview.lock",
        "packaging/windows/toolchain-win.json",
        "docs/WINDOWS_DESKTOP.md",
        "run_desktop_release_check.py",
        "run_desktop_doctor.py",
        "run_desktop_completion_audit.py",
        "run_desktop_release_evidence.py",
        "run_security_checks.py",
        ".github/workflows/windows-desktop.yml",
    ]
    missing_files = [path for path in required_files if not (ROOT / path).exists()]
    if missing_files:
        raise AssertionError("缺少桌面交付文件: " + ", ".join(missing_files))

    desktop_guide = _read_text(ROOT / "docs" / "WINDOWS_DESKTOP.md")
    required_guide_markers = [
        "desktop\\control_panel.py",
        "Start",
        "Stop",
        "Open App",
        "Settings",
        "Logs",
        "Data Refresh",
        "create_shortcuts.ps1",
        "run_desktop_doctor.py",
        "%APPDATA%\\NASDX\\config.toml",
        "build_portable.ps1 -SkipDependencyInstall",
        "build_launcher_exe.ps1",
        "build_portable_zip.ps1",
        "smoke_portable.ps1 -PackageDir dist\\NASDX-Desktop",
        "smoke_portable_zip.ps1",
        "NASDX-Desktop-portable.zip.sha256",
        "NASDX-Desktop-portable.manifest.json",
        "smoke_installed.ps1",
        "smoke_installer_roundtrip.ps1",
        "NASDX-Desktop-roundtrip-proof.json",
        "install_inno_setup.ps1",
        "preflight_installer_release.ps1",
        "-Install -AcceptAgreements",
        "build_installer.ps1 -SkipPortableBuild -SkipCompile",
        "run_desktop_release_check.py",
        "run_desktop_completion_audit.py",
        "run_desktop_release_evidence.py",
        "NASDX-desktop-release-evidence.json",
        "--package-dir",
        "--write-evidence",
        "--evidence-output",
        "--skip-zip",
        "forbidden_present",
        "package_forbidden_failures",
        "zip_forbidden_failures",
        "__pycache__",
        "*.pyc",
        "--package-timeout",
        "--zip-timeout",
        "--pip-timeout",
        "--pip-retries",
        "run_security_checks.py",
        "Do not commit",
    ]
    missing_guide_markers = [marker for marker in required_guide_markers if marker not in desktop_guide]
    if missing_guide_markers:
        raise AssertionError("桌面文档缺少: " + ", ".join(missing_guide_markers))

    launcher_text = _read_text(ROOT / "desktop" / "launcher.py")
    control_text = _read_text(ROOT / "desktop" / "control.py")
    exe_launcher_text = _read_text(ROOT / "desktop" / "exe_launcher.py")
    doctor_text = _read_text(ROOT / "desktop" / "doctor.py")
    root_bat_text = _read_text(ROOT / "启动NASDX桌面.bat")
    release_text = _read_text(ROOT / "run_desktop_release_check.py")
    completion_text = _read_text(ROOT / "run_desktop_completion_audit.py")
    release_evidence_text = _read_text(ROOT / "run_desktop_release_evidence.py")
    security_text = _read_text(ROOT / "run_security_checks.py")
    secret_scan_text = _read_text(ROOT / "nasdx" / "secret_scan.py")
    security_ci_text = _read_text(ROOT / ".github" / "workflows" / "security.yml")
    installer_text = _read_text(ROOT / "packaging" / "windows" / "NASDX-Desktop.iss")
    portable_text = _read_text(ROOT / "packaging" / "windows" / "build_portable.ps1")
    launcher_exe_text = _read_text(ROOT / "packaging" / "windows" / "build_launcher_exe.ps1")
    zip_text = _read_text(ROOT / "packaging" / "windows" / "build_portable_zip.ps1")
    hash_utils_text = _read_text(ROOT / "packaging" / "windows" / "hash_utils.ps1")
    shortcut_text = _read_text(ROOT / "packaging" / "windows" / "create_shortcuts.ps1")
    portable_smoke_text = _read_text(ROOT / "packaging" / "windows" / "smoke_portable.ps1")
    zip_smoke_text = _read_text(ROOT / "packaging" / "windows" / "smoke_portable_zip.ps1")
    installed_smoke_text = _read_text(ROOT / "packaging" / "windows" / "smoke_installed.ps1")
    roundtrip_smoke_text = _read_text(ROOT / "packaging" / "windows" / "smoke_installer_roundtrip.ps1")
    inno_bootstrap_text = _read_text(ROOT / "packaging" / "windows" / "install_inno_setup.ps1")
    installer_preflight_text = _read_text(ROOT / "packaging" / "windows" / "preflight_installer_release.ps1")
    ci_text = _read_text(ROOT / ".github" / "workflows" / "windows-desktop.yml")

    required_source_markers = [
        (launcher_text, "start_streamlit"),
        (launcher_text, "--headless-smoke"),
        (control_text, "CONTROL_ACTIONS"),
        (control_text, "fetch_stock_data.py"),
        (exe_launcher_text, "control_panel.py"),
        (exe_launcher_text, ".venv"),
        (exe_launcher_text, "subprocess.run"),
        (exe_launcher_text, "launcher.py"),
        (doctor_text, "run_doctor"),
        (doctor_text, "CORE_MODULES"),
        (doctor_text, "--check-write"),
        (doctor_text, "loaded keys"),
        (doctor_text, "ISCC.exe"),
        (root_bat_text, "desktop\\control_panel.py"),
        (root_bat_text, "desktop\\launcher.py --webview --page plan"),
        (root_bat_text, "%*"),
        (release_text, "build_portable.ps1"),
        (release_text, "smoke_portable.ps1"),
        (release_text, "run_security_checks.py"),
        (release_text, "run_desktop_doctor.py"),
        (release_text, "run_desktop_completion_audit.py"),
        (release_text, "run_desktop_release_evidence.py"),
        (release_text, "--zip-package"),
        (release_text, "release_evidence"),
        (release_text, "--package-dir"),
        (release_text, "--skip-zip"),
        (release_text, "--write-evidence"),
        (release_text, "--evidence-output"),
        (release_text, "portable_zip"),
        (release_text, "portable_zip_smoke"),
        (release_text, "NASDX-Desktop-portable.zip.sha256"),
        (release_text, "NASDX-Desktop-portable.manifest.json"),
        (release_text, "desktop_doctor"),
        (release_text, "desktop_completion_audit"),
        (release_text, "TimeoutExpired"),
        (release_text, "-PipTimeout"),
        (release_text, "-PipRetries"),
        (release_text, "--package-timeout"),
        (release_text, "--zip-timeout"),
        (release_text, "-RequireVenv"),
        (release_text, "--skip-optional"),
        (release_text, "-SkipCompile"),
        (release_text, "never runs the installer"),
        (completion_text, "preserved_entrypoints"),
        (completion_text, "desktop_launcher_mvp"),
        (completion_text, "portable_runtime_bundle"),
        (completion_text, "generated_files_excluded"),
        (completion_text, "NASDX-Desktop-portable.zip"),
        (completion_text, "NASDX-Desktop-portable.zip.sha256"),
        (completion_text, "NASDX-Desktop-portable.manifest.json"),
        (completion_text, "installer_roundtrip"),
        (completion_text, "NASDX-Desktop-roundtrip-proof.json"),
        (completion_text, "nasdx_installer_roundtrip_proof.v1"),
        (completion_text, "installer_sha256"),
        (completion_text, "INCOMPLETE"),
        (completion_text, "run_desktop_release_evidence.py"),
        (completion_text, "dist/release-evidence/NASDX-desktop-release-evidence.json"),
        (completion_text, "_package_cache_artifacts"),
        (completion_text, "__pycache__"),
        (completion_text, "*.pyc"),
        (release_evidence_text, "nasdx_desktop_release_evidence.v1"),
        (release_evidence_text, "run_completion_audit"),
        (release_evidence_text, "run_doctor"),
        (release_evidence_text, "--package-dir"),
        (release_evidence_text, "--skip-zip"),
        (release_evidence_text, "dist/release-evidence/NASDX-desktop-release-evidence.json"),
        (release_evidence_text, "NASDX-Desktop-roundtrip-proof.json"),
        (release_evidence_text, "next_commands"),
        (release_evidence_text, "FORBIDDEN_PACKAGE_PATTERNS"),
        (release_evidence_text, "forbidden_present"),
        (release_evidence_text, "package_forbidden_failures"),
        (release_evidence_text, "zip_forbidden_failures"),
        (release_evidence_text, "_zip_forbidden_entries"),
        (release_evidence_text, "path_policy"),
        (release_evidence_text, "source_root"),
        (release_evidence_text, "package_root"),
        (release_evidence_text, "__pycache__"),
        (release_evidence_text, "*.pyc"),
        (security_text, "secret_scan"),
        (security_text, "pip-audit"),
        (security_text, "bandit"),
        (security_text, "detect-secrets"),
        (security_text, "--run-optional"),
        (security_text, "--exclude-standard"),
        (security_text, "--history"),
        (security_text, "secret_history_scan"),
        (secret_scan_text, "github-token"),
        (secret_scan_text, "aws-access-key-id"),
        (secret_scan_text, "private-key-block"),
        (secret_scan_text, "generic-assigned-secret"),
        (secret_scan_text, "def scan_history"),
        (secret_scan_text, "secret_scan_allowlist.toml"),
        (security_ci_text, "fetch-depth: 0"),
        (security_ci_text, "run_security_checks.py --skip-optional --history"),
        (security_ci_text, "gitleaks"),
        (installer_text, "启动NASDX桌面.bat"),
        (installer_text, "Do not delete local user runtime state on uninstall"),
        (portable_text, "desktop\\control_panel.py"),
        (portable_text, "desktop\\exe_launcher.py"),
        (portable_text, "packaging/windows/build_launcher_exe.ps1"),
        (portable_text, "%*"),
        (portable_text, "packaging/windows/create_shortcuts.ps1"),
        (portable_text, "packaging/windows/smoke_installed.ps1"),
        (portable_text, "packaging/windows/smoke_installer_roundtrip.ps1"),
        (portable_text, "models/signal_confidence.json"),
        (portable_text, "path_policy"),
        (portable_text, "relative-or-redacted"),
        (portable_text, "<source-checkout>"),
        (portable_text, "package_root"),
        (portable_text, "Convert-ToPackageManifestPath"),
        (portable_text, "scrubbed_patterns"),
        (portable_text, "Remove-PackageExcludedArtifacts"),
        (portable_text, "__pycache__/"),
        (portable_text, "*.pyc"),
        (portable_text, "desktop_logs/"),
        (portable_text, "wheelhouse/"),
        (zip_text, "Compress-Archive"),
        (zip_text, "Get-ForbiddenPackageArtifacts"),
        (zip_text, "forbidden artifact before zip"),
        (zip_text, "scrubbed_patterns"),
        (zip_text, "path_policy"),
        (zip_text, "source_root"),
        (zip_text, "package_root"),
        (zip_text, "__pycache__"),
        (zip_text, "*.pyc"),
        (launcher_exe_text, "PyInstaller"),
        (launcher_exe_text, "SkipBuild"),
        (launcher_exe_text, "desktop\\exe_launcher.py"),
        (launcher_exe_text, "does not bundle app.py"),
        (zip_text, "tar.exe"),
        (zip_text, "NASDX-Desktop-portable.zip"),
        (zip_text, "Get-NasdxSha256"),
        (hash_utils_text, "System.Security.Cryptography.SHA256"),
        (zip_text, "SHA256"),
        (zip_text, "nasdx_portable_release.v1"),
        (zip_text, "zip_sha256"),
        (zip_text, "zip_size_bytes"),
        (zip_text, "RequireVenv"),
        (zip_text, "Refusing to write zip inside package directory"),
        (shortcut_text, "plan-only mode"),
        (shortcut_text, "Pass -Apply"),
        (shortcut_text, "CreateShortcut"),
        (shortcut_text, "启动NASDX桌面.bat"),
        (shortcut_text, "Start Menu\\Programs\\NASDX Desktop"),
        (portable_smoke_text, "run_desktop_doctor.py"),
        (portable_smoke_text, "run_desktop_completion_audit.py"),
        (portable_smoke_text, "create_shortcuts.ps1"),
        (portable_smoke_text, "BatchDryRun"),
        (portable_smoke_text, "启动NASDX桌面.bat"),
        (portable_smoke_text, "RequireVenv"),
        (portable_smoke_text, "Smoke python"),
        (portable_smoke_text, "required_files"),
        (portable_smoke_text, "preserved_entrypoints"),
        (portable_smoke_text, "installer_roundtrip"),
        (portable_smoke_text, "plan-only mode"),
        (zip_smoke_text, "Expand-Archive"),
        (zip_smoke_text, "tar.exe"),
        (zip_smoke_text, "Get-NasdxSha256"),
        (zip_smoke_text, "Portable zip checksum verified"),
        (zip_smoke_text, "Portable zip manifest verified"),
        (zip_smoke_text, "smoke_installed.ps1"),
        (zip_smoke_text, "NASDX portable zip smoke passed"),
        (zip_smoke_text, "Get-ForbiddenPackageArtifacts"),
        (zip_smoke_text, "forbidden runtime/cache/build artifact"),
        (zip_smoke_text, "__pycache__"),
        (zip_smoke_text, "*.pyc"),
        (installed_smoke_text, "Programs\\NASDX Desktop"),
        (installed_smoke_text, "--headless-smoke"),
        (installed_smoke_text, "Data Refresh"),
        (installed_smoke_text, "run_desktop_doctor.py"),
        (installed_smoke_text, "run_desktop_completion_audit.py"),
        (installed_smoke_text, "create_shortcuts.ps1"),
        (installed_smoke_text, "BatchDryRun"),
        (installed_smoke_text, "启动NASDX桌面.bat"),
        (installed_smoke_text, "RequireVenv"),
        (installed_smoke_text, "Smoke python"),
        (installed_smoke_text, "required_files"),
        (installed_smoke_text, "preserved_entrypoints"),
        (installed_smoke_text, "installer_roundtrip"),
        (installed_smoke_text, "plan-only mode"),
        (installed_smoke_text, "config.toml"),
        (roundtrip_smoke_text, "AllowInstall"),
        (roundtrip_smoke_text, "plan-only mode"),
        (roundtrip_smoke_text, "NASDX-Desktop-Setup.exe"),
        (roundtrip_smoke_text, "smoke_installed.ps1"),
        (roundtrip_smoke_text, "RequireVenv"),
        (roundtrip_smoke_text, "ProofPath"),
        (roundtrip_smoke_text, "nasdx_installer_roundtrip_proof.v1"),
        (roundtrip_smoke_text, "installer_sha256"),
        (roundtrip_smoke_text, "Roundtrip proof written"),
        (roundtrip_smoke_text, "unins*.exe"),
        (roundtrip_smoke_text, "NASDX installer roundtrip passed"),
        (inno_bootstrap_text, "JRSoftware.InnoSetup"),
        (inno_bootstrap_text, "winget"),
        (inno_bootstrap_text, "plan-only mode"),
        (inno_bootstrap_text, "AcceptAgreements"),
        (inno_bootstrap_text, "-Install -AcceptAgreements"),
        (inno_bootstrap_text, "ISCC.exe"),
        (installer_preflight_text, "preflight"),
        (installer_preflight_text, "Strict"),
        (installer_preflight_text, "RequireVenv"),
        (installer_preflight_text, "Get-NasdxSha256"),
        (installer_preflight_text, "nasdx_portable_release.v1"),
        (installer_preflight_text, "build_installer.ps1 -SkipPortableBuild"),
        (installer_preflight_text, "smoke_installer_roundtrip.ps1"),
        (installer_preflight_text, "NASDX-Desktop-roundtrip-proof.json"),
        (ci_text, "windows-latest"),
        (ci_text, "run_desktop_release_check.py --skip-final-audit --fail-fast"),
        (ci_text, "requirements-dev.txt"),
    ]
    missing_source_markers = [marker for text, marker in required_source_markers if marker not in text]
    if missing_source_markers:
        raise AssertionError("桌面交付源码缺少标记: " + ", ".join(missing_source_markers))

    ignored_paths = [
        "dist/NASDX-Desktop",
        "dist/NASDX-Desktop-portable.zip",
        "dist/NASDX-Desktop-portable.zip.sha256",
        "dist/NASDX-Desktop-portable.manifest.json",
        "dist/installer/NASDX-Desktop-Setup.exe",
        "dist/installer/NASDX-Desktop-roundtrip-proof.json",
        "dist/release-evidence/NASDX-desktop-release-evidence.json",
        "wheelhouse/example.whl",
        "desktop_logs/launcher.log",
        "models/signal_confidence.json",
    ]
    not_ignored = []
    for path in ignored_paths:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            not_ignored.append(path)
    if not_ignored:
        raise AssertionError("桌面生成物未被忽略: " + ", ".join(not_ignored))

    return f"{len(required_files)} 个桌面交付文件、文档标记和生成物忽略规则已验证"


def check_market_data_contract() -> str:
    files = sorted(ROOT.glob("stock_data_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise AssertionError("缺少 stock_data_YYYYMMDD.json")
    data = json.loads(_read_text(files[0]))
    stocks = []
    etfs = []
    for sector in data.get("sectors", []):
        stocks.extend(sector.get("stocks", []))
        etfs.extend(sector.get("etfs", []))
    if not stocks:
        raise AssertionError("行情文件缺少个股列表")
    missing_stocks = [f"{item.get('code')} {item.get('name')}" for item in stocks if not item.get("indicators")]
    missing_etfs = [f"{item.get('code')} {item.get('name')}" for item in etfs if not item.get("indicators")]
    if missing_stocks:
        raise AssertionError("个股指标为空: " + ", ".join(missing_stocks[:5]))
    if missing_etfs:
        raise AssertionError("ETF/LOF 指标为空: " + ", ".join(missing_etfs[:5]))
    if not any(item.get("data_source") for item in stocks + etfs):
        raise AssertionError("行情文件未记录数据源")
    return f"{len(stocks)} 只个股和 {len(etfs)} 只 ETF/LOF 均有技术指标"


def check_serenity_agent() -> str:
    agent_path = ROOT / "nasdx" / "agents" / "chokepoint.py"
    research_path = ROOT / "nasdx" / "environments" / "research.py"
    if not agent_path.exists():
        raise AssertionError("缺少 nasdx/agents/chokepoint.py")
    research_text = _read_text(research_path)
    if "ChokepointAgent" not in research_text:
        raise AssertionError("研究环境未接入 ChokepointAgent")
    agent_text = _read_text(agent_path)
    required = ["供应链", "需求", "贝叶斯"]
    missing = [word for word in required if word not in agent_text]
    if missing:
        raise AssertionError("ChokepointAgent 缺少关键框架: " + ", ".join(missing))
    return "供应链瓶颈 Agent 已进入研究环境"


def check_architecture_optimization_contract() -> str:
    from nasdx.environments.research import ResearchEnvironment
    from nasdx.schema import AnalysisResult

    class SleepingAgent:
        def __init__(self, dimension: str):
            self.dimension = dimension

        def run(self, stock_code: str, stock_data: dict) -> AnalysisResult:
            time.sleep(0.15)
            return AnalysisResult(
                agent_name=f"{self.dimension}_agent",
                dimension=self.dimension,
                conclusion=f"{stock_code} {self.dimension}",
                signal="neutral",
                confidence=0.5,
            )

    env = ResearchEnvironment(max_steps=1, delay=0, max_workers=5)
    env.agents = {dim: SleepingAgent(dim) for dim, _label in env.AGENT_ORDER}
    started = time.perf_counter()
    results = env.run("000001", {"name": "平安银行"}, verbose=False)
    elapsed = time.perf_counter() - started
    if list(results) != [dim for dim, _label in env.AGENT_ORDER]:
        raise AssertionError("并发结果未按 AGENT_ORDER 输出")
    if elapsed >= 0.45:
        raise AssertionError(f"研究阶段未并发执行，耗时 {elapsed:.3f}s")

    app_text = _read_text(ROOT / "app.py")
    banned = ["_req.get = _patched_get", "_real_get = _req.get", "import requests as _req"]
    found = [item for item in banned if item in app_text]
    if found:
        raise AssertionError("app.py 仍存在全局 requests monkey patch: " + ", ".join(found))

    data_modules = ("fetch_stock_data", "quant.data", "quant.patch_requests")
    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
    original_get = requests.get
    original_session_get = requests.Session.get
    original_env = {key: os.environ.get(key) for key in proxy_keys}
    sentinel_env = {key: "http://127.0.0.1:9000" for key in proxy_keys}
    try:
        os.environ.update(sentinel_env)
        for module_name in data_modules:
            sys.modules.pop(module_name, None)
            importlib.import_module(module_name)
        if requests.get is not original_get or requests.Session.get is not original_session_get:
            raise AssertionError("数据模块导入时修改了 requests 全局方法")
        changed = [key for key, expected in sentinel_env.items() if os.environ.get(key) != expected]
        if changed:
            raise AssertionError("数据模块导入时修改了代理环境变量: " + ", ".join(changed))
    finally:
        requests.get = original_get
        requests.Session.get = original_session_get
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return f"5 Agent 并发耗时 {elapsed:.3f}s，HTTP导入隔离已验证"


def check_llm_structured_output_contract() -> str:
    from nasdx.agents.technical import TechnicalAgent
    from nasdx.llm import extract_json_payload

    payload = extract_json_payload(
        """
        ```json
        {"signal":"bullish","confidence":0.81,"conclusion":"结构化结论","key_points":["JSON字段"]}
        ```
        """
    )
    if payload.get("signal") != "bullish" or payload.get("confidence") != 0.81:
        raise AssertionError("LLM JSON 提取失败")

    agent = TechnicalAgent()

    def fake_ask(prompt: str, temperature: float = 0.3) -> str:
        if '"signal": "bullish|bearish|neutral"' not in prompt:
            raise AssertionError("Agent prompt 未追加结构化 JSON 契约")
        return (
            "```json\n"
            '{"signal":"bearish","confidence":0.92,"conclusion":"结构化技术结论","key_points":["JSON优先"]}'
            "\n```"
        )

    agent._ask = fake_ask
    result = agent._analyze(
        "603501",
        {
            "name": "韦尔股份",
            "sector_name": "半导体",
            "indicators": {"close": 100, "ma5": 98, "ma20": 105, "rsi": 42, "macd_bar": -0.1, "vol_ratio": 0.8},
        },
    )
    if result.signal != "bearish" or result.confidence != 0.92:
        raise AssertionError("Agent 未优先使用结构化 JSON 信号")
    if result.conclusion != "结构化技术结论" or "JSON优先" not in result.key_points:
        raise AssertionError("Agent 未使用结构化 conclusion/key_points")

    bypasses = []
    for path in (ROOT / "nasdx" / "agents").glob("*.py"):
        if path.name == "base.py":
            continue
        text = _read_text(path)
        if "response = self._ask(prompt" in text:
            bypasses.append(_rel(path))
    if bypasses:
        raise AssertionError("Agent 绕过结构化输出契约: " + ", ".join(bypasses))
    return "JSON提取、Agent结构化消费和静态绕过检查通过"


def check_single_name_contract() -> str:
    from nasdx.decision import RISK_PROFILES
    from nasdx.schema import FinalReport

    if set(RISK_PROFILES) != {"conservative", "balanced", "aggressive"}:
        raise AssertionError("风险画像不是保守/均衡/进取三档")
    fields = getattr(FinalReport, "model_fields", None) or getattr(FinalReport, "__fields__", {})
    required = {"decision_plan", "data_quality"}
    missing = required - set(fields)
    if missing:
        raise AssertionError("FinalReport 缺少字段: " + ", ".join(sorted(missing)))
    return "风险画像、行动计划、数据状态字段齐全"


def check_rule_based_deep_report() -> str:
    from nasdx.rule_based_analysis import build_rule_based_report

    report = build_rule_based_report("603501", risk_profile="balanced")
    required_dims = {"technical", "fund_flow", "risk", "sector", "chokepoint", "synthesis"}
    missing = required_dims - set(report.research_results)
    if missing:
        raise AssertionError("规则深度报告缺少维度: " + ", ".join(sorted(missing)))
    decision = report.decision_plan
    if not decision.get("entry_conditions") or not decision.get("exit_conditions"):
        raise AssertionError("规则深度报告未生成入场/退出条件")
    if report.data_quality.get("analysis_mode") != "rules":
        raise AssertionError("规则深度报告未标记 analysis_mode=rules")
    if "研究辅助" not in decision.get("note", ""):
        raise AssertionError("规则深度报告缺少投资边界")
    return f"{report.stock_code} {report.final_signal}，看多占比 {report.bullish_pct:.1f}%"


def check_portfolio_contract() -> str:
    from nasdx.portfolio import build_portfolio_plan

    required_fields = {
        "allocation",
        "core_candidates",
        "satellite_candidates",
        "watchlist",
        "trim_or_avoid",
        "next_actions",
        "future_scenarios",
        "decision_rules",
        "monitoring_checklist",
        "data_quality",
        "source_files",
        "disclaimer",
    }
    gates = set()
    for profile in ("conservative", "balanced", "aggressive"):
        plan = build_portfolio_plan(risk_profile=profile)
        missing = required_fields - set(plan)
        if missing:
            raise AssertionError(f"{profile} 组合路线缺少字段: {', '.join(sorted(missing))}")
        if len(plan["future_scenarios"]) < 3:
            raise AssertionError(f"{profile} 缺少未来情景推演")
        if len(plan["decision_rules"]) < 5:
            raise AssertionError(f"{profile} 缺少执行规则")
        if len(plan["monitoring_checklist"]) < 3:
            raise AssertionError(f"{profile} 缺少监控清单")
        if "不保证收益" not in str(plan["disclaimer"]):
            raise AssertionError(f"{profile} 缺少研究辅助免责声明")
        bad_candidates = [
            f"{item.get('code')} {item.get('name')}"
            for item in plan["core_candidates"] + plan["satellite_candidates"]
            if item.get("action") == "回避/减仓"
        ]
        if bad_candidates:
            raise AssertionError(f"{profile} 回避/减仓标的仍在主候选池: " + ", ".join(bad_candidates[:5]))
        etf_path = str(plan["source_files"].get("etf_scan") or "")
        if "etf50_quant" in etf_path:
            raise AssertionError("组合路线误用了 etf50_quant 文件")
        source_files = plan["source_files"]
        active_reports = set((source_files.get("deep_reports") or {}).keys())
        stale_reports = set((source_files.get("stale_deep_reports") or {}).keys())
        overlap = active_reports & stale_reports
        if overlap:
            raise AssertionError("过期深度报告仍被当作可用报告: " + ", ".join(sorted(overlap)))
        deep_quality = plan["data_quality"].get("deep_reports", {})
        if stale_reports and deep_quality.get("stale_count", 0) < len(stale_reports):
            raise AssertionError("深度报告数据状态未统计过期报告")
        stock_quality = plan["data_quality"].get("stock_scan", {})
        if stock_quality.get("status") == "low_coverage":
            allocation = plan["allocation"]
            if plan["action_gate"] != "position_cap":
                raise AssertionError("个股扫描低覆盖时未触发仓位闸门")
            if allocation.get("max_total") != "0%-25%" or allocation.get("stock_budget") != "0%":
                raise AssertionError("个股扫描低覆盖时未强制关闭个股卫星预算")
            if plan["satellite_candidates"]:
                raise AssertionError("个股扫描低覆盖时仍生成个股卫星候选")
            if not any("覆盖率不足" in item for item in plan["next_actions"]):
                raise AssertionError("个股扫描低覆盖时未给出修复数据源动作")
        gates.add(plan["action_gate"])
        if plan["action_gate"] == "refresh_required":
            allocation = plan["allocation"]
            if allocation.get("max_total") != "0%-10%" or allocation.get("cash_buffer") != "90%-100%":
                raise AssertionError("数据过期时未强制降到观察仓位")
            if not any("run_investment_workflow.py" in item for item in plan["next_actions"]):
                raise AssertionError("数据过期时未给出刷新工作流动作")
    scanner_text = _read_text(ROOT / "scan_stocks_full.py")
    required_scan_fields = ["expected_total", "valid_count", "no_data", "coverage_ratio"]
    missing_scan_fields = [word for word in required_scan_fields if word not in scanner_text]
    if missing_scan_fields:
        raise AssertionError("个股扫描 JSON 缺少覆盖率字段: " + ", ".join(missing_scan_fields))
    return f"3 档风险画像路线可生成，行动闸门: {', '.join(sorted(gates))}"


def check_investment_brief_contract() -> str:
    from nasdx.investment_brief import build_investment_brief, format_investment_brief

    required_fields = {
        "primary_bias",
        "exposure_action",
        "allocation",
        "priority_routes",
        "candidate_playbook",
        "candidate_audits",
        "execution_queue",
        "external_review_pack",
        "future_scenarios",
        "risk_controls",
        "next_actions",
        "data_evidence",
        "disclaimer",
    }
    brief = build_investment_brief(risk_profile="balanced")
    missing = required_fields - set(brief)
    if missing:
        raise AssertionError("最终简报缺少字段: " + ", ".join(sorted(missing)))
    if len(brief.get("priority_routes", [])) < 3:
        raise AssertionError("最终简报缺少 ETF/个股/现金路线")
    if len(brief.get("candidate_playbook", [])) < 3:
        raise AssertionError("最终简报候选执行剧本不足")
    audits = brief.get("candidate_audits", [])
    if len(audits) < len(brief.get("candidate_playbook", [])):
        raise AssertionError("最终简报候选证据核查不足")
    missing_checklist = [item.get("candidate", "") for item in audits if not item.get("checklist")]
    if missing_checklist:
        raise AssertionError("候选证据核查缺少 checklist: " + ", ".join(missing_checklist[:5]))
    if not any("公告/财报/重大事项" in item.get("manual_checks", []) for item in audits):
        raise AssertionError("候选证据核查未标记公告/财报人工复核")
    execution_queue = brief.get("execution_queue", [])
    if not execution_queue:
        raise AssertionError("最终简报缺少执行队列")
    stages = {item.get("stage") for item in execution_queue}
    if not {"盘前", "盘中", "盘后"}.issubset(stages):
        raise AssertionError("执行队列缺少盘前/盘中/盘后阶段")
    if not any("action_gate" in str(item.get("action", "")) or "数据闸门" in str(item.get("target", "")) for item in execution_queue):
        raise AssertionError("执行队列未包含数据闸门动作")
    external_review = brief.get("external_review_pack", [])
    if len(external_review) < len(audits):
        raise AssertionError("外部复核包不足")
    if not all(item.get("review_status") == "pending_manual_review" for item in external_review):
        raise AssertionError("外部复核包未保持待人工复核状态")
    missing_links = [
        item.get("candidate", "")
        for item in external_review
        if len(item.get("source_links") or []) < 3
    ]
    if missing_links:
        raise AssertionError("外部复核包缺少来源入口: " + ", ".join(missing_links[:5]))
    if not any("巨潮资讯" in str(link.get("label", "")) for item in external_review for link in item.get("source_links", [])):
        raise AssertionError("外部复核包缺少巨潮资讯入口")
    if brief.get("action_gate") == "normal":
        trial_audits = [item for item in audits if item.get("status_code") == "trial_candidate"]
        if not any(item.get("type") == "ETF" for item in trial_audits):
            raise AssertionError("正常路线下缺少已核查 ETF 试错候选")
        if not any(item.get("type") == "个股" for item in trial_audits):
            raise AssertionError("正常路线下缺少已核查个股试错候选")
        missing_deep = [item for item in audits if item.get("status_code") == "needs_report"]
        if missing_deep:
            if not all(item.get("blocking_flags") for item in missing_deep):
                raise AssertionError("缺深度报告候选未写入阻断项")
            missing_codes = {item.get("code") for item in missing_deep}
            queue_commands = "\n".join(str(item.get("command", "")) for item in execution_queue)
            missing_command = [code for code in missing_codes if code and f"run_analysis.py {code}" not in queue_commands]
            if missing_command:
                raise AssertionError("执行队列缺少补深度报告命令: " + ", ".join(sorted(missing_command)))
            joined_actions = "\n".join(str(item) for item in brief.get("next_actions", []))
            if "跑深度分析" not in joined_actions:
                raise AssertionError("缺深度报告候选未给出补报告动作")
        trial_queue = [item for item in execution_queue if item.get("decision") == "小仓试错前复核"]
        if not trial_queue:
            raise AssertionError("执行队列缺少试错前复核动作")
        if any("人工复核" not in str(item.get("condition", "")) for item in trial_queue):
            raise AssertionError("执行队列试错动作未绑定人工复核条件")
        joined_actions = "\n".join(str(item) for item in brief.get("next_actions", []))
        if "ETF试错优先看" not in joined_actions or "个股卫星只从" not in joined_actions:
            raise AssertionError("最终简报缺少具体ETF/个股试错动作")
    if len(brief.get("risk_controls", [])) < 4:
        raise AssertionError("最终简报风险控制不足")
    if "不构成" not in str(brief.get("disclaimer", "")):
        raise AssertionError("最终简报缺少投资建议边界")
    markdown = format_investment_brief(brief)
    required_text = ["NASDX 最终投资简报", "优先路线", "候选执行剧本", "候选证据核查", "执行队列", "外部复核包", "未来情景", "数据证据"]
    missing_text = [word for word in required_text if word not in markdown]
    if missing_text:
        raise AssertionError("最终简报 Markdown 缺少: " + ", ".join(missing_text))
    return f"{len(brief.get('candidate_playbook', []))} 个候选剧本，{len(audits)} 个候选审计，{len(execution_queue)} 个执行动作，{len(external_review)} 个外部复核包"


def check_position_sizing_contract() -> str:
    from nasdx.investment_brief import build_investment_brief
    from nasdx.position_sizing import build_position_sizing, format_position_sizing, parse_percent_band

    if parse_percent_band("35%-60%") != (0.35, 0.60):
        raise AssertionError("百分比区间解析失败")
    brief = build_investment_brief(risk_profile="balanced")
    sizing = build_position_sizing(
        brief,
        total_capital=100000,
        current_etf_exposure=10000,
        current_stock_exposure=5000,
        current_other_exposure=0,
    )
    required = {
        "schema",
        "capital_inputs",
        "exposure",
        "candidate_sizing",
        "warnings",
        "assumptions",
        "disclaimer",
    }
    missing = required - set(sizing)
    if missing:
        raise AssertionError("仓位换算缺少字段: " + ", ".join(sorted(missing)))
    if sizing.get("schema") != "nasdx_position_sizing.v1":
        raise AssertionError("仓位换算 schema 不正确")
    exposure = sizing.get("exposure", {})
    if exposure.get("max_total_amount", 0) <= 0:
        raise AssertionError("仓位换算未生成总仓位金额上限")
    candidates = sizing.get("candidate_sizing", [])
    if len(candidates) < len(brief.get("candidate_audits", [])):
        raise AssertionError("仓位换算候选数量不足")
    if brief.get("action_gate") == "normal":
        trial_rows = [item for item in candidates if item.get("status_code") == "trial_candidate"]
        if not trial_rows:
            raise AssertionError("正常路线下仓位换算缺少试错候选")
        if not any(item.get("max_new_amount", 0) > 0 for item in trial_rows):
            raise AssertionError("试错候选未分配任何新增上限")
    if "不读取也不保存" not in " ".join(sizing.get("assumptions", [])):
        raise AssertionError("仓位换算缺少账户隐私边界")
    markdown = format_position_sizing(sizing)
    for word in ("NASDX 仓位换算", "候选金额", "风险提示"):
        if word not in markdown:
            raise AssertionError(f"仓位换算 Markdown 缺少: {word}")
    return f"{len(candidates)} 个候选已换算，剩余可新增 {exposure.get('remaining_total_capacity', 0):.0f}"


def check_recommendation_tracker_contract() -> str:
    from nasdx.recommendation_tracker import build_recommendation_tracker, format_recommendation_tracker

    tracker = build_recommendation_tracker()
    required = {
        "schema",
        "comparison_status",
        "action_gate_change",
        "posture_change",
        "allocation_changes",
        "added_candidates",
        "removed_candidates",
        "changed_candidates",
        "stable_trial_candidates",
        "counts",
        "review_focus",
        "disclaimer",
    }
    missing = required - set(tracker)
    if missing:
        raise AssertionError("建议漂移追踪缺少字段: " + ", ".join(sorted(missing)))
    if tracker.get("schema") != "nasdx_recommendation_tracker.v1":
        raise AssertionError("建议漂移追踪 schema 不正确")
    if tracker.get("comparison_status") not in {"compared", "no_prior"}:
        raise AssertionError("建议漂移追踪对比状态异常")
    counts = tracker.get("counts", {})
    if counts.get("current_candidates", 0) < 3:
        raise AssertionError("建议漂移追踪当前候选数量不足")
    if not tracker.get("review_focus"):
        raise AssertionError("建议漂移追踪缺少下次复盘重点")
    markdown = format_recommendation_tracker(tracker)
    for word in ("NASDX 建议漂移追踪", "候选变化", "下次复盘重点"):
        if word not in markdown:
            raise AssertionError(f"建议漂移追踪 Markdown 缺少: {word}")
    return f"{counts.get('added', 0)} 新增，{counts.get('removed', 0)} 移除，{counts.get('changed', 0)} 变化"


def check_recommendation_review_contract() -> str:
    from nasdx.recommendation_review import build_recommendation_review, format_recommendation_review

    review = build_recommendation_review()
    required = {
        "schema",
        "baseline_generated_at",
        "current_generated_at",
        "market_data_date",
        "time_context",
        "review_rows",
        "counts",
        "summary",
        "next_review_actions",
        "disclaimer",
    }
    missing = required - set(review)
    if missing:
        raise AssertionError("建议结果复盘缺少字段: " + ", ".join(sorted(missing)))
    if review.get("schema") != "nasdx_recommendation_review.v1":
        raise AssertionError("建议结果复盘 schema 不正确")
    rows = review.get("review_rows", [])
    if len(rows) < 3:
        raise AssertionError("建议结果复盘候选数量不足")
    bad_rows = [
        row.get("candidate", "")
        for row in rows
        if not row.get("review_status") or not row.get("review_action")
    ]
    if bad_rows:
        raise AssertionError("建议结果复盘缺少结论或动作: " + ", ".join(bad_rows[:5]))
    counts = review.get("counts", {})
    counted = sum(counts.get(key, 0) for key in ("signal_continues", "downgrade_review", "pending_evidence", "missing_current_data"))
    if counted != len(rows):
        raise AssertionError("建议结果复盘计数与候选数量不一致")
    if not review.get("next_review_actions"):
        raise AssertionError("建议结果复盘缺少下一步动作")
    if "不等同于真实交易收益" not in str(review.get("disclaimer", "")):
        raise AssertionError("建议结果复盘缺少真实收益边界")
    markdown = format_recommendation_review(review)
    for word in ("NASDX 建议结果复盘", "候选复盘", "下一步"):
        if word not in markdown:
            raise AssertionError(f"建议结果复盘 Markdown 缺少: {word}")
    return f"{len(rows)} 个候选已复盘，{counts.get('signal_continues', 0)} 个信号延续"


def check_account_review_contract() -> str:
    from nasdx.account_review import build_account_review, format_account_review, template_csv

    missing = build_account_review(None)
    if missing.get("review_status") != "missing_ledger":
        raise AssertionError("缺账户流水时未返回 missing_ledger")
    if "date,code" not in template_csv():
        raise AssertionError("账户复盘缺少 CSV 模板")

    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "ledger.csv"
        ledger.write_text(
            "\n".join(
                [
                    "date,code,name,side,quantity,price,fee,tax",
                    "2026-06-10,512890,红利低波ETF华泰柏瑞,buy,1000,1.000,1,0",
                    "2026-06-11,512890,红利低波ETF华泰柏瑞,sell,300,1.050,1,0",
                    "2026-06-11,600498,烽火通信,buy,100,24.50,1,0",
                ]
            ),
            encoding="utf-8",
        )
        review = build_account_review(ledger, total_capital=100000)
    required = {
        "schema",
        "review_status",
        "summary",
        "holdings",
        "closed_positions",
        "next_actions",
        "assumptions",
        "disclaimer",
    }
    missing_fields = required - set(review)
    if missing_fields:
        raise AssertionError("真实账户复盘缺少字段: " + ", ".join(sorted(missing_fields)))
    if review.get("schema") != "nasdx_account_review.v1":
        raise AssertionError("真实账户复盘 schema 不正确")
    if review.get("review_status") != "reviewed":
        raise AssertionError("真实账户复盘未进入 reviewed 状态")
    if len(review.get("holdings", [])) < 2:
        raise AssertionError("真实账户复盘未生成持仓")
    if "markdown" in json.dumps(review.get("holdings", []), ensure_ascii=False):
        raise AssertionError("真实账户复盘持仓字段异常")
    if "真实收益只来自用户导入的成交流水" not in " ".join(review.get("assumptions", [])):
        raise AssertionError("真实账户复盘缺少收益来源边界")
    markdown = format_account_review(review)
    for word in ("NASDX 真实账户复盘", "当前持仓", "已清仓/已卖出", "下一步"):
        if word not in markdown:
            raise AssertionError(f"真实账户复盘 Markdown 缺少: {word}")
    summary = review.get("summary", {})
    return f"{review.get('trade_count', 0)} 笔交易，{len(review.get('holdings', []))} 个持仓，仓位 {summary.get('exposure_pct')}"


def check_workflow_dry_run() -> str:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            sys.executable,
            "run_investment_workflow.py",
            "603501",
            "--workflow",
            "full",
            "--risk-profile",
            "balanced",
            "--rounds",
            "1",
            "--dry-run",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip() or proc.stdout.strip() or f"退出码 {proc.returncode}")
    output = proc.stdout
    required = ["刷新行情", "ETF50", "60只个股", "多 Agent 深度分析", "DRY-RUN"]
    missing = [word for word in required if word not in output]
    if missing:
        raise AssertionError("Dry-run 输出缺少步骤: " + ", ".join(missing))
    return "full 工作流步骤链完整"


def check_review_snapshot_contract() -> str:
    from nasdx.review_snapshot import build_review_snapshot

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = build_review_snapshot(risk_profile="balanced", output_dir=tmp, refresh=False)
        zip_path = Path(snapshot.get("zip_path", ""))
        if not zip_path.exists():
            raise AssertionError("复盘快照 ZIP 未生成")
        manifest = snapshot.get("manifest", {})
        if manifest.get("schema") != "nasdx_review_snapshot.v2":
            raise AssertionError("复盘快照 manifest schema 不正确")
        if manifest.get("validation_status") != "valid":
            raise AssertionError("复盘快照未通过源文件校验")
        if manifest.get("candidate_count", 0) < 3:
            raise AssertionError("复盘快照候选数量不足")
        if "链接存在不代表复核已通过" not in str(manifest.get("boundary", "")):
            raise AssertionError("复盘快照缺少外部复核边界")
        with zipfile.ZipFile(zip_path, "r") as archive:
            names = set(archive.namelist())
        required = {
            "manifest.json",
            "investment_brief_latest.md",
            "investment_brief_latest.json",
            "portfolio_plan_latest.json",
            "recommendation_tracker.md",
            "recommendation_tracker.json",
            "recommendation_review.md",
            "recommendation_review.json",
            "candidate_audits.csv",
            "execution_queue.csv",
            "external_review_pack.csv",
        }
        missing = sorted(required - names)
        if missing:
            raise AssertionError("复盘快照缺少文件: " + ", ".join(missing))
        return f"{len(names)} 个文件已打包"


def check_streamlit_state_boundaries() -> str:
    source = _read_text(ROOT / "app.py")
    task_source = _read_text(ROOT / "nasdx" / "ui_tasks.py")
    forbidden = [
        'os.environ["NASDX_API_KEY"]',
        'os.environ["NASDX_BASE_URL"]',
        'os.environ["NASDX_MODEL"]',
        "LLMClient._instance = None",
        '"thread":None',
        "st.session_state.thread",
        '"etf50_scan_thread"',
        'st.session_state["etf50_scan_thread"]',
        'ROOT / f"nasdx_log_{code}.txt"',
        "RUNNING_TASKS = {}",
    ]
    found = [item for item in forbidden if item in source]
    if found:
        raise AssertionError("Streamlit 状态边界存在风险标记: " + ", ".join(found))
    required = [
        "from nasdx.ui_tasks import",
        "_build_llm_env",
        'subprocess.run(cmd, stdout=f, stderr=f, env=env)',
        "nasdx_log_{code}_{task_id}.txt",
        '"task_id":None',
        '"etf50_scan_task_id"',
    ]
    missing = [item for item in required if item not in source]
    if missing:
        raise AssertionError("Streamlit 状态边界缺少实现: " + ", ".join(missing))
    task_required = ["_TASKS", "def register_task", "def task_alive", "def set_task_result"]
    task_missing = [item for item in task_required if item not in task_source]
    if task_missing:
        raise AssertionError("持久任务注册表缺少实现: " + ", ".join(task_missing))
    return "API配置进子进程env，后台任务注册表跨rerun持久化，session不保存线程对象"


def check_history_store_contract() -> str:
    from nasdx.history_store import (
        artifact_counts,
        init_history_db,
        latest_artifact,
        record_artifact,
        record_daily_scan,
        record_etf_pool,
        record_report_history,
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "nasdx_history.db"
        init_history_db(db_path)
        record_artifact(
            "investment_brief",
            "latest",
            {"generated_at": "2026-06-18T12:00:00", "action_gate": "normal"},
            generated_at="2026-06-18T12:00:00",
            source_path="reports/investment_brief_20260618_1200.json",
            db_path=db_path,
        )
        record_report_history(
            "603501",
            "20260618",
            {"final_signal": "bullish"},
            source_path="reports/report_603501_20260618.json",
            db_path=db_path,
        )
        record_daily_scan(
            "stocks60",
            "20260618",
            {"expected_total": 60, "valid_count": 58},
            source_path="reports/stocks60_20260618_1500.json",
            db_path=db_path,
        )
        record_etf_pool(
            "etf50",
            {"etfs": [{"code": "510300", "name": "沪深300ETF"}]},
            source_path="etf50_pool.json",
            db_path=db_path,
        )
        latest = latest_artifact("investment_brief", "latest", db_path=db_path)
        counts = artifact_counts(db_path)

    if not latest or latest["payload"].get("action_gate") != "normal":
        raise AssertionError("SQLite历史库未能读取最新最终简报")
    required_counts = {"investment_brief", "report_history", "daily_scan", "etf_pool"}
    missing = [name for name in required_counts if counts.get(name, 0) < 1]
    if missing:
        raise AssertionError("SQLite历史库缺少记录类型: " + ", ".join(sorted(missing)))
    return "nasdx_history.db 支持简报、单股报告、扫描和ETF池历史"


def check_streamlit_markers() -> str:
    text = _read_text(ROOT / "app.py")
    required = [
        "投资路线",
        "from nasdx.ui.plan_tables import (",
        "load_portfolio_latest",
        "load_investment_brief_latest",
        "最终简报",
        "候选执行剧本",
        "build_and_save_investment_brief",
        "build_review_snapshot",
        "candidate_playbook",
        "candidate_audits",
        "候选证据核查",
        "execution_queue",
        "执行队列",
        "build_position_sizing",
        "candidate_sizing",
        "资金仓位换算",
        "load_recommendation_tracker_latest",
        "建议漂移追踪",
        "changed_candidates",
        "load_recommendation_review_latest",
        "建议结果复盘",
        "review_rows",
        "load_account_review_latest",
        "build_account_review_from_text",
        "真实账户复盘",
        "account_review_csv",
        "external_review_pack",
        "外部复核包",
        "download_button",
        "复盘包",
        "risk_controls",
        "data_evidence",
        "future_scenarios",
        "decision_rules",
        "monitoring_checklist",
        "data_quality",
    ]
    missing = [word for word in required if word not in text]
    if missing:
        raise AssertionError("app.py 缺少页面标记: " + ", ".join(missing))

    table_text = _read_text(ROOT / "nasdx" / "ui" / "plan_tables.py")
    helpers = [
        "candidate_table",
        "scenario_table",
        "brief_playbook_table",
        "audit_table",
        "execution_queue_table",
        "external_review_table",
        "position_sizing_table",
        "account_review_table",
        "tracker_change_table",
        "recommendation_review_table",
    ]
    table_required = [
        *(f"def {name}(" for name in helpers),
        "escape_html",
        "safe_external_link",
        "deep_signal",
        'class="n-card plan-table"',
    ]
    missing = [word for word in table_required if word not in table_text]
    if missing:
        raise AssertionError("计划页表格 helper 缺少标记: " + ", ".join(missing))

    inline_helpers = [name for name in helpers if f"        def _{name.replace('candidate_table', 'table')}(" in text]
    if inline_helpers:
        raise AssertionError("计划页表格 helper 回流 app.py: " + ", ".join(inline_helpers))
    return "投资路线页包含生成、执行队列、外部复核包、情景、规则、监控和数据状态，使用 10 个独立表格 helper"


def check_documentation() -> str:
    readme = _read_text(ROOT / "README.md")
    doc_path = ROOT / "docs" / "INVESTMENT_DECISION_FRAMEWORK.md"
    if not doc_path.exists():
        raise AssertionError("缺少 docs/INVESTMENT_DECISION_FRAMEWORK.md")
    framework = _read_text(doc_path)
    required_readme = [
        "组合级投资路线",
        "未来情景推演",
        "run_investment_workflow.py",
        "run_portfolio_plan.py",
        "run_investment_brief.py",
        "run_position_sizing.py",
        "run_recommendation_tracker.py",
        "run_recommendation_review.py",
        "run_account_review.py",
        "run_review_snapshot.py",
        "nasdx_history.db",
        "task_id",
        "最终投资简报",
        "资金仓位换算",
        "建议漂移追踪",
        "建议结果复盘",
        "真实账户复盘",
        "账户流水",
        "执行队列",
        "外部复核包",
        "复盘快照包",
        "规则深度报告",
        "风险画像",
    ]
    missing_readme = [word for word in required_readme if word not in readme]
    if missing_readme:
        raise AssertionError("README 缺少: " + ", ".join(missing_readme))
    required_framework = ["nasdx.decision", "nasdx.portfolio", "nasdx.position_sizing", "nasdx.recommendation_tracker", "nasdx.recommendation_review", "nasdx.account_review", "nasdx.execution_queue", "nasdx.external_review", "nasdx.review_snapshot", "nasdx.history_store", "nasdx.rule_based_analysis", "nasdx.data_quality", "nasdx_history.db", "task_id", "账户流水", "覆盖率", "不保证收益", "链接存在不代表复核已通过"]
    missing_framework = [word for word in required_framework if word not in framework]
    if missing_framework:
        raise AssertionError("决策文档缺少: " + ", ".join(missing_framework))
    return "运行方式和投资决策边界已记录"


def _project_files(suffixes: Iterable[str]) -> list[Path]:
    suffix_set = tuple(suffixes)
    return [path for path in ROOT.rglob("*") if path.is_file() and path.suffix.lower() in suffix_set]


def _is_ignored(path: Path) -> bool:
    ignored_parts = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "reports",
        "models",
        "dist",
        "build",
        "wheelhouse",
    }
    parts = set(path.relative_to(ROOT).parts)
    return bool(parts & ignored_parts)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig")


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


if __name__ == "__main__":
    raise SystemExit(main())

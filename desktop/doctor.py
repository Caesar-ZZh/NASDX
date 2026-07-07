from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

from desktop.config import CONFIG_FILE_ENV, load_desktop_config
from desktop.inno import find_iscc
from desktop.paths import HISTORY_DB_ENV, REPORTS_DIR_ENV, RUNTIME_DIR_ENV, build_desktop_env, resolve_app_root
from desktop.runtime import DEFAULT_HOST, create_launch_plan


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


CORE_MODULES = ("streamlit", "pandas", "numpy", "requests")
FEATURE_MODULES = ("akshare", "mootdx", "openai", "pydantic")


@dataclass(frozen=True)
class DoctorCheck:
    label: str
    status: str
    detail: str


def run_doctor(
    *,
    root: Path | None = None,
    env: Mapping[str, str] | None = None,
    page: str | None = "plan",
    check_write: bool = False,
) -> list[DoctorCheck]:
    source = dict(env) if env is not None else dict(os.environ)
    checks: list[DoctorCheck] = []

    try:
        app_root = resolve_app_root(root, source)
    except Exception as exc:  # noqa: BLE001 - diagnostic should report context.
        return [DoctorCheck("app_root", FAIL, str(exc))]

    checks.append(_check_required_files(app_root))
    checks.append(_check_python_version())
    checks.extend(_check_modules(CORE_MODULES, fail_missing=True))
    checks.extend(_check_modules(FEATURE_MODULES, fail_missing=False))
    checks.append(_check_config(app_root, source))
    checks.append(_check_desktop_env(app_root, source, check_write=check_write))
    checks.append(_check_launch_plan(app_root, page))
    checks.append(_check_optional_webview())
    checks.append(_check_inno_setup())
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose the NASDX Windows desktop environment.")
    parser.add_argument("--root", default=None, help="NASDX app root. Defaults to auto-detection.")
    parser.add_argument("--page", default="plan", help="Page key used for the launch-plan check.")
    parser.add_argument("--check-write", action="store_true", help="Create and remove temporary runtime write probes.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else None
    checks = run_doctor(root=root, page=args.page, check_write=args.check_write)

    if args.json:
        print(json.dumps([asdict(item) for item in checks], ensure_ascii=False, indent=2))
    else:
        for item in checks:
            print(f"[{item.status}] {item.label}: {item.detail}")
        passed = sum(1 for item in checks if item.status == PASS)
        warned = sum(1 for item in checks if item.status == WARN)
        failed = sum(1 for item in checks if item.status == FAIL)
        print(f"summary: passed={passed} warned={warned} failed={failed}")

    return 1 if any(item.status == FAIL for item in checks) else 0


def _check_required_files(app_root: Path) -> DoctorCheck:
    required = [
        "app.py",
        "requirements_nasdx.txt",
        "启动网页.bat",
        "启动NASDX桌面.bat",
        "desktop\\launcher.py",
        "desktop\\control_panel.py",
        "config.example.toml",
    ]
    missing = [item for item in required if not (app_root / item).exists()]
    if missing:
        return DoctorCheck("required_files", FAIL, "missing: " + ", ".join(missing))
    return DoctorCheck("required_files", PASS, f"{len(required)} desktop entry files found")


def _check_python_version() -> DoctorCheck:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 11):
        return DoctorCheck("python_version", PASS, f"Python {version}")
    return DoctorCheck("python_version", WARN, f"Python {version}; Python 3.11 is recommended")


def _check_modules(names: Iterable[str], *, fail_missing: bool) -> list[DoctorCheck]:
    results: list[DoctorCheck] = []
    for name in names:
        found = importlib.util.find_spec(name) is not None
        if found:
            results.append(DoctorCheck(f"module:{name}", PASS, "available"))
        else:
            status = FAIL if fail_missing else WARN
            results.append(DoctorCheck(f"module:{name}", status, "not installed"))
    return results


def _check_config(app_root: Path, env: Mapping[str, str]) -> DoctorCheck:
    try:
        config = load_desktop_config(app_root, env)
    except Exception as exc:  # noqa: BLE001 - diagnostic should report context.
        return DoctorCheck("config", FAIL, str(exc))

    source = "explicit" if CONFIG_FILE_ENV in env else "auto"
    exists = "exists" if config.exists else "missing"
    keys = ", ".join(config.loaded_keys) if config.loaded_keys else "none"
    return DoctorCheck("config", PASS, f"{source} path {exists}: {config.path}; loaded keys: {keys}")


def _check_desktop_env(app_root: Path, env: Mapping[str, str], *, check_write: bool) -> DoctorCheck:
    try:
        desktop_env = build_desktop_env(app_root, env)
    except Exception as exc:  # noqa: BLE001 - diagnostic should report context.
        return DoctorCheck("desktop_env", FAIL, str(exc))

    runtime_dir = Path(desktop_env[RUNTIME_DIR_ENV])
    reports_dir = Path(desktop_env[REPORTS_DIR_ENV])
    history_db = Path(desktop_env[HISTORY_DB_ENV])
    if check_write:
        try:
            _write_probe(runtime_dir)
            _write_probe(reports_dir)
            _write_probe(history_db.parent)
        except Exception as exc:  # noqa: BLE001 - diagnostic should report context.
            return DoctorCheck("desktop_env", FAIL, f"runtime path is not writable: {exc}")
        detail = "runtime paths writable"
    else:
        detail = "runtime paths resolved"

    return DoctorCheck(
        "desktop_env",
        PASS,
        f"{detail}; runtime={runtime_dir}; reports={reports_dir}; history_db={history_db}",
    )


def _check_launch_plan(app_root: Path, page: str | None) -> DoctorCheck:
    try:
        plan = create_launch_plan(root=app_root, host=DEFAULT_HOST, port=None, page=page)
    except Exception as exc:  # noqa: BLE001 - diagnostic should report context.
        return DoctorCheck("launch_plan", FAIL, str(exc))
    return DoctorCheck("launch_plan", PASS, f"{plan.url}; command uses app.py")


def _check_optional_webview() -> DoctorCheck:
    if importlib.util.find_spec("webview") is None:
        return DoctorCheck("optional_webview", WARN, "pywebview not installed; browser fallback will be used")
    return DoctorCheck("optional_webview", PASS, "pywebview available")


def _check_inno_setup() -> DoctorCheck:
    candidate = find_iscc()
    if candidate:
        return DoctorCheck("inno_setup", PASS, f"ISCC found: {candidate}")
    return DoctorCheck("inno_setup", WARN, "ISCC.exe not found; installer compile is unavailable on this machine")


def _write_probe(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".nasdx_doctor_write_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


if __name__ == "__main__":
    raise SystemExit(main())

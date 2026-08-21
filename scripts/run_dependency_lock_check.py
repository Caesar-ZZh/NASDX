"""Validate and optionally resolve NASDX Windows hash lockfiles."""
from __future__ import annotations

# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "packaging" / "windows"
LOCKS = (
    WINDOWS / "requirements-win-core.lock",
    WINDOWS / "requirements-win-webview.lock",
)
PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^\s\\]+(?:\s*\\)?$")


def validate_lock(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    packages = [line for line in text.splitlines() if PACKAGE_RE.fullmatch(line)]
    if not packages:
        raise ValueError(f"lockfile has no pinned packages: {path}")
    if text.count("--hash=sha256:") < len(packages):
        raise ValueError(f"lockfile has unhashed packages: {path}")
    return len(packages)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 Windows 完整依赖锁")
    parser.add_argument("--static-only", action="store_true", help="只校验固定版本和哈希格式")
    parser.add_argument("--enforce-toolchain", action="store_true", help="要求当前 Python/pip 与发布工具链完全一致")
    args = parser.parse_args()

    toolchain = json.loads((WINDOWS / "toolchain-win.json").read_text(encoding="utf-8"))
    current_python = ".".join(str(part) for part in sys.version_info[:3])
    current_pip = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.split()[1]
    uv_executable = shutil.which("uv")
    current_uv = None
    if uv_executable:
        current_uv = subprocess.run(
            [uv_executable, "--version"], check=True, capture_output=True, text=True, encoding="utf-8"
        ).stdout.split()[1]
    if args.enforce_toolchain and (
        current_python != toolchain["python"]
        or current_pip != toolchain["pip"]
        or current_uv != toolchain["uv"]
    ):
        raise RuntimeError(
            f"toolchain mismatch: python={current_python}, pip={current_pip}, uv={current_uv}; "
            f"expected python={toolchain['python']}, pip={toolchain['pip']}, uv={toolchain['uv']}"
        )

    total = sum(validate_lock(path) for path in LOCKS)
    if not args.static_only:
        if uv_executable is None:
            raise RuntimeError("uv is required for deterministic lock resolution")
        for path in LOCKS:
            subprocess.run(
                [
                    uv_executable,
                    "pip",
                    "install",
                    "--dry-run",
                    "--system",
                    "--require-hashes",
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "windows",
                    "-r",
                    str(path),
                ],
                cwd=ROOT,
                check=True,
            )
    print(f"Windows dependency locks valid: {len(LOCKS)}/2 files, {total} pinned entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

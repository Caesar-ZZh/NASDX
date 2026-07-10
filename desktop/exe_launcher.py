from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = resolve_runtime_root()
    python_exe = resolve_python(root)

    control_panel = root / "desktop" / "control_panel.py"
    fallback_launcher = root / "desktop" / "launcher.py"
    primary = [python_exe, "-B", str(control_panel), *args]
    result = subprocess.run(primary, cwd=str(root), check=False)
    if result.returncode == 0:
        return 0

    fallback = [python_exe, "-B", str(fallback_launcher), "--webview", "--page", "plan", *args]
    return subprocess.run(fallback, cwd=str(root), check=False).returncode


def resolve_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_python(root: Path) -> str:
    bundled_python = root / ".venv" / "Scripts" / "python.exe"
    if bundled_python.exists():
        return str(bundled_python)
    if getattr(sys, "frozen", False):
        return "python"
    return sys.executable


if __name__ == "__main__":
    raise SystemExit(main())

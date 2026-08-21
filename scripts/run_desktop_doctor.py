"""Run NASDX desktop environment diagnostics."""
from __future__ import annotations

# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
from desktop.doctor import main


if __name__ == "__main__":
    raise SystemExit(main())

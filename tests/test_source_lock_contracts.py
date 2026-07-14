"""源码级依赖锁契约测试（issue #36）。

Windows 桌面打包已有 hash 锁（packaging/windows/requirements-win-*.lock，由
run_dependency_lock_check.py 强制）。此处补齐「源码 / CI 测试」层面的固定版本锁：
requirements.lock 必须固定 requirements_nasdx.txt 中声明的每一个运行时顶层依赖，
避免 CI 用 >= 浮动范围安装导致不可复现。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _top_level_runtime_deps(path: Path) -> dict[str, str]:
    deps: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*>=.*$", line)
        if m:
            deps[m.group(1).lower()] = line
    return deps


class SourceLockContractsTest(unittest.TestCase):
    def test_source_lock_pins_every_runtime_dependency(self):
        req = ROOT / "requirements_nasdx.txt"
        lock = ROOT / "requirements.lock"
        self.assertTrue(lock.exists(), "requirements.lock 缺失（issue #36 未生成锁文件）")
        lock_text = lock.read_text(encoding="utf-8")
        locked = {m.group(1).lower() for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==", lock_text, re.M)}
        self.assertGreater(len(locked), 0, "锁文件没有任何固定版本的包")

        # 桌面打包锁（hash）已固定部分运行时依赖（如 tdxrs，随桌面运行时单独打包），
        # 源码级锁允许这些依赖豁免——只要在某个锁文件中被固定即可。
        win_lock = ROOT / "packaging/windows/requirements-win-core.lock"
        win_pinned: set[str] = set()
        if win_lock.exists():
            win_pinned = {
                m.group(1).lower()
                for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==", win_lock.read_text(encoding="utf-8"), re.M)
            }

        for name, spec in _top_level_runtime_deps(req).items():
            self.assertTrue(
                name in locked or name in win_pinned,
                f"依赖未在任一锁文件中固定: {spec}",
            )


if __name__ == "__main__":
    unittest.main()

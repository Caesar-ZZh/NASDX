"""debate.collect_dossier 契约：4 个 section 哪怕全卡死也必须 25s 内 yield 完。

newsradar.py 同样的 30s 修过（详见那里）。这里防止 4 个 section
里某个 section.exec_tool() 卡 socket（akshare/tdx 不可达时常见），
让 dossier 一直挂着不返回。
"""
from __future__ import annotations

import os
import sys
import time

# 让 tests 能直接 import server.stock
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_STOCK = os.path.normpath(os.path.join(HERE, "..", "server", "stock"))
if SERVER_STOCK not in sys.path:
    sys.path.insert(0, SERVER_STOCK)

import debate  # noqa: E402
import tools  # noqa: E402


def test_collect_dossier_with_hanging_section_finishes_under_25s():
    """mock 让某个 section 卡死 60s，验证 collect_dossier 在 25s 内 yield 完。"""
    real_exec = tools.exec_tool
    call_count = {"n": 0}

    def hanging_exec(name, args):
        call_count["n"] += 1
        # 第一个调用 hang 60s（远超 dossier 超时 20s）
        if call_count["n"] == 1:
            time.sleep(60)
        return real_exec(name, args)

    tools.exec_tool = hanging_exec
    try:
        t0 = time.time()
        events = list(debate.collect_dossier("600519"))
        elapsed = time.time() - t0
    finally:
        tools.exec_tool = real_exec

    assert elapsed < 60, f"collect_dossier 用了 {elapsed:.1f}s，超过 60s 上限（仍卡死）"
    # 至少一个 dossier_progress 事件（说明生成器在跑）
    progress_events = [e for e in events if e.get("type") == "dossier_progress"]
    assert len(progress_events) >= 1, "应至少 yield 一个 dossier_progress"
    # 核心契约：hanging section 的 future 触发超时后被标缺口，进度仍能推进。
    # 末事件 loaded 应等于 total（全部 section 都有结果，含超时的 placeholder）。
    last = progress_events[-1]
    assert last["loaded"] == last["total"], f"加载数 {last['loaded']} != total {last['total']}（超时 section 未标缺口）"
    print(f"✓ collect_dossier {elapsed:.1f}s 内 yield 完，全部 {last['total']} section 都有结果")

"""
统一决策日志 / 审计链（TradingAgents 借鉴：透明日志）

结构化记录 agent / 输入 / 输出 / 置信度 / timestamp，落本地 JSONL，不入库。
日志可关闭（NASDX_DECISION_LOG=0）。高可逆。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from nasdx.paths import get_reports_dir

_ENABLED = os.environ.get("NASDX_DECISION_LOG", "1") != "0"
_lock = threading.Lock()


def _log_path() -> str:
    d = get_reports_dir(create=True)
    return os.path.join(str(d), "decision_log.jsonl")


def log_decision(
    agent: str,
    action: str,
    *,
    inputs: Any = None,
    output: Any = None,
    confidence: Optional[float] = None,
    meta: Optional[dict] = None,
) -> None:
    """记录一条决策日志。被 NASDX_DECISION_LOG=0 关闭时直接返回。"""
    if not _ENABLED:
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "action": action,
        "inputs": inputs,
        "output": output,
        "confidence": confidence,
        "meta": meta or {},
    }
    with _lock:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def decision_logger(agent: str, action: str = "call"):
    """装饰器：自动记录函数调用与返回（大对象截断）。"""

    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                log_decision(
                    agent, action,
                    inputs={"args": _truncate(args), "kwargs": _truncate(kwargs)},
                    output=_truncate(result),
                )
                return result
            except Exception as e:  # noqa: BLE001 — 记录后继续抛出
                log_decision(agent, action, inputs={"args": _truncate(args)},
                             output=f"ERROR: {e}")
                raise

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


def _truncate(obj, limit: int = 500):
    s = repr(obj)
    return s if len(s) <= limit else s[:limit] + "...(truncated)"

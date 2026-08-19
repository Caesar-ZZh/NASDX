"""NASDX 共享 TTL 缓存工具（被 daily_review / analysis / intraday_copilot 等共用）。

接口与 miaoousc /market.py/_cached 保持一致：同进程共享内存字典，
默认 5 分钟 TTL，空结果不缓存（调用方可传 custom valid 判据）。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

_DEFAULT_TTL: int = 300

_slot: dict[str, tuple[float, Any]] = {}


def cached(
    key: str,
    fn: Callable[[], Any],
    ttl: Optional[int] = None,
    valid: Callable[[Any], bool] = bool,
) -> Any:
    """TTL 缓存。空结果不缓存（valid 判否），下次请求直接重试。"""
    t = ttl if ttl is not None else _DEFAULT_TTL
    now = time.time()
    hit = _slot.get(key)
    if hit and now - hit[0] < t:
        return hit[1]
    val = fn()
    if valid(val):
        _slot[key] = (now, val)
    return val


def clear(keys: Optional[list[str]] = None) -> None:
    if keys is None:
        _slot.clear()
        return
    for k in keys:
        _slot.pop(k, None)


def ttl() -> int:
    return _DEFAULT_TTL

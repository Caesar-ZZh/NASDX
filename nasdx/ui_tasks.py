from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


_TASKS: dict[str, dict[str, Any]] = {}
_TASK_LOCK = threading.Lock()


def new_task_id(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"{prefix}_{stamp}"


def register_task(
    task_id: str,
    thread: threading.Thread,
    log_path: Path | None = None,
) -> None:
    with _TASK_LOCK:
        _TASKS[task_id] = {
            "thread": thread,
            "log_path": str(log_path) if log_path else None,
            "started_at": time.time(),
        }


def task_alive(task_id: str | None) -> bool:
    if not task_id:
        return False
    with _TASK_LOCK:
        item = _TASKS.get(task_id)
    thread = item.get("thread") if item else None
    alive = bool(thread and thread.is_alive())
    if item and not alive and "result" not in item:
        with _TASK_LOCK:
            _TASKS.pop(task_id, None)
    return alive


def set_task_result(task_id: str, result: dict) -> None:
    with _TASK_LOCK:
        item = _TASKS.get(task_id)
        if item is not None:
            item["result"] = result


def take_task_result(task_id: str | None) -> dict | None:
    if not task_id:
        return None
    with _TASK_LOCK:
        item = _TASKS.pop(task_id, None)
    return item.get("result") if item else None

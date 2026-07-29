"""
统一决策日志 / 审计链（TradingAgents 借鉴：透明日志）

结构化记录 agent / 输入 / 输出 / 置信度 / timestamp，落本地 JSONL，不入库。

隐私与安全（#62）：
- **默认关闭（opt-in）**：仅当环境变量 ``NASDX_DECISION_LOG=1``（或 true/yes/on）
  时才写盘；未设置或设为 0/false 时不产生任何持久化记录。
- **写盘前递归脱敏**：inputs/output/meta 中键名命中敏感模式
  （api_key/token/secret/password/authorization/cookie/credential 等，
  可用 ``NASDX_DECISION_LOG_REDACT_KEYS`` 追加，逗号分隔）的值替换为
  ``[REDACTED]``；字符串值中的 Bearer token / ``sk-`` 形态密钥 /
  ``key=value`` 形态凭据同样在落盘前被抹除。
- **不依赖 repr()**：仅序列化 JSON 基元与 dict/list/tuple/set，
  未知对象只记录 ``<module.TypeName>`` 类型摘要，不落对象内容。
- **有界保留**：单文件超过 ``NASDX_DECISION_LOG_MAX_BYTES``（默认 5MB）
  时轮转到 ``decision_log.jsonl.1``（仅保留一份备份，总占用有上界）。

日志位置：``<reports 目录>/decision_log.jsonl``（reports/ 已在 .gitignore 中，
且 reports 目录位于用户运行时目录，不进版本库与打包产物）。
日志可能包含股票代码、信号、置信度等投资分析上下文——如不希望留存，
请勿设置 ``NASDX_DECISION_LOG=1``。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from nasdx.paths import get_reports_dir

_TRUTHY = {"1", "true", "yes", "on"}

# 键名命中即整值替换为 [REDACTED]（对键做小写 + 去除非字母数字后子串匹配）
_SENSITIVE_KEY_PATTERNS = (
    "apikey", "token", "secret", "password", "passwd", "pwd",
    "authorization", "cookie", "credential", "privatekey", "accesskey",
)

# 字符串值中的凭据形态：Bearer token、sk- 系密钥、key=value 形态
_SENSITIVE_VALUE_RES = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/=]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?key|token|secret|password|passwd|pwd|authorization|cookie)"
        r"\s*[=:]\s*\S+"
    ),
)

_REDACTED = "[REDACTED]"
_MAX_DEPTH = 8
_STR_LIMIT = 500


def _env_enabled() -> bool:
    """opt-in：默认关闭，仅显式设置真值时开启。"""
    return os.environ.get("NASDX_DECISION_LOG", "").strip().lower() in _TRUTHY


_ENABLED = _env_enabled()
_lock = threading.Lock()


def _log_path() -> str:
    d = get_reports_dir(create=True)
    return os.path.join(str(d), "decision_log.jsonl")


def _max_bytes() -> int:
    raw = os.environ.get("NASDX_DECISION_LOG_MAX_BYTES", "")
    try:
        n = int(raw)
        if n > 0:
            return n
    except ValueError:
        pass
    return 5 * 1024 * 1024  # 5MB


def _extra_redact_keys() -> tuple:
    raw = os.environ.get("NASDX_DECISION_LOG_REDACT_KEYS", "")
    return tuple(
        _normalize_key(k) for k in raw.split(",") if k.strip()
    )


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive_key(key: Any) -> bool:
    norm = _normalize_key(key)
    if not norm:
        return False
    patterns = _SENSITIVE_KEY_PATTERNS + _extra_redact_keys()
    return any(p in norm for p in patterns)


def _sanitize_str(s: str) -> str:
    for rx in _SENSITIVE_VALUE_RES:
        s = rx.sub(_REDACTED, s)
    if len(s) > _STR_LIMIT:
        s = s[:_STR_LIMIT] + "...(truncated)"
    return s


def _sanitize(obj: Any, depth: int = 0) -> Any:
    """递归脱敏 + 安全序列化：不使用 repr() 落未知对象内容。"""
    if depth > _MAX_DEPTH:
        return "...(max depth)"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _sanitize_str(obj)
    if isinstance(obj, dict):
        return {
            str(k): (_REDACTED if _is_sensitive_key(k) else _sanitize(v, depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_sanitize(v, depth + 1) for v in obj]
    # 未知对象：仅记录类型摘要，绝不 repr() 内容
    t = type(obj)
    return f"<{t.__module__}.{t.__name__}>"


def _rotate_if_needed(path: str) -> None:
    """单文件超限时轮转为 .1（仅保留一份备份，保留量有上界）。"""
    try:
        if os.path.exists(path) and os.path.getsize(path) >= _max_bytes():
            backup = path + ".1"
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(path, backup)
    except OSError:
        # 轮转失败不阻断主流程（日志是附加能力）
        pass


def log_decision(
    agent: str,
    action: str,
    *,
    inputs: Any = None,
    output: Any = None,
    confidence: Optional[float] = None,
    meta: Optional[dict] = None,
) -> None:
    """记录一条决策日志。默认关闭；仅 NASDX_DECISION_LOG=1 时写盘。

    写盘前对 inputs/output/meta 递归脱敏（见模块 docstring）。
    """
    if not _ENABLED:
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "action": action,
        "inputs": _sanitize(inputs),
        "output": _sanitize(output),
        "confidence": confidence,
        "meta": _sanitize(meta or {}),
    }
    with _lock:
        path = _log_path()
        _rotate_if_needed(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def decision_logger(agent: str, action: str = "call"):
    """装饰器：自动记录函数调用与返回（写盘前脱敏，未知对象只记类型）。"""

    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                log_decision(
                    agent, action,
                    inputs={"args": _sanitize(args), "kwargs": _sanitize(kwargs)},
                    output=_sanitize(result),
                )
                return result
            except Exception as e:  # noqa: BLE001 — 记录后继续抛出
                log_decision(agent, action, inputs={"args": _sanitize(args)},
                             output=_sanitize_str(f"ERROR: {e}"))
                raise

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


def _truncate(obj, limit: int = 500):
    """兼容保留：旧接口。现仅对已脱敏内容做长度截断。"""
    s = obj if isinstance(obj, str) else json.dumps(
        _sanitize(obj), ensure_ascii=False, default=str
    )
    return s if len(s) <= limit else s[:limit] + "...(truncated)"

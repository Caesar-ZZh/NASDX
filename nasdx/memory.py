"""
决策记忆层（TradingAgents 借鉴：决策记忆与反思）

记录每次工作流的结论、标的、日期、置信度、后续实际表现；
复盘时回灌 prompt 触发反思。记忆缺失时降级为无记忆运行（高可逆）。

三层输出的边界（#63 文档化）：
- **临时分析输出**（reports/*.html|*.json）：单次运行的报告成品，随手可删；
- **决策日志**（nasdx/decision_log.py → decision_log.jsonl）：函数级审计链，
  受 ``NASDX_DECISION_LOG`` 独立控制（#62，默认关闭）；
- **决策记忆**（本模块 → decision_memory.jsonl / SQLite）：跨运行可复用的
  历史决策沉淀，可能在未来经 ``format_memory_prompt()`` 回灌 LLM prompt。

隐私与持久化策略（#63）：
- **默认关闭（opt-in）**：唯一开关 ``NASDX_MEMORY_ENABLED=1``（或 true/yes/on）。
  未开启时 ``record_decision()`` 不产生任何持久化（包括显式 db_path 调用），
  ``recall_for_reflection()`` 返回空列表——功能整体关闭，不会把历史摘要
  回灌进 LLM prompt。仅设置 ``NASDX_MEMORY_DB`` 不会开启记忆。
- **摘要有界 + 脱敏**：summary 落盘前复用 decision_log 的凭据抹除
  （Bearer/sk-/key=value 形态 → [REDACTED]），并截断到
  ``NASDX_MEMORY_SUMMARY_MAX`` 字符（默认 200；设 0 表示不保留摘要正文）。
- **保留上限**：记录条数超过 ``NASDX_MEMORY_MAX_RECORDS``（默认 500）时
  自动淘汰最旧记录（JSONL 重写保留尾部 / SQLite 删除最小 id），
  文件不会无限增长。
- **可清除**：``clear_memory()`` 或命令行
  ``python -m nasdx.memory --clear`` 一键删除已存记忆，无需手工编辑文件。
- **可诊断**：``memory_status()`` 返回开关状态、存储后端、路径、
  当前记录数与保留配置。

存储：开启后默认 JSONL（decision_memory.jsonl，落本地报告目录）；
      若设置环境变量 NASDX_MEMORY_DB 则改用 SQLite。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from nasdx.paths import get_reports_dir
from nasdx.decision_log import sanitize_text

MEMORY_DB = os.environ.get("NASDX_MEMORY_DB", "")

_TRUTHY = {"1", "true", "yes", "on"}
_DEFAULT_SUMMARY_MAX = 200
_DEFAULT_MAX_RECORDS = 500

_lock = threading.Lock()


@dataclass
class DecisionRecord:
    stock_code: str
    date: str
    signal: str
    confidence: float
    summary: str
    source: str = "analysis"
    benchmark: Optional[float] = None   # 相对基准收益（如沪深300），用于反思
    outcome: Optional[float] = None      # 后续实际表现，复盘时回填
    created_at: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enabled() -> bool:
    """唯一开关（#63）：默认关闭，仅显式设置真值时开启。每次调用读 env。"""
    return os.environ.get("NASDX_MEMORY_ENABLED", "").strip().lower() in _TRUTHY


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name, "")
    try:
        n = int(raw)
        if n >= minimum:
            return n
    except ValueError:
        pass
    return default


def _summary_max() -> int:
    return _int_env("NASDX_MEMORY_SUMMARY_MAX", _DEFAULT_SUMMARY_MAX, minimum=0)


def _max_records() -> int:
    return _int_env("NASDX_MEMORY_MAX_RECORDS", _DEFAULT_MAX_RECORDS, minimum=1)


def _jsonl_path() -> str:
    d = get_reports_dir(create=True)
    return os.path.join(str(d), "decision_memory.jsonl")


def _target(db_path: Optional[str]) -> str:
    return db_path or MEMORY_DB or os.environ.get("NASDX_MEMORY_DB", "")


def record_decision(
    stock_code: str,
    date: str,
    signal: str,
    confidence: float,
    summary: str,
    *,
    source: str = "analysis",
    benchmark: Optional[float] = None,
    outcome: Optional[float] = None,
    db_path: Optional[str] = None,
) -> DecisionRecord:
    """记录一条决策。默认不持久化；仅 NASDX_MEMORY_ENABLED=1 时写盘（#63）。

    写盘前 summary 经脱敏 + 截断（NASDX_MEMORY_SUMMARY_MAX，默认 200）；
    写盘后按 NASDX_MEMORY_MAX_RECORDS（默认 500）淘汰最旧记录。
    返回的 DecisionRecord 始终可用（内存对象，含脱敏后的 summary），
    未开启时仅不落盘。
    """
    rec = DecisionRecord(
        stock_code=stock_code, date=date, signal=signal, confidence=float(confidence),
        summary=sanitize_text(summary or "", limit=_summary_max()),
        source=source, benchmark=benchmark, outcome=outcome,
        created_at=_now_iso(),
    )
    if not _enabled():
        return rec
    target = _target(db_path)
    with _lock:
        if target:
            _record_sqlite(target, rec)
            _prune_sqlite(target)
        else:
            path = _jsonl_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
            _prune_jsonl(path)
    return rec


def _record_sqlite(db_path: str, rec: DecisionRecord) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT, date TEXT, signal TEXT, confidence REAL,
                summary TEXT, source TEXT, benchmark REAL, outcome REAL,
                created_at TEXT)"""
        )
        conn.execute(
            "INSERT INTO decisions "
            "(stock_code,date,signal,confidence,summary,source,benchmark,outcome,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (rec.stock_code, rec.date, rec.signal, rec.confidence, rec.summary,
             rec.source, rec.benchmark, rec.outcome, rec.created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _prune_sqlite(db_path: str) -> None:
    """SQLite 保留策略：只保留最新 NASDX_MEMORY_MAX_RECORDS 条。"""
    keep = _max_records()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "DELETE FROM decisions WHERE id NOT IN "
            "(SELECT id FROM decisions ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        conn.commit()
    except sqlite3.Error:
        pass  # 保留策略失败不阻断主流程
    finally:
        conn.close()


def _prune_jsonl(path: str) -> None:
    """JSONL 保留策略：行数超限时重写文件，仅保留尾部最新记录。"""
    keep = _max_records()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        if len(lines) <= keep:
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-keep:])
        os.replace(tmp, path)
    except OSError:
        pass  # 保留策略失败不阻断主流程


def recall_for_reflection(
    stock_code: Optional[str] = None, limit: int = 10, db_path: Optional[str] = None
) -> list:
    """回灌 prompt 用：返回近期决策记录。

    记忆未开启（NASDX_MEMORY_ENABLED 非真值）时返回空列表——
    关闭即整体关闭，历史摘要不会被回灌进 LLM prompt（#63 数据边界）。
    缺失文件时同样返回空列表，降级运行。
    """
    if not _enabled():
        return []
    target = _target(db_path)
    with _lock:
        if target:
            return _recall_sqlite(target, stock_code, limit)
        path = _jsonl_path()
        if not os.path.exists(path):
            return []
        out: list = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if stock_code and rec.get("stock_code") != stock_code:
                    continue
                out.append(rec)
        return out[-limit:]


def _recall_sqlite(db_path: str, stock_code, limit) -> list:
    conn = sqlite3.connect(db_path)
    try:
        if stock_code:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE stock_code=? ORDER BY id DESC LIMIT ?",
                (stock_code, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM decisions").description]
        return [dict(zip(cols, r)) for r in rows]
    except sqlite3.OperationalError:
        return []  # 表不存在等：降级为空记忆
    finally:
        conn.close()


def clear_memory(db_path: Optional[str] = None) -> int:
    """清除已存决策记忆（#63 验收：无需手工编辑文件/数据库）。

    返回删除的记录数（JSONL 按行计，文件不存在返回 0）。
    清除不受开关限制——即使记忆已关闭，也允许删除历史遗留数据。
    """
    target = _target(db_path)
    with _lock:
        if target:
            if not os.path.exists(target):
                return 0
            conn = sqlite3.connect(target)
            try:
                cur = conn.execute("DELETE FROM decisions")
                conn.commit()
                return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            except sqlite3.OperationalError:
                return 0
            finally:
                conn.close()
        path = _jsonl_path()
        if not os.path.exists(path):
            return 0
        with open(path, "r", encoding="utf-8") as f:
            n = sum(1 for ln in f if ln.strip())
        os.remove(path)
        return n


def memory_status(db_path: Optional[str] = None) -> dict:
    """诊断信息（#63 验收）：开关/后端/路径/记录数/保留配置。"""
    target = _target(db_path)
    backend = "sqlite" if target else "jsonl"
    path = target if target else _jsonl_path()
    count = 0
    if os.path.exists(path):
        if target:
            conn = sqlite3.connect(path)
            try:
                count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            except sqlite3.OperationalError:
                count = 0
            finally:
                conn.close()
        else:
            with open(path, "r", encoding="utf-8") as f:
                count = sum(1 for ln in f if ln.strip())
    return {
        "enabled": _enabled(),
        "backend": backend,
        "path": path,
        "record_count": count,
        "max_records": _max_records(),
        "summary_max_chars": _summary_max(),
    }


def format_memory_prompt(records: list, benchmark_name: str = "沪深300") -> str:
    """把记忆格式化为可注入 prompt 的反思段落；无记忆返回空串。"""
    if not records:
        return ""
    lines = ["[历史决策记忆（用于反思，非投资建议）]"]
    for r in records:
        bench = f" 基准({benchmark_name})={r.get('benchmark')}" if r.get("benchmark") is not None else ""
        out = f" 后续表现={r.get('outcome')}" if r.get("outcome") is not None else ""
        lines.append(
            f"- {r.get('date')} {r.get('stock_code')} 信号={r.get('signal')} "
            f"置信度={r.get('confidence')} {bench} {out} :: {(r.get('summary') or '')[:80]}"
        )
    return "\n".join(lines)


def _main(argv: Optional[list] = None) -> int:
    """CLI：python -m nasdx.memory --status | --clear"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m nasdx.memory", description="NASDX 决策记忆管理（#63）"
    )
    parser.add_argument("--status", action="store_true", help="显示记忆状态")
    parser.add_argument("--clear", action="store_true", help="清除已存决策记忆")
    args = parser.parse_args(argv)
    if args.clear:
        n = clear_memory()
        print(f"已清除决策记忆记录 {n} 条")
        return 0
    status = memory_status()
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

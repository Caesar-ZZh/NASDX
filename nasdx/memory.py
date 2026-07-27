"""
决策记忆层（TradingAgents 借鉴：决策记忆与反思）

记录每次工作流的结论、标的、日期、置信度、后续实际表现；
复盘时回灌 prompt 触发反思。记忆缺失时降级为无记忆运行（高可逆）。

存储：默认 JSONL（decision_memory.jsonl，落本地报告目录）；
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

MEMORY_DB = os.environ.get("NASDX_MEMORY_DB", "")

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


def _jsonl_path() -> str:
    d = get_reports_dir(create=True)
    return os.path.join(str(d), "decision_memory.jsonl")


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
    rec = DecisionRecord(
        stock_code=stock_code, date=date, signal=signal, confidence=float(confidence),
        summary=summary, source=source, benchmark=benchmark, outcome=outcome,
        created_at=_now_iso(),
    )
    target = db_path or MEMORY_DB
    with _lock:
        if target:
            _record_sqlite(target, rec)
        else:
            with open(_jsonl_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
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


def recall_for_reflection(
    stock_code: Optional[str] = None, limit: int = 10, db_path: Optional[str] = None
) -> list:
    """回灌 prompt 用：返回近期决策记录（缺失文件时返回空列表，降级运行）。"""
    target = db_path or MEMORY_DB
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
    finally:
        conn.close()


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

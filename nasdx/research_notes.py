"""研究笔记模块 —— 本地 SQLite 存储，不入 git。

支持：
- 增/删/改/查笔记（类型：复盘/要点/问AI/辩论）
- 每条笔记可选关联反思审计结果
- 完全本地、不上传、不涉及荐股
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

_NOTE_TYPES = ("复盘", "要点", "问AI", "辩论")
_DB_FILE = Path(__file__).parent.parent / ".data" / "research_notes.db"


def _get_conn() -> sqlite3.Connection:
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id          TEXT PRIMARY KEY,
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL,
            title       TEXT NOT NULL DEFAULT '',
            content     TEXT NOT NULL DEFAULT '',
            kind        TEXT NOT NULL CHECK(kind IN ('复盘','要点','问AI','辩论')),
            tags        TEXT NOT NULL DEFAULT '[]',
            reflect_id  TEXT,
            FOREIGN KEY (reflect_id) REFERENCES reflections(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reflections (
            id          TEXT PRIMARY KEY,
            note_id     TEXT NOT NULL,
            created_at  REAL NOT NULL,
            source_len  INTEGER NOT NULL,
            truncated   INTEGER NOT NULL DEFAULT 0,
            full_text   TEXT NOT NULL,
            FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
        )
    """)
    conn.commit()


def add(
    title: str,
    content: str,
    kind: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """新增一条笔记，返回 id/created_at 等字段。"""
    if kind not in _NOTE_TYPES:
        raise ValueError(f"kind 必须在 {_NOTE_TYPES} 中，收到: {kind!r}")
    now = time.time()
    note_id = f"N{uuid.uuid4().hex}"
    conn = _get_conn()
    _init_schema(conn)
    conn.execute(
        "INSERT INTO notes(id, created_at, updated_at, title, content, kind, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (note_id, now, now, title or "", content or "", kind, json.dumps(tags or [], ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    return {"id": note_id, "created_at": now, "updated_at": now, "kind": kind}


def update(note_id: str, *, title: str | None = None, content: str | None = None, tags: list[str] | None = None) -> dict[str, Any]:
    """更新笔记的字段，返回最新元信息。"""
    conn = _get_conn()
    _init_schema(conn)
    row = conn.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        conn.close()
        raise KeyError(f"笔记不存在: {note_id}")
    sets: list[str] = []
    vals: list[Any] = []
    if title is not None:
        sets.append("title = ?"); vals.append(title)
    if content is not None:
        sets.append("content = ?"); vals.append(content)
    if tags is not None:
        sets.append("tags = ?"); vals.append(json.dumps(tags, ensure_ascii=False))
    updated_at = time.time()
    sets.append("updated_at = ?"); vals.append(updated_at)
    vals.append(note_id)
    conn.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()
    conn.close()
    return get(note_id)


def remove(note_id: str) -> bool:
    """删除笔记及其关联反思（级联）。"""
    conn = _get_conn()
    _init_schema(conn)
    cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get(note_id: str) -> dict[str, Any]:
    """查询单条笔记；不存在抛 KeyError。"""
    conn = _get_conn()
    _init_schema(conn)
    row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    conn.close()
    if not row:
        raise KeyError(f"笔记不存在: {note_id}")
    return _row_to_dict(row)


def list_notes(
    kind: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """按条件分页列出笔记（不含 content 全文，节省 IO）。"""
    conn = _get_conn()
    _init_schema(conn)
    wheres: list[str] = []
    params: list[Any] = []
    if kind:
        wheres.append("kind = ?")
        params.append(kind)
    if tag:
        wheres.append("json_extract(tags, '$') LIKE ?")
        params.append(f"%{tag}%")
    where = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    rows = conn.execute(
        f"SELECT id, created_at, updated_at, title, kind, tags FROM notes {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    conn.close()
    return [_row_to_summary(r) for r in rows]


def add_reflection(
    note_id: str,
    full_text: str,
    source_len: int,
    truncated: bool = False,
) -> dict[str, Any]:
    """保存一次反思审计结果，返回记录。"""
    now = time.time()
    rid = f"R{uuid.uuid4().hex}"
    conn = _get_conn()
    _init_schema(conn)
    conn.execute(
        "INSERT INTO reflections(id, note_id, created_at, source_len, truncated, full_text) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rid, note_id, now, source_len, 1 if truncated else 0, full_text),
    )
    conn.execute("UPDATE notes SET reflect_id = ? WHERE id = ?", (rid, note_id))
    conn.commit()
    conn.close()
    return {"id": rid, "note_id": note_id, "created_at": now}


def get_reflection(note_id: str) -> dict[str, Any] | None:
    """取笔记的最新一次反思；无则返回 None。"""
    conn = _get_conn()
    _init_schema(conn)
    row = conn.execute(
        "SELECT r.id, r.note_id, r.created_at, r.source_len, r.truncated, r.full_text "
        "FROM reflections r JOIN notes n ON r.note_id = n.id WHERE n.id = ? ORDER BY r.created_at DESC LIMIT 1",
        (note_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def list_reflections(
    note_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """列出反思记录（不含 full_text）。"""
    conn = _get_conn()
    _init_schema(conn)
    where = "WHERE note_id = ?" if note_id else ""
    params = [note_id] if note_id else []
    rows = conn.execute(
        f"SELECT id, note_id, created_at, source_len, truncated FROM reflections {where} ORDER BY created_at DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def count_notes(kind: str | None = None) -> int:
    conn = _get_conn()
    _init_schema(conn)
    where = "WHERE kind = ?" if kind else ""
    params = [kind] if kind else []
    row = conn.execute(f"SELECT COUNT(*) FROM notes {where}", params).fetchone()
    conn.close()
    return row[0]


def clear_all() -> int:
    """清除全部笔记与反思（用于测试/重置）。"""
    conn = _get_conn()
    _init_schema(conn)
    conn.execute("DELETE FROM reflections")
    conn.execute("DELETE FROM notes")
    conn.commit()
    n = conn.total_changes
    conn.close()
    return n


def stream_from_file(path: str) -> Iterator[str]:
    """流式读取文件内容（用于导入外部笔记）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        while True:
            chunk = fh.read(8192)
            if not chunk:
                break
            yield chunk


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    if "tags" in data and isinstance(data["tags"], str):
        try:
            data["tags"] = json.loads(data["tags"])
        except json.JSONDecodeError:
            data["tags"] = []
    return data


def _row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if "tags" in d and isinstance(d["tags"], str):
        try:
            d["tags"] = json.loads(d["tags"])
        except Exception:
            d["tags"] = []
    return d

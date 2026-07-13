"""SQLite persistence for NASDX generated research artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


PROJECT_DIR = Path(__file__).parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "nasdx_history.db"
SCHEMA_VERSION = "nasdx_history.v2"
BUSY_TIMEOUT_MS = 5_000
_INIT_LOCK = threading.RLock()

_SPECIALIZED = {
    "report_history": {
        "artifact_type": "report_history",
        "key_column": "stock_code",
        "columns": ("stock_code", "report_date", "generated_at", "source_path", "artifact_id", "created_at"),
    },
    "daily_scans": {
        "artifact_type": "daily_scan",
        "key_column": "scan_type",
        "columns": ("scan_type", "scan_date", "generated_at", "source_path", "artifact_id", "created_at"),
    },
    "etf_pools": {
        "artifact_type": "etf_pool",
        "key_column": "pool_name",
        "columns": ("pool_name", "loaded_at", "source_path", "artifact_id", "created_at"),
    },
}


def history_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the SQLite history database path."""
    if db_path:
        return Path(db_path)
    env_path = os.environ.get("NASDX_HISTORY_DB")
    return Path(env_path) if env_path else DEFAULT_DB_PATH


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute("pragma foreign_keys = on")
    conn.execute(f"pragma busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def init_history_db(db_path: str | Path | None = None) -> Path:
    """Create or migrate the canonical-payload history schema."""
    path = history_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _INIT_LOCK, closing(_connect(path)) as conn:
        conn.execute("pragma journal_mode = wal")
        with conn:
            conn.execute(
                """
                create table if not exists artifacts (
                    id integer primary key autoincrement,
                    artifact_type text not null,
                    artifact_key text not null,
                    generated_at text,
                    source_path text,
                    schema_version text not null,
                    payload_json text not null,
                    payload_hash text not null,
                    created_at text not null
                )
                """
            )
            conn.execute(
                "create index if not exists idx_artifacts_type_key_id on artifacts (artifact_type, artifact_key, id)"
            )
            for table in _SPECIALIZED:
                if _table_exists(conn, table) and "payload_json" in _table_columns(conn, table):
                    _migrate_legacy_table(conn, table)
                else:
                    _create_specialized_table(conn, table)
            conn.execute(f"pragma user_version = 2")
    return path


def record_artifact(
    artifact_type: str,
    artifact_key: str,
    payload: Any,
    generated_at: str | None = None,
    source_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Store a generated artifact payload and return its row metadata."""
    payload_json, payload_hash = _serialize_payload(payload)
    created_at = _now()
    path = init_history_db(db_path)
    with closing(_connect(path)) as conn:
        with conn:
            row_id = _insert_artifact(
                conn,
                artifact_type,
                artifact_key,
                generated_at,
                source_path,
                payload_json,
                payload_hash,
                created_at,
            )
    return {"id": row_id, "db_path": str(path), "payload_hash": payload_hash, "created_at": created_at}


def latest_artifact(
    artifact_type: str,
    artifact_key: str | None = None,
    db_path: str | Path | None = None,
) -> Dict[str, Any] | None:
    """Return the latest artifact row with decoded payload."""
    path = init_history_db(db_path)
    query = (
        "select id, artifact_type, artifact_key, generated_at, source_path, "
        "schema_version, payload_json, payload_hash, created_at "
        "from artifacts where artifact_type = ?"
    )
    args: list[Any] = [artifact_type]
    if artifact_key is not None:
        query += " and artifact_key = ?"
        args.append(artifact_key)
    query += " order by id desc limit 1"
    with closing(_connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, args).fetchone()
    return _decode_artifact_row(row) if row is not None else None


def artifact_counts(db_path: str | Path | None = None) -> Dict[str, int]:
    """Return artifact row counts grouped by type."""
    path = init_history_db(db_path)
    with closing(_connect(path)) as conn:
        rows = conn.execute("select artifact_type, count(*) from artifacts group by artifact_type").fetchall()
    return {str(name): int(count) for name, count in rows}


def record_report_history(
    stock_code: str,
    report_date: str,
    payload: Any,
    source_path: str | Path | None = None,
    generated_at: str | None = None,
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _payload_time(payload) or report_date
    return _record_specialized(
        "report_history",
        stock_code,
        payload,
        (stock_code, report_date, generated_at, _as_posix(source_path)),
        generated_at=generated_at,
        source_path=source_path,
        db_path=db_path,
    )


def record_daily_scan(
    scan_type: str,
    scan_date: str,
    payload: Any,
    source_path: str | Path | None = None,
    generated_at: str | None = None,
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    generated_at = generated_at or _payload_time(payload) or scan_date
    return _record_specialized(
        "daily_scans",
        scan_type,
        payload,
        (scan_type, scan_date, generated_at, _as_posix(source_path)),
        generated_at=generated_at,
        source_path=source_path,
        db_path=db_path,
    )


def record_etf_pool(
    pool_name: str,
    payload: Any,
    source_path: str | Path | None = None,
    loaded_at: str | None = None,
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    loaded_at = loaded_at or _payload_time(payload) or _now()
    return _record_specialized(
        "etf_pools",
        pool_name,
        payload,
        (pool_name, loaded_at, _as_posix(source_path)),
        generated_at=loaded_at,
        source_path=source_path,
        db_path=db_path,
    )


def _record_specialized(
    table: str,
    artifact_key: str,
    payload: Any,
    metadata_values: tuple[Any, ...],
    *,
    generated_at: str,
    source_path: str | Path | None,
    db_path: str | Path | None,
) -> Dict[str, Any]:
    payload_json, payload_hash = _serialize_payload(payload)
    created_at = _now()
    path = init_history_db(db_path)
    config = _SPECIALIZED[table]
    with closing(_connect(path)) as conn:
        with conn:
            artifact_id = _insert_artifact(
                conn,
                config["artifact_type"],
                artifact_key,
                generated_at,
                source_path,
                payload_json,
                payload_hash,
                created_at,
            )
            row_id = _insert_specialized_row(conn, table, (*metadata_values, artifact_id, created_at))
    return {
        "id": row_id,
        "artifact_id": artifact_id,
        "db_path": str(path),
        "payload_hash": payload_hash,
        "created_at": created_at,
    }


def _insert_artifact(
    conn: sqlite3.Connection,
    artifact_type: str,
    artifact_key: str,
    generated_at: str | None,
    source_path: str | Path | None,
    payload_json: str,
    payload_hash: str,
    created_at: str,
) -> int:
    cursor = conn.execute(
        """
        insert into artifacts (
            artifact_type, artifact_key, generated_at, source_path,
            schema_version, payload_json, payload_hash, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_type,
            artifact_key,
            generated_at,
            _as_posix(source_path),
            SCHEMA_VERSION,
            payload_json,
            payload_hash,
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def _insert_specialized_row(conn: sqlite3.Connection, table: str, values: tuple[Any, ...]) -> int:
    config = _SPECIALIZED[table]
    columns = config["columns"]
    placeholders = ", ".join("?" for _ in columns)
    cursor = conn.execute(
        f"insert into {table} ({', '.join(columns)}) values ({placeholders})",
        values,
    )
    return int(cursor.lastrowid)


def audit_history_consistency(db_path: str | Path | None = None) -> Dict[str, Any]:
    """Report specialized rows that have no matching canonical artifact."""
    path = init_history_db(db_path)
    orphans = []
    with closing(_connect(path)) as conn:
        for table, config in _SPECIALIZED.items():
            rows = conn.execute(
                f"""
                select s.id, s.artifact_id
                from {table} s
                left join artifacts a on a.id = s.artifact_id
                where a.id is null or a.artifact_type != ? or a.artifact_key != s.{config['key_column']}
                """,
                (config["artifact_type"],),
            ).fetchall()
            orphans.extend({"table": table, "row_id": row_id, "artifact_id": artifact_id} for row_id, artifact_id in rows)
        artifact_count = conn.execute("select count(*) from artifacts").fetchone()[0]
    return {"db_path": str(path), "artifact_count": int(artifact_count), "orphans": orphans}


def _create_specialized_table(conn: sqlite3.Connection, table: str, *, target: str | None = None) -> None:
    name = target or table
    if table == "report_history":
        columns = "stock_code text not null, report_date text not null, generated_at text, source_path text"
        index = "stock_code"
    elif table == "daily_scans":
        columns = "scan_type text not null, scan_date text not null, generated_at text, source_path text"
        index = "scan_type"
    else:
        columns = "pool_name text not null, loaded_at text not null, source_path text"
        index = "pool_name"
    conn.execute(
        f"""
        create table if not exists {name} (
            id integer primary key autoincrement,
            {columns},
            artifact_id integer not null,
            created_at text not null,
            foreign key (artifact_id) references artifacts(id) on delete cascade
        )
        """
    )
    if target is None:
        conn.execute(f"create index if not exists idx_{table}_{index}_id on {table} ({index}, id)")


def _migrate_legacy_table(conn: sqlite3.Connection, table: str) -> None:
    config = _SPECIALIZED[table]
    temp_table = f"{table}_v2"
    conn.execute(f"drop table if exists {temp_table}")
    _create_specialized_table(conn, table, target=temp_table)
    metadata_columns = [column for column in config["columns"] if column not in {"artifact_id", "created_at"}]
    select_columns = ["id", *metadata_columns, "payload_json", "payload_hash", "created_at"]
    for row in conn.execute(f"select {', '.join(select_columns)} from {table} order by id").fetchall():
        legacy_id = row[0]
        metadata = row[1 : 1 + len(metadata_columns)]
        payload_json, payload_hash, created_at = row[-3:]
        key = str(metadata[0])
        generated_at = metadata[2] if table != "etf_pools" else metadata[1]
        source_path = metadata[3] if table != "etf_pools" else metadata[2]
        candidate = conn.execute(
            f"""
            select id from artifacts
            where artifact_type = ? and artifact_key = ? and payload_hash = ?
              and id not in (select artifact_id from {temp_table})
            order by id limit 1
            """,
            (config["artifact_type"], key, payload_hash),
        ).fetchone()
        artifact_id = candidate[0] if candidate else _insert_artifact(
            conn,
            config["artifact_type"],
            key,
            generated_at,
            source_path,
            payload_json,
            payload_hash,
            created_at,
        )
        values = (*metadata, artifact_id, created_at)
        columns = config["columns"]
        conn.execute(
            f"insert into {temp_table} (id, {', '.join(columns)}) values (?, {', '.join('?' for _ in columns)})",
            (legacy_id, *values),
        )
    conn.execute(f"drop table {table}")
    conn.execute(f"alter table {temp_table} rename to {table}")
    index_column = config["key_column"]
    conn.execute(f"create index if not exists idx_{table}_{index_column}_id on {table} ({index_column}, id)")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("select 1 from sqlite_master where type = 'table' and name = ?", (table,)).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"pragma table_info({table})")}


def _serialize_payload(payload: Any) -> tuple[str, str]:
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return payload_json, payload_hash


def _decode_artifact_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload_json"))
    return data


def _payload_time(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("generated_at") or payload.get("datetime") or payload.get("date")
    return str(value) if value else None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_posix(path: str | Path | None) -> str | None:
    return Path(path).as_posix() if path is not None else None

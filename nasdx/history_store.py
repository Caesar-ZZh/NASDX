"""SQLite persistence for NASDX generated research artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


PROJECT_DIR = Path(__file__).parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "nasdx_history.db"
SCHEMA_VERSION = "nasdx_history.v1"


def history_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the SQLite history database path."""
    if db_path:
        return Path(db_path)
    env_path = os.environ.get("NASDX_HISTORY_DB")
    return Path(env_path) if env_path else DEFAULT_DB_PATH


def init_history_db(db_path: str | Path | None = None) -> Path:
    """Create the NASDX history schema if needed."""
    path = history_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            conn.executescript(
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
            );
            create index if not exists idx_artifacts_type_key_id
                on artifacts (artifact_type, artifact_key, id);

            create table if not exists report_history (
                id integer primary key autoincrement,
                stock_code text not null,
                report_date text not null,
                generated_at text,
                source_path text,
                payload_json text not null,
                payload_hash text not null,
                created_at text not null
            );
            create index if not exists idx_report_history_stock_id
                on report_history (stock_code, id);

            create table if not exists daily_scans (
                id integer primary key autoincrement,
                scan_type text not null,
                scan_date text not null,
                generated_at text,
                source_path text,
                payload_json text not null,
                payload_hash text not null,
                created_at text not null
            );
            create index if not exists idx_daily_scans_type_id
                on daily_scans (scan_type, id);

            create table if not exists etf_pools (
                id integer primary key autoincrement,
                pool_name text not null,
                loaded_at text not null,
                source_path text,
                payload_json text not null,
                payload_hash text not null,
                created_at text not null
            );
            create index if not exists idx_etf_pools_name_id
                on etf_pools (pool_name, id);
                """
            )
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
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            cursor = conn.execute(
                """
            insert into artifacts (
                artifact_type, artifact_key, generated_at, source_path,
                schema_version, payload_json, payload_hash, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
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
            row_id = int(cursor.lastrowid)
    return {
        "id": row_id,
        "db_path": str(path),
        "payload_hash": payload_hash,
        "created_at": created_at,
    }


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
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, args).fetchone()
    if row is None:
        return None
    return _decode_artifact_row(row)


def artifact_counts(db_path: str | Path | None = None) -> Dict[str, int]:
    """Return artifact row counts grouped by type."""
    path = init_history_db(db_path)
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "select artifact_type, count(*) from artifacts group by artifact_type"
        ).fetchall()
    return {str(name): int(count) for name, count in rows}


def record_report_history(
    stock_code: str,
    report_date: str,
    payload: Any,
    source_path: str | Path | None = None,
    generated_at: str | None = None,
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Store a single-stock report history row."""
    generated_at = generated_at or _payload_time(payload) or report_date
    payload_json, payload_hash = _serialize_payload(payload)
    created_at = _now()
    path = init_history_db(db_path)
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            cursor = conn.execute(
                """
            insert into report_history (
                stock_code, report_date, generated_at, source_path,
                payload_json, payload_hash, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stock_code,
                    report_date,
                    generated_at,
                    _as_posix(source_path),
                    payload_json,
                    payload_hash,
                    created_at,
                ),
            )
            row_id = int(cursor.lastrowid)
    record_artifact(
        "report_history",
        stock_code,
        payload,
        generated_at=generated_at,
        source_path=source_path,
        db_path=path,
    )
    return {"id": row_id, "db_path": str(path), "payload_hash": payload_hash, "created_at": created_at}


def record_daily_scan(
    scan_type: str,
    scan_date: str,
    payload: Any,
    source_path: str | Path | None = None,
    generated_at: str | None = None,
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Store one daily scanner output."""
    generated_at = generated_at or _payload_time(payload) or scan_date
    payload_json, payload_hash = _serialize_payload(payload)
    created_at = _now()
    path = init_history_db(db_path)
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            cursor = conn.execute(
                """
            insert into daily_scans (
                scan_type, scan_date, generated_at, source_path,
                payload_json, payload_hash, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_type,
                    scan_date,
                    generated_at,
                    _as_posix(source_path),
                    payload_json,
                    payload_hash,
                    created_at,
                ),
            )
            row_id = int(cursor.lastrowid)
    record_artifact(
        "daily_scan",
        scan_type,
        payload,
        generated_at=generated_at,
        source_path=source_path,
        db_path=path,
    )
    return {"id": row_id, "db_path": str(path), "payload_hash": payload_hash, "created_at": created_at}


def record_etf_pool(
    pool_name: str,
    payload: Any,
    source_path: str | Path | None = None,
    loaded_at: str | None = None,
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Store an ETF pool snapshot."""
    loaded_at = loaded_at or _payload_time(payload) or _now()
    payload_json, payload_hash = _serialize_payload(payload)
    created_at = _now()
    path = init_history_db(db_path)
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            cursor = conn.execute(
                """
            insert into etf_pools (
                pool_name, loaded_at, source_path,
                payload_json, payload_hash, created_at
            )
            values (?, ?, ?, ?, ?, ?)
                """,
                (
                    pool_name,
                    loaded_at,
                    _as_posix(source_path),
                    payload_json,
                    payload_hash,
                    created_at,
                ),
            )
            row_id = int(cursor.lastrowid)
    record_artifact(
        "etf_pool",
        pool_name,
        payload,
        generated_at=loaded_at,
        source_path=source_path,
        db_path=path,
    )
    return {"id": row_id, "db_path": str(path), "payload_hash": payload_hash, "created_at": created_at}


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
    if path is None:
        return None
    return Path(path).as_posix()

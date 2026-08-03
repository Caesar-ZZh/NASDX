"""
Authoritative portfolio state for NASDX (#66).

An append-only SQLite ledger of trade events is the single source of truth for
"what do I actually hold". Every portfolio snapshot is *derived* by replaying
active events, so the same ledger always yields the same holdings, the same
monotonic ``portfolio_version`` and the same ``snapshot_hash``.

Design contract
---------------
* Events are never mutated or deleted. Corrections insert a replacement event
  and mark the original ``superseded_by``; the audit chain stays intact.
* Every write path shares one guard, ``_validate_event_against_ledger``: a
  correction can never introduce a fill that ``add_event`` would have rejected
  (#70). Corrections are judged against the holdings that remain *after* the
  superseded event is removed.
* ``event_id`` is unique. Re-submitting the same id (or re-importing the same
  CSV row, whose id is derived from its economics) is a no-op.
* Snapshots fail **closed**: missing prices, an unset cash baseline, negative
  cash or an unreadable ledger set ``fail_closed=True`` so downstream layers
  must not emit a confident "buy / add" action.
* Storage is local only. No broker account, credential or API key is stored;
  free-form text is redacted by ``nasdx.decision_log.sanitize_text``.

CLI::

    python -m nasdx.portfolio_store show
    python -m nasdx.portfolio_store add-trade --code 601101 --side buy --qty 100 --price 10.73
    python -m nasdx.portfolio_store import-csv trades.csv
    python -m nasdx.portfolio_store correct --event-id <id> --price 10.75
    python -m nasdx.portfolio_store correct --event-id <id> --qty 137 --allow-odd-lot
    python -m nasdx.portfolio_store set-cash --amount 100000
    python -m nasdx.portfolio_store status | export | backup | clear
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from nasdx.paths import get_runtime_dir
from nasdx.trade_events import (
    LotSizeError,
    TradeEvent,
    TradeEventError,
    build_trade_event,
    check_lot_size,
    classify_asset_class,
    derive_event_id,
    normalize_code,
    parse_trade_csv,
)


SCHEMA_VERSION = "nasdx_portfolio.v1"
SNAPSHOT_SCHEMA = "nasdx_portfolio_snapshot.v1"
DEFAULT_DB_NAME = "nasdx_portfolio.db"
PORTFOLIO_DB_ENV = "NASDX_PORTFOLIO_DB"
BUSY_TIMEOUT_MS = 5_000
DEFAULT_SINGLE_NAME_CAP_PCT = 10.0
DEFAULT_INDUSTRY_CAP_PCT = 30.0
_QUANTITY_EPS = 1e-9
_INIT_LOCK = threading.RLock()
_VOID_MARKER = "__void__"
# Replay tie-break inside one timestamp: increases before decreases.
_SIDE_REPLAY_RANK = {"buy": 0, "adjustment": 1, "dividend": 2, "fee": 3, "sell": 4}


class PortfolioLedgerError(RuntimeError):
    """Raised when the ledger cannot be read or is internally inconsistent."""


@dataclass(frozen=True)
class PortfolioPosition:
    """A derived open position."""

    code: str
    name: str
    asset_class: str
    industry: str
    quantity: float
    avg_cost: float
    cost_basis: float
    realized_pnl: float
    last_price: float | None
    price_as_of: str
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pct: float | None
    weight_pct: float | None
    valuation_status: str
    last_event_at: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Deterministic portfolio state derived from the event ledger."""

    schema: str
    generated_at: str
    portfolio_version: int
    ledger_hash: str
    snapshot_hash: str
    event_count: int
    active_event_count: int
    cash: float | None
    cash_status: str
    cash_baseline: float | None
    total_market_value: float
    total_cost_basis: float
    total_assets: float | None
    gross_exposure: float
    exposure_pct: float | None
    realized_pnl: float
    unrealized_pnl: float
    positions: List[Dict[str, Any]]
    closed_positions: List[Dict[str, Any]]
    asset_class_exposure: Dict[str, float]
    industry_exposure: Dict[str, float]
    policy: Dict[str, float]
    fail_closed: bool
    blocking_reasons: List[str]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def position(self, code: str) -> Dict[str, Any] | None:
        """Return the open position for a code, if any."""
        target = normalize_code(code)
        for row in self.positions:
            if row.get("code") == target:
                return row
        return None


# ---------------------------------------------------------------------------
# storage primitives
# ---------------------------------------------------------------------------


def portfolio_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the ledger database path (param > env > runtime dir)."""
    if db_path:
        return Path(db_path).expanduser()
    configured = os.environ.get(PORTFOLIO_DB_ENV)
    if configured:
        return Path(configured).expanduser()
    return get_runtime_dir() / DEFAULT_DB_NAME


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"pragma busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def init_portfolio_db(db_path: str | Path | None = None) -> Path:
    """Create the ledger schema when missing and return the database path."""
    path = portfolio_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _INIT_LOCK, closing(_connect(path)) as conn:
        with conn:
            conn.execute(
                """
                create table if not exists trade_events (
                    seq integer primary key autoincrement,
                    event_id text not null unique,
                    occurred_at text not null,
                    code text not null,
                    name text not null default '',
                    side text not null,
                    quantity real not null,
                    price real not null,
                    fee real not null default 0,
                    tax real not null default 0,
                    source text not null default 'manual',
                    note text not null default '',
                    corrects text not null default '',
                    recorded_at text not null,
                    superseded_by text not null default '',
                    superseded_at text not null default '',
                    supersede_reason text not null default ''
                )
                """
            )
            conn.execute("create index if not exists idx_trade_events_code on trade_events (code)")
            conn.execute(
                "create index if not exists idx_trade_events_time on trade_events (occurred_at, seq)"
            )
            conn.execute(
                "create table if not exists portfolio_meta (key text primary key, value text not null)"
            )
            conn.execute(
                "insert or ignore into portfolio_meta (key, value) values ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            conn.execute(
                "insert or ignore into portfolio_meta (key, value) values ('version', '0')"
            )
    return path


def _read_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("select value from portfolio_meta where key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "insert into portfolio_meta (key, value) values (?, ?) "
        "on conflict(key) do update set value = excluded.value",
        (key, value),
    )


def _bump_version(conn: sqlite3.Connection) -> int:
    current = _read_meta(conn, "version", "0")
    try:
        number = int(current)
    except (TypeError, ValueError):
        number = 0
    number += 1
    _write_meta(conn, "version", str(number))
    return number


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# write paths
# ---------------------------------------------------------------------------


def add_event(
    event: TradeEvent,
    db_path: str | Path | None = None,
    enforce_lot_rules: bool = True,
) -> Dict[str, Any]:
    """Append one event. Duplicate ``event_id`` is a no-op.

    Returns ``{"status": "recorded"|"duplicate", "event_id", "seq",
    "portfolio_version", "lot_warnings"}``.
    """
    if not isinstance(event, TradeEvent):
        raise TradeEventError(f"add_event 需要 TradeEvent，收到 {type(event).__name__}")
    path = init_portfolio_db(db_path)
    with closing(_connect(path)) as conn:
        existing = conn.execute(
            "select seq from trade_events where event_id = ?", (event.event_id,)
        ).fetchone()
        if existing:
            return {
                "status": "duplicate",
                "event_id": event.event_id,
                "seq": int(existing["seq"]),
                "portfolio_version": int(_read_meta(conn, "version", "0") or 0),
                "lot_warnings": [],
            }
        lot_warnings = _validate_event_against_ledger(conn, event)
        if lot_warnings and enforce_lot_rules:
            raise LotSizeError("；".join(lot_warnings))
        with conn:
            cursor = conn.execute(
                """
                insert into trade_events
                    (event_id, occurred_at, code, name, side, quantity, price, fee, tax,
                     source, note, corrects, recorded_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.occurred_at,
                    event.code,
                    event.name,
                    event.side,
                    float(event.quantity),
                    float(event.price),
                    float(event.fee),
                    float(event.tax),
                    event.source,
                    event.note,
                    event.corrects,
                    _now_iso(),
                ),
            )
            version = _bump_version(conn)
        return {
            "status": "recorded",
            "event_id": event.event_id,
            "seq": int(cursor.lastrowid),
            "portfolio_version": version,
            "lot_warnings": lot_warnings,
        }


def add_trade(
    code: str,
    side: str,
    quantity: Any = 0,
    price: Any = 0,
    occurred_at: Any = None,
    name: str = "",
    fee: Any = 0,
    tax: Any = 0,
    source: str = "manual",
    note: str = "",
    event_id: str | None = None,
    db_path: str | Path | None = None,
    enforce_lot_rules: bool = True,
) -> Dict[str, Any]:
    """Validate and append a trade in one call."""
    event = build_trade_event(
        code=code,
        side=side,
        quantity=quantity,
        price=price,
        occurred_at=occurred_at,
        name=name,
        fee=fee,
        tax=tax,
        source=source,
        note=note,
        event_id=event_id,
    )
    return add_event(event, db_path=db_path, enforce_lot_rules=enforce_lot_rules)


def import_trades_csv(
    csv_text: str | None = None,
    csv_path: str | Path | None = None,
    db_path: str | Path | None = None,
    source: str = "csv_import",
) -> Dict[str, Any]:
    """Import a CSV ledger idempotently and return a full audit result."""
    if csv_text is None:
        if not csv_path:
            raise TradeEventError("import_trades_csv 需要 csv_text 或 csv_path")
        csv_text = Path(csv_path).read_text(encoding="utf-8-sig")
    events, rejected = parse_trade_csv(csv_text, source=source)
    recorded: List[str] = []
    duplicates: List[str] = []
    lot_warnings: List[str] = []
    for event in events:
        result = add_event(event, db_path=db_path, enforce_lot_rules=False)
        if result["status"] == "recorded":
            recorded.append(event.event_id)
        else:
            duplicates.append(event.event_id)
        lot_warnings.extend(result.get("lot_warnings") or [])
    return {
        "source": str(csv_path) if csv_path else "<text>",
        "parsed": len(events),
        "recorded": len(recorded),
        "duplicate": len(duplicates),
        "rejected": rejected,
        "recorded_event_ids": recorded,
        "duplicate_event_ids": duplicates,
        "lot_warnings": lot_warnings,
        "audit": (
            f"解析 {len(events)} 条，新记 {len(recorded)} 条，"
            f"重复跳过 {len(duplicates)} 条，拒绝 {len(rejected)} 条。"
        ),
    }


def correct_event(
    event_id: str,
    db_path: str | Path | None = None,
    reason: str = "",
    replacement: TradeEvent | None = None,
    enforce_lot_rules: bool = True,
    **replacement_fields: Any,
) -> Dict[str, Any]:
    """Supersede an event with a compensating replacement (or void it).

    The original row is preserved and flagged; snapshots replay only active
    events. Pass no replacement fields to void the event entirely.

    A replacement that changes the security, the direction or the size goes
    through the same board/lot guard as ``add_event`` (#70), measured against
    the holdings that remain once the original event is superseded. Corrections
    that only touch price/fee/tax/time keep the exposure the ledger already
    accepted and are not re-judged. Validation runs *before* the transaction
    opens, so a rejected correction leaves the original active and the ledger
    byte-identical.

    ``enforce_lot_rules=False`` is a high-risk audit/migration escape hatch: it
    still reports every violation in ``lot_warnings`` instead of silently
    accepting an unexecutable quantity. The CLI keeps it fail-closed unless
    ``--allow-odd-lot`` is passed explicitly.
    """
    target_id = str(event_id or "").strip()
    if not target_id:
        raise TradeEventError("correct_event 需要 event_id")
    path = init_portfolio_db(db_path)
    with closing(_connect(path)) as conn:
        row = conn.execute("select * from trade_events where event_id = ?", (target_id,)).fetchone()
        if row is None:
            raise TradeEventError(f"未找到事件 {target_id}")
        if row["superseded_by"]:
            raise TradeEventError(f"事件 {target_id} 已被 {row['superseded_by']} 修正，不能重复修正")

        new_event: TradeEvent | None = None
        if replacement is not None:
            new_event = replacement
        elif replacement_fields:
            payload = {
                "code": row["code"],
                "side": row["side"],
                "quantity": row["quantity"],
                "price": row["price"],
                "occurred_at": row["occurred_at"],
                "name": row["name"],
                "fee": row["fee"],
                "tax": row["tax"],
                "source": row["source"],
                "note": row["note"],
            }
            payload.update({key: value for key, value in replacement_fields.items() if value is not None})
            payload["corrects"] = target_id
            payload.setdefault("event_id", None)
            new_event = build_trade_event(**payload)

        if new_event is not None:
            if not isinstance(new_event, TradeEvent):
                raise TradeEventError(
                    f"correct_event 的 replacement 需要 TradeEvent，收到 {type(new_event).__name__}"
                )
            # Guard first: nothing is written until the replacement is legal, so
            # a rejected correction cannot leave the original superseded or a
            # half-applied replacement behind.
            lot_warnings: List[str] = []
            if _changes_lot_exposure(row, new_event):
                lot_warnings = _validate_event_against_ledger(
                    conn, new_event, replacing_event_id=target_id
                )
                if lot_warnings and enforce_lot_rules:
                    raise LotSizeError("；".join(lot_warnings))
            new_event = _ensure_unique_event_id(conn, new_event, target_id)
            with conn:
                conn.execute(
                    """
                    insert into trade_events
                        (event_id, occurred_at, code, name, side, quantity, price, fee, tax,
                         source, note, corrects, recorded_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_event.event_id,
                        new_event.occurred_at,
                        new_event.code,
                        new_event.name,
                        new_event.side,
                        float(new_event.quantity),
                        float(new_event.price),
                        float(new_event.fee),
                        float(new_event.tax),
                        new_event.source,
                        new_event.note,
                        target_id,
                        _now_iso(),
                    ),
                )
                conn.execute(
                    "update trade_events set superseded_by = ?, superseded_at = ?, supersede_reason = ? "
                    "where event_id = ?",
                    (new_event.event_id, _now_iso(), str(reason or "")[:200], target_id),
                )
                version = _bump_version(conn)
            return {
                "status": "corrected",
                "original_event_id": target_id,
                "replacement_event_id": new_event.event_id,
                "portfolio_version": version,
                "lot_warnings": lot_warnings,
            }

        with conn:
            conn.execute(
                "update trade_events set superseded_by = ?, superseded_at = ?, supersede_reason = ? "
                "where event_id = ?",
                (_VOID_MARKER, _now_iso(), str(reason or "")[:200], target_id),
            )
            version = _bump_version(conn)
        return {
            "status": "voided",
            "original_event_id": target_id,
            "replacement_event_id": "",
            "portfolio_version": version,
            "lot_warnings": [],
        }


def _ensure_unique_event_id(
    conn: sqlite3.Connection, event: TradeEvent, original_id: str
) -> TradeEvent:
    from nasdx.trade_events import with_event_id

    candidate = event.event_id
    if candidate == original_id or _event_exists(conn, candidate):
        base = derive_event_id(
            event.occurred_at,
            event.code,
            event.side,
            event.quantity,
            event.price,
            event.fee,
            event.tax,
            event.source,
        )
        index = 1
        candidate = f"{base}-fix{index}"
        while _event_exists(conn, candidate) or candidate == original_id:
            index += 1
            candidate = f"{base}-fix{index}"
        return with_event_id(event, candidate)
    return event


def _event_exists(conn: sqlite3.Connection, event_id: str) -> bool:
    return (
        conn.execute("select 1 from trade_events where event_id = ?", (event_id,)).fetchone()
        is not None
    )


def set_cash_baseline(
    amount: Any,
    as_of: Any = None,
    note: str = "",
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Record the account cash baseline that ledger cash flows are applied to."""
    if isinstance(amount, bool):
        raise TradeEventError("现金基线不能是布尔值")
    try:
        value = float(amount)
    except (TypeError, ValueError) as exc:
        raise TradeEventError(f"现金基线必须是数字，收到 {amount!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise TradeEventError("现金基线必须是有限数值")
    if value < 0:
        raise TradeEventError("现金基线不能为负数")
    path = init_portfolio_db(db_path)
    stamp = str(as_of) if as_of else _now_iso()
    with closing(_connect(path)) as conn, conn:
        _write_meta(conn, "cash_baseline", repr(value))
        _write_meta(conn, "cash_baseline_as_of", stamp)
        _write_meta(conn, "cash_baseline_note", str(note or "")[:200])
        version = _bump_version(conn)
    return {"cash_baseline": value, "as_of": stamp, "portfolio_version": version}


# ---------------------------------------------------------------------------
# read paths
# ---------------------------------------------------------------------------


def list_events(
    db_path: str | Path | None = None,
    include_superseded: bool = False,
    code: str | None = None,
) -> List[Dict[str, Any]]:
    """Return ledger rows ordered by (occurred_at, seq)."""
    path = init_portfolio_db(db_path)
    query = "select * from trade_events"
    params: List[Any] = []
    clauses: List[str] = []
    if not include_superseded:
        clauses.append("superseded_by = ''")
    if code:
        clauses.append("code = ?")
        params.append(normalize_code(code))
    if clauses:
        query += " where " + " and ".join(clauses)
    query += " order by occurred_at asc, seq asc"
    with closing(_connect(path)) as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _held_quantity(
    conn: sqlite3.Connection,
    code: str,
    exclude_event_ids: Sequence[str] | None = None,
) -> float:
    """Active holdings for ``code``, optionally ignoring events about to leave.

    ``exclude_event_ids`` is used by the correction path: the event that is
    being superseded must not count towards the baseline the replacement is
    judged against, otherwise a resized fill is measured against holdings that
    include the very fill it replaces.
    """
    normalized = normalize_code(code)
    if not normalized:
        return 0.0
    rows = conn.execute(
        "select event_id, side, quantity from trade_events where code = ? and superseded_by = ''",
        (normalized,),
    ).fetchall()
    excluded = {str(item) for item in (exclude_event_ids or []) if item}
    total = 0.0
    for row in rows:
        if row["event_id"] in excluded:
            continue
        if row["side"] == "buy":
            total += float(row["quantity"])
        elif row["side"] == "sell":
            total -= float(row["quantity"])
        elif row["side"] == "adjustment":
            total += float(row["quantity"])
    return max(total, 0.0)


def _validate_event_against_ledger(
    conn: sqlite3.Connection,
    event: TradeEvent,
    replacing_event_id: str | None = None,
) -> List[str]:
    """Check a pending event against the ledger state it is about to join (#70).

    Shared by ``add_event`` and ``correct_event`` so both write paths apply the
    identical board/lot rule. The rule is resolved from ``event.code`` and
    ``event.side``, so a correction that changes the security or the direction
    is re-validated as the *replacement*, not as the original.

    Returns the list of violations (empty when compliant); callers decide
    whether to raise. Read-only: never mutates the ledger.
    """
    exclude = [replacing_event_id] if replacing_event_id else []
    held = _held_quantity(conn, event.code, exclude_event_ids=exclude)
    return check_lot_size(event.code, event.side, event.quantity, held_quantity=held)


def _changes_lot_exposure(original: Mapping[str, Any], replacement: TradeEvent) -> bool:
    """True when a correction alters the security, the direction or the size.

    A price/fee/timestamp-only correction carries exactly the lot exposure the
    ledger already accepted, so re-checking it would reject legitimate fixes to
    historical odd lots (broker receipts imported with
    ``enforce_lot_rules=False``) for a violation the correction never
    introduced — or reject a valid odd-lot liquidation simply because unrelated
    fills were recorded afterwards. Only exposure-changing corrections are
    re-validated.
    """
    if normalize_code(original["code"]) != replacement.code:
        return True
    if original["side"] != replacement.side:
        return True
    return abs(float(original["quantity"]) - float(replacement.quantity)) > _QUANTITY_EPS


def _resolve_price(entry: Any) -> Tuple[float | None, str]:
    if entry is None:
        return None, ""
    if isinstance(entry, Mapping):
        raw = entry.get("price", entry.get("close"))
        as_of = str(entry.get("as_of") or entry.get("price_as_of") or entry.get("data_date") or "")
    elif isinstance(entry, (tuple, list)) and len(entry) == 2:
        raw, as_of = entry[0], str(entry[1] or "")
    else:
        raw, as_of = entry, ""
    if isinstance(raw, bool) or raw is None:
        return None, as_of
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, as_of
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        return None, as_of
    return value, as_of


def build_snapshot(
    prices: Mapping[str, Any] | None = None,
    industry_map: Mapping[str, str] | None = None,
    db_path: str | Path | None = None,
    now: datetime | None = None,
    single_name_cap_pct: float = DEFAULT_SINGLE_NAME_CAP_PCT,
    industry_cap_pct: float = DEFAULT_INDUSTRY_CAP_PCT,
    strict: bool = False,
) -> PortfolioSnapshot:
    """Replay the ledger into a deterministic snapshot.

    ``strict=True`` re-raises ledger errors; the default returns a fail-closed
    snapshot so callers never mistake a broken ledger for an empty account.
    """
    generated_at = (now or datetime.now()).isoformat(timespec="seconds")
    try:
        rows, meta = _load_ledger(db_path)
    except (sqlite3.Error, OSError, PortfolioLedgerError) as exc:
        if strict:
            raise PortfolioLedgerError(str(exc)) from exc
        return _broken_snapshot(generated_at, str(exc), single_name_cap_pct, industry_cap_pct)

    warnings: List[str] = []
    blocking: List[str] = []
    active = [row for row in rows if not row["superseded_by"]]
    active.sort(key=_replay_key)

    books: Dict[str, Dict[str, Any]] = {}
    cash_flow = 0.0
    for row in active:
        cash_flow += _row_cash_delta(row)
        code = row["code"]
        if row["side"] in ("fee", "dividend") and not code:
            continue
        if not code:
            continue
        book = books.setdefault(
            code,
            {
                "code": code,
                "name": row["name"] or "",
                "quantity": 0.0,
                "cost_basis": 0.0,
                "realized_pnl": 0.0,
                "last_event_at": "",
            },
        )
        if row["name"] and not book["name"]:
            book["name"] = row["name"]
        book["last_event_at"] = row["occurred_at"]
        _apply_row(book, row, warnings, blocking)

    positions_raw = sorted(
        (book for book in books.values() if book["quantity"] > _QUANTITY_EPS),
        key=lambda item: item["code"],
    )
    closed_raw = sorted(
        (book for book in books.values() if book["quantity"] <= _QUANTITY_EPS),
        key=lambda item: item["code"],
    )

    cash_baseline = meta.get("cash_baseline")
    cash = None if cash_baseline is None else round(cash_baseline + cash_flow, 4)
    cash_status = "unknown" if cash is None else ("negative" if cash < -1e-6 else "known")

    price_map = {normalize_code(key): value for key, value in (prices or {}).items()}
    industry_lookup = {normalize_code(key): str(value) for key, value in (industry_map or {}).items()}

    priced: List[Dict[str, Any]] = []
    missing_price_codes: List[str] = []
    total_market_value = 0.0
    total_cost_basis = 0.0
    for book in positions_raw:
        last_price, price_as_of = _resolve_price(price_map.get(book["code"]))
        quantity = round(book["quantity"], 6)
        cost_basis = round(book["cost_basis"], 4)
        avg_cost = round(cost_basis / quantity, 6) if quantity > _QUANTITY_EPS else 0.0
        market_value = round(last_price * quantity, 4) if last_price is not None else None
        unrealized = round(market_value - cost_basis, 4) if market_value is not None else None
        unrealized_pct = (
            round(unrealized / cost_basis * 100, 4)
            if unrealized is not None and cost_basis > _QUANTITY_EPS
            else None
        )
        if market_value is None:
            missing_price_codes.append(book["code"])
        else:
            total_market_value += market_value
        total_cost_basis += cost_basis
        priced.append(
            {
                "code": book["code"],
                "name": book["name"],
                "asset_class": classify_asset_class(book["code"]),
                "industry": industry_lookup.get(book["code"], "未分类"),
                "quantity": quantity,
                "avg_cost": avg_cost,
                "cost_basis": cost_basis,
                "realized_pnl": round(book["realized_pnl"], 4),
                "last_price": last_price,
                "price_as_of": price_as_of,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "unrealized_pct": unrealized_pct,
                "weight_pct": None,
                "valuation_status": "priced" if market_value is not None else "missing_price",
                "last_event_at": book["last_event_at"],
            }
        )

    total_assets = round(total_market_value + cash, 4) if cash is not None else None
    if total_assets and total_assets > 0:
        for row in priced:
            if row["market_value"] is not None:
                row["weight_pct"] = round(row["market_value"] / total_assets * 100, 4)

    asset_class_exposure: Dict[str, float] = {}
    industry_exposure: Dict[str, float] = {}
    for row in priced:
        if row["market_value"] is None:
            continue
        asset_class_exposure[row["asset_class"]] = round(
            asset_class_exposure.get(row["asset_class"], 0.0) + row["market_value"], 4
        )
        industry_exposure[row["industry"]] = round(
            industry_exposure.get(row["industry"], 0.0) + row["market_value"], 4
        )

    realized_total = round(sum(book["realized_pnl"] for book in books.values()), 4)
    unrealized_total = round(
        sum(row["unrealized_pnl"] for row in priced if row["unrealized_pnl"] is not None), 4
    )

    if missing_price_codes:
        blocking.append("以下持仓缺最新有效价格，无法估值：" + "、".join(missing_price_codes))
    if cash is None:
        blocking.append("尚未设置现金基线（set-cash），可用现金未知。")
    elif cash < -1e-6:
        blocking.append(f"账本推导出的现金为负（{cash:.2f}），先补录出入金或修正成交。")

    ledger_hash = _ledger_hash(rows, meta)
    snapshot_hash = _snapshot_hash(ledger_hash, priced, cash, bool(blocking))

    return PortfolioSnapshot(
        schema=SNAPSHOT_SCHEMA,
        generated_at=generated_at,
        portfolio_version=int(meta.get("version") or 0),
        ledger_hash=ledger_hash,
        snapshot_hash=snapshot_hash,
        event_count=len(rows),
        active_event_count=len(active),
        cash=cash,
        cash_status=cash_status,
        cash_baseline=cash_baseline,
        total_market_value=round(total_market_value, 4),
        total_cost_basis=round(total_cost_basis, 4),
        total_assets=total_assets,
        gross_exposure=round(total_market_value, 4),
        exposure_pct=(
            round(total_market_value / total_assets * 100, 4)
            if total_assets and total_assets > 0
            else None
        ),
        realized_pnl=realized_total,
        unrealized_pnl=unrealized_total,
        positions=priced,
        closed_positions=[
            {
                "code": book["code"],
                "name": book["name"],
                "realized_pnl": round(book["realized_pnl"], 4),
                "last_event_at": book["last_event_at"],
            }
            for book in closed_raw
        ],
        asset_class_exposure=asset_class_exposure,
        industry_exposure=industry_exposure,
        policy={
            "single_name_cap_pct": float(single_name_cap_pct),
            "industry_cap_pct": float(industry_cap_pct),
        },
        fail_closed=bool(blocking),
        blocking_reasons=blocking,
        warnings=warnings,
    )


def _replay_key(row: Mapping[str, Any]) -> Tuple[str, int, str]:
    """Deterministic replay order, independent of local insertion order.

    Events are ordered by time; ties are broken by side so that an
    increase-then-decrease inside the same timestamp never looks like an
    oversell, and finally by ``event_id`` so two machines that recorded the
    same fills in a different order still replay identically.
    """
    return (row["occurred_at"], _SIDE_REPLAY_RANK.get(row["side"], 9), row["event_id"])


def _apply_row(
    book: Dict[str, Any], row: Mapping[str, Any], warnings: List[str], blocking: List[str]
) -> None:
    side = row["side"]
    quantity = float(row["quantity"])
    price = float(row["price"])
    charges = float(row["fee"]) + float(row["tax"])
    if side == "buy":
        book["quantity"] += quantity
        book["cost_basis"] += quantity * price + charges
        return
    if side == "sell":
        held = float(book["quantity"])
        if held <= _QUANTITY_EPS:
            message = f"{row['code']}: 出现无持仓卖出（{quantity:g} 股），账本可能漏记买入。"
            warnings.append(message)
            blocking.append(message)
            book["realized_pnl"] += quantity * price - charges
            return
        matched = min(quantity, held)
        avg_cost = float(book["cost_basis"]) / held if held else 0.0
        sold_cost = avg_cost * matched
        book["realized_pnl"] += matched * price - charges - sold_cost
        book["quantity"] = max(held - matched, 0.0)
        book["cost_basis"] = max(float(book["cost_basis"]) - sold_cost, 0.0)
        if quantity - held > _QUANTITY_EPS:
            message = (
                f"{row['code']}: 卖出数量 {quantity:g} 超过账本持仓 {held:g}，超出部分未纳入成本。"
            )
            warnings.append(message)
            blocking.append(message)
        return
    if side == "adjustment":
        book["quantity"] = max(book["quantity"] + quantity, 0.0)
        if price:
            book["realized_pnl"] += price - charges
        elif charges:
            book["realized_pnl"] -= charges
        return
    if side in ("fee", "dividend"):
        book["realized_pnl"] += (price if side == "dividend" else 0.0) - charges


def _row_cash_delta(row: Mapping[str, Any]) -> float:
    side = row["side"]
    charges = float(row["fee"]) + float(row["tax"])
    quantity = float(row["quantity"])
    price = float(row["price"])
    if side == "buy":
        return -(quantity * price + charges)
    if side == "sell":
        return quantity * price - charges
    if side == "fee":
        return -charges
    return price - charges  # dividend / adjustment


def _load_ledger(db_path: str | Path | None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    path = init_portfolio_db(db_path)
    with closing(_connect(path)) as conn:
        schema = _read_meta(conn, "schema_version", SCHEMA_VERSION)
        if schema != SCHEMA_VERSION:
            raise PortfolioLedgerError(
                f"账本 schema 版本不兼容：期望 {SCHEMA_VERSION}，实际 {schema}"
            )
        rows = [dict(row) for row in conn.execute("select * from trade_events order by seq asc")]
        baseline_raw = _read_meta(conn, "cash_baseline", "")
        try:
            baseline = float(baseline_raw) if baseline_raw != "" else None
        except (TypeError, ValueError):
            raise PortfolioLedgerError(f"现金基线数据损坏：{baseline_raw!r}")
        meta = {
            "version": int(_read_meta(conn, "version", "0") or 0),
            "cash_baseline": baseline,
            "cash_baseline_as_of": _read_meta(conn, "cash_baseline_as_of", ""),
            "db_path": str(path),
        }
    return rows, meta


def _broken_snapshot(
    generated_at: str, message: str, single_name_cap_pct: float, industry_cap_pct: float
) -> PortfolioSnapshot:
    reason = f"组合账本不可读，已 fail-closed：{message}"
    return PortfolioSnapshot(
        schema=SNAPSHOT_SCHEMA,
        generated_at=generated_at,
        portfolio_version=0,
        ledger_hash="",
        snapshot_hash="",
        event_count=0,
        active_event_count=0,
        cash=None,
        cash_status="unavailable",
        cash_baseline=None,
        total_market_value=0.0,
        total_cost_basis=0.0,
        total_assets=None,
        gross_exposure=0.0,
        exposure_pct=None,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        positions=[],
        closed_positions=[],
        asset_class_exposure={},
        industry_exposure={},
        policy={
            "single_name_cap_pct": float(single_name_cap_pct),
            "industry_cap_pct": float(industry_cap_pct),
        },
        fail_closed=True,
        blocking_reasons=[reason],
        warnings=[reason],
    )


def _ledger_hash(rows: Sequence[Mapping[str, Any]], meta: Mapping[str, Any]) -> str:
    # Hash the economic content only, ordered by (occurred_at, event_id). Local
    # insertion order (``seq``) is deliberately excluded so the same set of
    # fills always yields the same hash, whatever order it was recorded in.
    payload = sorted(
        (
            [
                row["occurred_at"],
                row["event_id"],
                row["code"],
                row["side"],
                round(float(row["quantity"]), 6),
                round(float(row["price"]), 6),
                round(float(row["fee"]), 6),
                round(float(row["tax"]), 6),
                row["superseded_by"],
                row["corrects"],
            ]
            for row in rows
        ),
        key=lambda item: (item[0], item[1]),
    )
    blob = json.dumps(
        {"events": payload, "cash_baseline": meta.get("cash_baseline")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _snapshot_hash(
    ledger_hash: str, positions: Sequence[Mapping[str, Any]], cash: float | None, fail_closed: bool
) -> str:
    payload = {
        "ledger": ledger_hash,
        "cash": None if cash is None else round(float(cash), 4),
        "fail_closed": bool(fail_closed),
        "positions": [
            [
                row["code"],
                round(float(row["quantity"]), 6),
                round(float(row["avg_cost"]), 6),
                row["last_price"],
                row["price_as_of"],
            ]
            for row in positions
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def current_snapshot_hash(
    prices: Mapping[str, Any] | None = None, db_path: str | Path | None = None
) -> str:
    """Return only the snapshot hash (cheap cache key for intraday paths)."""
    return build_snapshot(prices=prices, db_path=db_path).snapshot_hash


def portfolio_status(db_path: str | Path | None = None) -> Dict[str, Any]:
    """Return a diagnostic view of the ledger without valuing positions."""
    try:
        rows, meta = _load_ledger(db_path)
    except (sqlite3.Error, OSError, PortfolioLedgerError) as exc:
        return {"healthy": False, "error": str(exc), "db_path": str(portfolio_db_path(db_path))}
    active = [row for row in rows if not row["superseded_by"]]
    return {
        "healthy": True,
        "schema": SCHEMA_VERSION,
        "db_path": meta["db_path"],
        "portfolio_version": meta["version"],
        "event_count": len(rows),
        "active_event_count": len(active),
        "superseded_count": len(rows) - len(active),
        "cash_baseline": meta["cash_baseline"],
        "cash_baseline_as_of": meta["cash_baseline_as_of"],
        "first_event_at": active[0]["occurred_at"] if active else "",
        "last_event_at": active[-1]["occurred_at"] if active else "",
        "sources": sorted({row["source"] for row in rows}),
    }


def export_events(
    db_path: str | Path | None = None, include_superseded: bool = True
) -> Dict[str, Any]:
    """Export the full ledger (audit chain included) as a JSON-ready dict."""
    rows = list_events(db_path=db_path, include_superseded=include_superseded)
    status = portfolio_status(db_path=db_path)
    return {
        "schema": SCHEMA_VERSION,
        "exported_at": _now_iso(),
        "status": status,
        "events": rows,
    }


def backup_portfolio(destination: str | Path, db_path: str | Path | None = None) -> str:
    """Copy the ledger database to ``destination`` and return the new path."""
    source = init_portfolio_db(db_path)
    target = Path(destination).expanduser()
    if target.is_dir():
        target = target / f"nasdx_portfolio_{datetime.now():%Y%m%d_%H%M%S}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(source)) as conn, closing(sqlite3.connect(target)) as backup_conn:
        conn.backup(backup_conn)
    return str(target)


def clear_portfolio(confirm: bool = False, db_path: str | Path | None = None) -> Dict[str, Any]:
    """Delete all ledger events. Requires ``confirm=True``."""
    if not confirm:
        raise PortfolioLedgerError("clear_portfolio 需要 confirm=True，避免误删账本")
    path = init_portfolio_db(db_path)
    with closing(_connect(path)) as conn, conn:
        removed = conn.execute("select count(*) as n from trade_events").fetchone()["n"]
        conn.execute("delete from trade_events")
        conn.execute("delete from portfolio_meta where key like 'cash_baseline%'")
        version = _bump_version(conn)
    return {"cleared_events": int(removed), "portfolio_version": version, "db_path": str(path)}


def format_snapshot(snapshot: PortfolioSnapshot) -> str:
    """Render a snapshot as compact Markdown for CLI/Streamlit display."""
    lines = [
        "# NASDX 组合快照",
        "",
        f"- 生成时间：{snapshot.generated_at}",
        f"- 组合版本：{snapshot.portfolio_version}",
        f"- 快照哈希：{snapshot.snapshot_hash[:16]}",
        f"- 事件总数：{snapshot.event_count}（有效 {snapshot.active_event_count}）",
        f"- 现金：{'未知' if snapshot.cash is None else f'{snapshot.cash:,.2f} 元'}",
        f"- 持仓市值：{snapshot.total_market_value:,.2f} 元",
        f"- 总资产：{'未知' if snapshot.total_assets is None else f'{snapshot.total_assets:,.2f} 元'}",
        f"- 已实现盈亏：{snapshot.realized_pnl:,.2f} 元 · 浮动盈亏：{snapshot.unrealized_pnl:,.2f} 元",
        f"- 状态：{'FAIL-CLOSED（不可输出加仓动作）' if snapshot.fail_closed else '可用'}",
        "",
        "| 代码 | 名称 | 数量 | 成本 | 现价 | 市值 | 浮盈% | 权重% | 估值 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in snapshot.positions:
        lines.append(
            "| {code} | {name} | {qty:g} | {cost:.4f} | {price} | {value} | {pct} | {weight} | {status} |".format(
                code=row["code"],
                name=row["name"] or "-",
                qty=row["quantity"],
                cost=row["avg_cost"],
                price="-" if row["last_price"] is None else f"{row['last_price']:.4f}",
                value="-" if row["market_value"] is None else f"{row['market_value']:,.2f}",
                pct="-" if row["unrealized_pct"] is None else f"{row['unrealized_pct']:.2f}",
                weight="-" if row["weight_pct"] is None else f"{row['weight_pct']:.2f}",
                status=row["valuation_status"],
            )
        )
    if not snapshot.positions:
        lines.append("| - | 暂无持仓 | - | - | - | - | - | - | - |")
    if snapshot.blocking_reasons:
        lines += ["", "## 阻断原因", ""] + [f"- {item}" for item in snapshot.blocking_reasons]
    if snapshot.warnings:
        lines += ["", "## 警告", ""] + [f"- {item}" for item in snapshot.warnings]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nasdx.portfolio_store",
        description="NASDX 本地权威持仓账本（事件溯源，仅保存在本机）",
    )
    parser.add_argument("--db", dest="db_path", default=None, help="账本数据库路径（默认运行目录）")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="显示当前组合快照")
    show.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")

    add = sub.add_parser("add-trade", help="录入一笔成交")
    add.add_argument("--code", required=True)
    add.add_argument("--side", required=True, help="buy/sell/fee/dividend/adjustment 或中文买入/卖出")
    add.add_argument("--qty", default=0)
    add.add_argument("--price", default=0)
    add.add_argument("--at", dest="occurred_at", default=None, help="成交时间，默认现在")
    add.add_argument("--name", default="")
    add.add_argument("--fee", default=0)
    add.add_argument("--tax", default=0)
    add.add_argument("--note", default="")
    add.add_argument("--event-id", default=None, help="幂等键，重复提交不会重复记账")
    add.add_argument("--allow-odd-lot", action="store_true", help="跳过整数手校验（券商真实回单）")

    imp = sub.add_parser("import-csv", help="导入成交 CSV（重复导入自动去重）")
    imp.add_argument("path")

    fix = sub.add_parser("correct", help="修正一笔成交（保留审计链）")
    fix.add_argument("--event-id", required=True)
    fix.add_argument("--qty", default=None)
    fix.add_argument("--price", default=None)
    fix.add_argument("--fee", default=None)
    fix.add_argument("--tax", default=None)
    fix.add_argument("--at", dest="occurred_at", default=None)
    fix.add_argument("--code", default=None, help="改正证券代码（按新代码重新校验手数）")
    fix.add_argument("--side", default=None, help="改正买卖方向（按新方向重新校验手数）")
    fix.add_argument("--reason", default="")
    fix.add_argument("--void", action="store_true", help="作废该事件而不替换")
    fix.add_argument(
        "--allow-odd-lot",
        action="store_true",
        help="【高风险】跳过整数手校验，仅用于券商真实回单或历史迁移；违规项仍会在 lot_warnings 中列出",
    )

    cash = sub.add_parser("set-cash", help="设置现金基线")
    cash.add_argument("--amount", required=True)
    cash.add_argument("--at", dest="as_of", default=None)
    cash.add_argument("--note", default="")

    sub.add_parser("status", help="账本诊断信息")
    events = sub.add_parser("events", help="列出账本事件")
    events.add_argument("--all", action="store_true", help="包含已被修正的历史事件")

    export = sub.add_parser("export", help="导出完整账本 JSON")
    export.add_argument("--out", default=None, help="输出文件；缺省打印到标准输出")

    backup = sub.add_parser("backup", help="备份账本数据库")
    backup.add_argument("--out", required=True)

    clear = sub.add_parser("clear", help="清空账本（需要 --yes）")
    clear.add_argument("--yes", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    db_path = args.db_path
    try:
        if args.command == "show":
            snapshot = build_snapshot(db_path=db_path)
            print(
                json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2)
                if args.json
                else format_snapshot(snapshot)
            )
        elif args.command == "add-trade":
            result = add_trade(
                code=args.code,
                side=args.side,
                quantity=args.qty,
                price=args.price,
                occurred_at=args.occurred_at,
                name=args.name,
                fee=args.fee,
                tax=args.tax,
                note=args.note,
                event_id=args.event_id,
                db_path=db_path,
                enforce_lot_rules=not args.allow_odd_lot,
            )
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "import-csv":
            print(json.dumps(import_trades_csv(csv_path=args.path, db_path=db_path), ensure_ascii=False, indent=2))
        elif args.command == "correct":
            fields = {
                key: value
                for key, value in (
                    ("quantity", args.qty),
                    ("price", args.price),
                    ("fee", args.fee),
                    ("tax", args.tax),
                    ("occurred_at", args.occurred_at),
                    ("code", args.code),
                    ("side", args.side),
                )
                if value is not None
            }
            if args.void:
                fields = {}
            print(
                json.dumps(
                    correct_event(
                        args.event_id,
                        db_path=db_path,
                        reason=args.reason,
                        enforce_lot_rules=not args.allow_odd_lot,
                        **fields,
                    ),
                    ensure_ascii=False,
                )
            )
        elif args.command == "set-cash":
            print(
                json.dumps(
                    set_cash_baseline(args.amount, as_of=args.as_of, note=args.note, db_path=db_path),
                    ensure_ascii=False,
                )
            )
        elif args.command == "status":
            print(json.dumps(portfolio_status(db_path=db_path), ensure_ascii=False, indent=2))
        elif args.command == "events":
            print(
                json.dumps(
                    list_events(db_path=db_path, include_superseded=args.all),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "export":
            payload = json.dumps(export_events(db_path=db_path), ensure_ascii=False, indent=2)
            if args.out:
                Path(args.out).expanduser().write_text(payload, encoding="utf-8")
                print(f"已导出账本到 {args.out}")
            else:
                print(payload)
        elif args.command == "backup":
            print(f"已备份账本到 {backup_portfolio(args.out, db_path=db_path)}")
        elif args.command == "clear":
            print(json.dumps(clear_portfolio(confirm=args.yes, db_path=db_path), ensure_ascii=False))
    except (TradeEventError, PortfolioLedgerError) as exc:
        print(f"错误：{exc}")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI passthrough
    raise SystemExit(main())

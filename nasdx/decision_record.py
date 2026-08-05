"""
Immutable decision records for out-of-sample evaluation (#68).

Why this module exists
----------------------
``recommendation_review`` answers "does the *signal* still hold" and
``account_review`` answers "what did my *account* actually earn". Neither can
answer "was the advice any good", because nothing froze the advice at the
moment it was produced. Without a frozen reference price, data timestamp,
horizon and configuration version, every later evaluation silently reuses
today's price and becomes look-ahead biased.

Design contract
---------------
* A record is **frozen at generation time**. The table is insert-only: there is
  no UPDATE path. Re-submitting the identical record is a no-op (idempotent);
  re-submitting the same ``decision_id`` with different content raises.
* Forward labels live in a *separate* table (``decision_outcomes``) so that
  recomputing labels as new bars arrive can never mutate the frozen decision.
* ``data_as_of`` must not be after ``generated_at``. Labels are only allowed to
  read bars strictly after ``data_as_of`` (enforced in :mod:`nasdx.outcome_labels`).
* Every mode (``rules`` / ``full`` / ``intraday`` / ablation variants such as
  ``full-no_battle``) writes the *same* schema, so they are directly comparable.
* Storage is local only. Free-form text is redacted by
  :func:`nasdx.decision_log.sanitize_text` and truncated.
* Reads never create the database file; only writes do.

Environment
-----------
``NASDX_DECISION_DB``        override the SQLite path.
``NASDX_DECISION_RECORDS``   set to 0/false/no/off to disable persistence
                             (``record_decision`` still returns a valid frozen
                             record, nothing is written and no file is created).
``NASDX_DECISION_RECORDS_MAX`` retention cap, default 5000 (0 = unlimited).

CLI::

    python -m nasdx.decision_record status
    python -m nasdx.decision_record list --code 600519 --limit 20
    python -m nasdx.decision_record clear
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from nasdx.decision_log import sanitize_text
from nasdx.paths import get_runtime_dir

DECISION_RECORD_SCHEMA = "nasdx_decision_record.v1"
DECISION_OUTCOME_SCHEMA = "nasdx_decision_outcome.v1"

DECISION_DB_ENV = "NASDX_DECISION_DB"
DECISION_RECORDS_ENV = "NASDX_DECISION_RECORDS"
DECISION_RECORDS_MAX_ENV = "NASDX_DECISION_RECORDS_MAX"
DEFAULT_DB_NAME = "nasdx_decisions.db"
DEFAULT_MAX_RECORDS = 5000
BUSY_TIMEOUT_MS = 5_000
_TEXT_LIMIT = 240

_INIT_LOCK = threading.RLock()
_FALSEY = {"0", "false", "no", "off", ""}

# ---------------------------------------------------------------------------
# Evaluation classes: buy / hold / reduce / avoid are scored with *different*
# semantics (see nasdx.outcome_labels). "reduce" and "avoid" treat a fall as a
# favourable outcome, so they must never be pooled with "buy" win rates.
# ---------------------------------------------------------------------------
CLASS_BUY = "buy"
CLASS_HOLD = "hold"
CLASS_REDUCE = "reduce"
CLASS_AVOID = "avoid"
EVALUATION_CLASSES: Tuple[str, ...] = (CLASS_BUY, CLASS_HOLD, CLASS_REDUCE, CLASS_AVOID)

#: Directional sign used by label maths: +1 means "up is good".
CLASS_SIGN: Dict[str, int] = {
    CLASS_BUY: 1,
    CLASS_HOLD: 1,
    CLASS_REDUCE: -1,
    CLASS_AVOID: -1,
}

#: ``nasdx.intraday_decision.ACTIONS`` -> evaluation class.
_INTRADAY_ACTION_CLASS: Dict[str, str] = {
    "buy_first_lot": CLASS_BUY,
    "add": CLASS_BUY,
    "hold": CLASS_HOLD,
    "reduce": CLASS_REDUCE,
    "take_profit": CLASS_REDUCE,
    "exit": CLASS_REDUCE,
    "wait": CLASS_AVOID,
    "no_chase": CLASS_AVOID,
    "review_required": CLASS_AVOID,
}

#: ``nasdx.decision.build_decision_plan`` Chinese actions -> evaluation class.
_PLAN_ACTION_CLASS: Dict[str, str] = {
    "分批布局": CLASS_BUY,
    "轻仓试错": CLASS_BUY,
    "维持持仓，不加仓": CLASS_HOLD,
    "继续持有": CLASS_HOLD,
    "持有观察": CLASS_HOLD,
    "回避或减仓": CLASS_REDUCE,
    "减仓": CLASS_REDUCE,
    "清仓": CLASS_REDUCE,
    "观察等待": CLASS_AVOID,
    "暂停新增，先修复组合数据": CLASS_AVOID,
}

_MODE_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_-")


class DecisionRecordError(RuntimeError):
    """Raised when a decision record is invalid or would mutate frozen state."""


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DecisionRecord:
    """One frozen recommendation. Never mutated after :func:`record_decision`."""

    decision_id: str
    generated_at: str
    data_as_of: str
    code: str
    mode: str
    action: str
    evaluation_class: str
    reference_price: float
    horizon_trading_days: int
    schema: str = DECISION_RECORD_SCHEMA
    name: str = ""
    industry: str = ""
    confidence: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    stop_condition: str = ""
    target_condition: str = ""
    invalidation_condition: str = ""
    benchmark_code: str = ""
    portfolio_snapshot_hash: str = ""
    market_snapshot_hash: str = ""
    provider: str = ""
    model: str = ""
    prompt_schema_version: str = ""
    agent_config_version: str = ""
    llm_calls: int = 0
    latency_ms: float = 0.0
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["extra"] = dict(self.extra or {})
        return payload

    @property
    def content_hash(self) -> str:
        return _content_hash(self)

    @property
    def data_as_of_date(self) -> str:
        """``YYYY-MM-DD`` part of ``data_as_of`` (used for bar selection)."""
        return self.data_as_of[:10]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _require_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise DecisionRecordError(f"{label} 必须是字符串，收到 {value!r}")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise DecisionRecordError(f"{label} 不能为空")
    return cleaned


def _require_finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise DecisionRecordError(f"{label} 不能是布尔值，收到 {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise DecisionRecordError(f"{label} 必须是数值，收到 {value!r}") from None
    if not math.isfinite(number):
        raise DecisionRecordError(f"{label} 必须是有限数值，收到 {value!r}")
    return number


def _optional_pct(value: Any, label: str) -> Optional[float]:
    """Validate a percentage magnitude, e.g. 5.0 means 5%.

    The whole subsystem uses percent units (``return_pct``/``favorable_pct``/...),
    so stop/target distances use the same convention. Accepting ratios here would
    silently turn an intended ``0.05`` ("5%") into a 0.05% stop.
    """

    if value is None:
        return None
    number = _require_finite(value, label)
    if not 0.0 < number < 100.0:
        raise DecisionRecordError(
            f"{label} 是百分比数值，必须落在 (0, 100) 开区间（5.0 表示 5%），收到 {value!r}"
        )
    return number


def _parse_moment(value: Any, label: str) -> datetime:
    text = _require_text(value, label)
    raw = text.replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        try:
            moment = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            raise DecisionRecordError(f"{label} 不是合法时间: {value!r}") from None
    return moment


def _comparable(moment: datetime) -> datetime:
    """Drop tzinfo so naive and aware timestamps can be ordered consistently."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _clean_mode(value: Any) -> str:
    mode = _require_text(value, "mode").lower()
    if not set(mode) <= _MODE_ALLOWED:
        raise DecisionRecordError(f"mode 只允许小写字母/数字/下划线/连字符，收到 {value!r}")
    return mode


def classify_action(action: str, *, held: bool = False) -> str:
    """Map a plan or intraday action onto an evaluation class.

    ``held`` disambiguates "回避或减仓": reduce when the position exists,
    avoid when it does not.
    """
    text = (action or "").strip()
    if not text:
        raise DecisionRecordError("action 不能为空")
    lowered = text.lower()
    if lowered in _INTRADAY_ACTION_CLASS:
        return _INTRADAY_ACTION_CLASS[lowered]
    if lowered in EVALUATION_CLASSES:
        return lowered
    if text in _PLAN_ACTION_CLASS:
        mapped = _PLAN_ACTION_CLASS[text]
        if text == "回避或减仓" and not held:
            return CLASS_AVOID
        return mapped
    raise DecisionRecordError(f"无法识别的 action: {action!r}，请显式传入 evaluation_class")


def _sanitized(value: Any, limit: int = _TEXT_LIMIT) -> str:
    if value is None:
        return ""
    return sanitize_text(str(value), limit)


def _canonical_extra(extra: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not extra:
        return {}
    if not isinstance(extra, Mapping):
        raise DecisionRecordError("extra 必须是映射类型")
    cleaned: Dict[str, Any] = {}
    for key in sorted(str(k) for k in extra):
        value = extra[key] if key in extra else extra.get(key)
        if isinstance(value, str):
            cleaned[key] = _sanitized(value)
        elif isinstance(value, (int, float, bool)) or value is None:
            cleaned[key] = value
        else:
            cleaned[key] = _sanitized(json.dumps(value, ensure_ascii=False, default=str))
    return cleaned


def _content_hash(record: DecisionRecord) -> str:
    payload = record.to_dict()
    payload.pop("decision_id", None)
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_decision_record(
    *,
    code: str,
    mode: str,
    action: str,
    reference_price: float,
    data_as_of: str,
    generated_at: Optional[str] = None,
    horizon_trading_days: int = 20,
    evaluation_class: Optional[str] = None,
    held: bool = False,
    decision_id: Optional[str] = None,
    name: str = "",
    industry: str = "",
    confidence: Optional[float] = None,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
    stop_condition: str = "",
    target_condition: str = "",
    invalidation_condition: str = "",
    benchmark_code: str = "",
    portfolio_snapshot_hash: str = "",
    market_snapshot_hash: str = "",
    provider: str = "",
    model: str = "",
    prompt_schema_version: str = "",
    agent_config_version: str = "",
    llm_calls: int = 0,
    latency_ms: float = 0.0,
    extra: Optional[Mapping[str, Any]] = None,
) -> DecisionRecord:
    """Validate and freeze one recommendation. Pure: never touches disk."""
    code_text = _require_text(code, "code")
    mode_text = _clean_mode(mode)
    action_text = _require_text(action, "action")
    klass = evaluation_class or classify_action(action_text, held=held)
    if klass not in EVALUATION_CLASSES:
        raise DecisionRecordError(
            f"evaluation_class 必须属于 {EVALUATION_CLASSES}，收到 {evaluation_class!r}"
        )

    price = _require_finite(reference_price, "reference_price")
    if price <= 0:
        raise DecisionRecordError(f"reference_price 必须为正，收到 {reference_price!r}")

    if isinstance(horizon_trading_days, bool):
        raise DecisionRecordError("horizon_trading_days 不能是布尔值")
    if not isinstance(horizon_trading_days, int):
        raise DecisionRecordError(
            f"horizon_trading_days 必须是整数，收到 {horizon_trading_days!r}"
        )
    if horizon_trading_days <= 0:
        raise DecisionRecordError(
            f"horizon_trading_days 必须为正整数，收到 {horizon_trading_days!r}"
        )

    conf: Optional[float] = None
    if confidence is not None:
        conf = _require_finite(confidence, "confidence")
        if not 0.0 <= conf <= 1.0:
            raise DecisionRecordError(f"confidence 必须落在 [0, 1]，收到 {confidence!r}")

    data_moment = _parse_moment(data_as_of, "data_as_of")
    generated_text = generated_at or datetime.now().isoformat(timespec="seconds")
    generated_moment = _parse_moment(generated_text, "generated_at")
    if _comparable(data_moment) > _comparable(generated_moment):
        raise DecisionRecordError(
            "data_as_of 不能晚于 generated_at（决策不能使用未来数据）"
        )

    calls = 0 if llm_calls is None else int(_require_finite(llm_calls, "llm_calls"))
    if calls < 0:
        raise DecisionRecordError("llm_calls 不能为负")
    latency = _require_finite(latency_ms or 0.0, "latency_ms")
    if latency < 0:
        raise DecisionRecordError("latency_ms 不能为负")

    record = DecisionRecord(
        decision_id="",
        generated_at=generated_text.strip(),
        data_as_of=_require_text(data_as_of, "data_as_of"),
        code=code_text,
        mode=mode_text,
        action=action_text,
        evaluation_class=klass,
        reference_price=price,
        horizon_trading_days=horizon_trading_days,
        name=_sanitized(name, 60),
        industry=_sanitized(industry, 60),
        confidence=conf,
        stop_loss_pct=_optional_pct(stop_loss_pct, "stop_loss_pct"),
        take_profit_pct=_optional_pct(take_profit_pct, "take_profit_pct"),
        stop_condition=_sanitized(stop_condition),
        target_condition=_sanitized(target_condition),
        invalidation_condition=_sanitized(invalidation_condition),
        benchmark_code=_sanitized(benchmark_code, 20),
        portfolio_snapshot_hash=_sanitized(portfolio_snapshot_hash, 80),
        market_snapshot_hash=_sanitized(market_snapshot_hash, 80),
        provider=_sanitized(provider, 40),
        model=_sanitized(model, 80),
        prompt_schema_version=_sanitized(prompt_schema_version, 40),
        agent_config_version=_sanitized(agent_config_version, 40),
        llm_calls=calls,
        latency_ms=latency,
        extra=_canonical_extra(extra),
    )
    resolved_id = (decision_id or "").strip() or _content_hash(record)[:32]
    return DecisionRecord(**{**record.to_dict(), "decision_id": resolved_id})


# ---------------------------------------------------------------------------
# Adapters: every mode produces the same schema (#68 acceptance 5)
# ---------------------------------------------------------------------------
def record_from_decision_plan(
    plan: Mapping[str, Any],
    *,
    reference_price: float,
    data_as_of: str,
    mode: str = "full",
    held: bool = False,
    **overrides: Any,
) -> DecisionRecord:
    """Freeze a ``nasdx.decision.build_decision_plan`` result."""
    if not isinstance(plan, Mapping):
        raise DecisionRecordError("plan 必须是映射类型")
    exits = plan.get("exit_conditions") or []
    entries = plan.get("entry_conditions") or []
    triggers = plan.get("review_triggers") or []
    payload: Dict[str, Any] = dict(
        code=plan.get("stock_code", ""),
        name=plan.get("stock_name", ""),
        industry=plan.get("industry", ""),
        mode=mode,
        action=plan.get("action", ""),
        reference_price=reference_price,
        data_as_of=data_as_of,
        held=held,
        confidence=plan.get("confidence"),
        stop_condition="; ".join(str(x) for x in exits[:3]),
        target_condition="; ".join(str(x) for x in entries[:3]),
        invalidation_condition="; ".join(str(x) for x in triggers[:3]),
        portfolio_snapshot_hash=plan.get("portfolio_snapshot_hash", "") or "",
    )
    payload.update(overrides)
    return build_decision_record(**payload)


def record_from_intraday_decision(
    decision: Any,
    *,
    reference_price: Optional[float] = None,
    mode: str = "intraday",
    **overrides: Any,
) -> DecisionRecord:
    """Freeze a ``nasdx.intraday_decision.IntradayDecision``."""
    getter = decision.get if isinstance(decision, Mapping) else lambda k, d=None: getattr(decision, k, d)
    price = reference_price
    if price is None:
        position = getter("position", None)
        price = getattr(position, "last_price", None) if position is not None else None
    payload: Dict[str, Any] = dict(
        code=getter("code", ""),
        name=getter("name", "") or "",
        industry=getter("industry", "") or "",
        mode=mode,
        action=getter("action", ""),
        reference_price=price,
        data_as_of=getter("data_as_of", ""),
        generated_at=getter("generated_at", None),
        decision_id=getter("decision_id", None),
        confidence=getter("confidence", None),
        target_condition=getter("trigger", "") or "",
        invalidation_condition=getter("invalidation", "") or "",
        portfolio_snapshot_hash=getter("snapshot_hash", "") or "",
        llm_calls=0,
    )
    payload.update(overrides)
    return build_decision_record(**payload)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def persistence_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get(DECISION_RECORDS_ENV)
    if raw is None:
        return True
    return str(raw).strip().lower() not in _FALSEY


def decision_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the decision database path (param > env > runtime dir)."""
    if db_path:
        return Path(db_path).expanduser()
    configured = os.environ.get(DECISION_DB_ENV)
    if configured:
        return Path(configured).expanduser()
    return get_runtime_dir() / DEFAULT_DB_NAME


def _max_records() -> int:
    raw = os.environ.get(DECISION_RECORDS_MAX_ENV)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MAX_RECORDS
    try:
        value = int(str(raw).strip())
    except ValueError:
        return DEFAULT_MAX_RECORDS
    return max(0, value)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"pragma busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def init_decision_db(db_path: str | Path | None = None) -> Path:
    """Create the schema when missing and return the database path."""
    path = decision_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _INIT_LOCK, closing(_connect(path)) as conn:
        with conn:
            conn.execute(
                """
                create table if not exists decision_records (
                    seq integer primary key autoincrement,
                    decision_id text not null unique,
                    content_hash text not null,
                    code text not null,
                    mode text not null,
                    evaluation_class text not null,
                    generated_at text not null,
                    data_as_of text not null,
                    payload text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists decision_outcomes (
                    decision_id text primary key,
                    label_schema text not null,
                    computed_at text not null,
                    bars_used integer not null,
                    payload text not null
                )
                """
            )
            conn.execute(
                "create index if not exists idx_decision_records_code on decision_records(code)"
            )
            conn.execute(
                "create index if not exists idx_decision_records_mode on decision_records(mode)"
            )
            conn.execute("pragma journal_mode = wal")
    return path


def record_decision(
    record: DecisionRecord, *, db_path: str | Path | None = None
) -> DecisionRecord:
    """Append one frozen record. Idempotent; never mutates an existing row."""
    if not isinstance(record, DecisionRecord):
        raise DecisionRecordError("record 必须是 DecisionRecord 实例")
    if not persistence_enabled():
        return record

    path = init_decision_db(db_path)
    digest = record.content_hash
    with _INIT_LOCK, closing(_connect(path)) as conn:
        row = conn.execute(
            "select content_hash from decision_records where decision_id = ?",
            (record.decision_id,),
        ).fetchone()
        if row is not None:
            if row["content_hash"] != digest:
                raise DecisionRecordError(
                    f"decision_id={record.decision_id} 已存在且内容不同，决策记录不可变更"
                )
            return record
        with conn:
            conn.execute(
                """
                insert into decision_records
                    (decision_id, content_hash, code, mode, evaluation_class,
                     generated_at, data_as_of, payload)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.decision_id,
                    digest,
                    record.code,
                    record.mode,
                    record.evaluation_class,
                    record.generated_at,
                    record.data_as_of,
                    json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True),
                ),
            )
            cap = _max_records()
            if cap:
                conn.execute(
                    """
                    delete from decision_records
                     where seq not in (
                        select seq from decision_records order by seq desc limit ?
                     )
                    """,
                    (cap,),
                )
                conn.execute(
                    """
                    delete from decision_outcomes
                     where decision_id not in (select decision_id from decision_records)
                    """
                )
    return record


def _row_to_record(row: sqlite3.Row) -> DecisionRecord:
    payload = json.loads(row["payload"])
    payload.setdefault("schema", DECISION_RECORD_SCHEMA)
    known = set(DecisionRecord.__dataclass_fields__)
    return DecisionRecord(**{k: v for k, v in payload.items() if k in known})


def get_record(
    decision_id: str, *, db_path: str | Path | None = None
) -> Optional[DecisionRecord]:
    path = decision_db_path(db_path)
    if not path.exists():
        return None
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "select payload from decision_records where decision_id = ?", (decision_id,)
        ).fetchone()
    return _row_to_record(row) if row else None


def list_records(
    *,
    code: Optional[str] = None,
    mode: Optional[str] = None,
    since: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: str | Path | None = None,
) -> List[DecisionRecord]:
    """Return records ordered by insertion sequence (deterministic)."""
    path = decision_db_path(db_path)
    if not path.exists():
        return []
    clauses: List[str] = []
    params: List[Any] = []
    if code:
        clauses.append("code = ?")
        params.append(str(code).strip())
    if mode:
        clauses.append("mode = ?")
        params.append(str(mode).strip().lower())
    if since:
        clauses.append("data_as_of >= ?")
        params.append(str(since))
    where = (" where " + " and ".join(clauses)) if clauses else ""
    sql = f"select payload from decision_records{where} order by seq asc"
    if limit:
        sql += " limit ?"
        params.append(int(limit))
    with closing(_connect(path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_record(row) for row in rows]


def save_outcome(
    decision_id: str,
    labels: Mapping[str, Any],
    *,
    db_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Store or refresh forward labels. The frozen record is never touched."""
    if not persistence_enabled():
        return dict(labels)
    payload = dict(labels)
    path = init_decision_db(db_path)
    with _INIT_LOCK, closing(_connect(path)) as conn:
        exists = conn.execute(
            "select 1 from decision_records where decision_id = ?", (decision_id,)
        ).fetchone()
        if exists is None:
            raise DecisionRecordError(f"decision_id={decision_id} 不存在，无法写入结果标签")
        with conn:
            conn.execute(
                """
                insert into decision_outcomes
                    (decision_id, label_schema, computed_at, bars_used, payload)
                values (?, ?, ?, ?, ?)
                on conflict(decision_id) do update set
                    label_schema = excluded.label_schema,
                    computed_at  = excluded.computed_at,
                    bars_used    = excluded.bars_used,
                    payload      = excluded.payload
                """,
                (
                    decision_id,
                    str(payload.get("schema", DECISION_OUTCOME_SCHEMA)),
                    datetime.now().isoformat(timespec="seconds"),
                    int(payload.get("bars_available", 0) or 0),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                ),
            )
    return payload


def get_outcome(
    decision_id: str, *, db_path: str | Path | None = None
) -> Optional[Dict[str, Any]]:
    path = decision_db_path(db_path)
    if not path.exists():
        return None
    with closing(_connect(path)) as conn:
        row = conn.execute(
            "select payload from decision_outcomes where decision_id = ?", (decision_id,)
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def load_pairs(
    *,
    code: Optional[str] = None,
    mode: Optional[str] = None,
    since: Optional[str] = None,
    db_path: str | Path | None = None,
) -> List[Tuple[DecisionRecord, Dict[str, Any]]]:
    """Return ``(record, labels)`` pairs for every record that has labels."""
    pairs: List[Tuple[DecisionRecord, Dict[str, Any]]] = []
    for record in list_records(code=code, mode=mode, since=since, db_path=db_path):
        labels = get_outcome(record.decision_id, db_path=db_path)
        if labels:
            pairs.append((record, labels))
    return pairs


def decision_status(db_path: str | Path | None = None) -> Dict[str, Any]:
    path = decision_db_path(db_path)
    status: Dict[str, Any] = {
        "schema": DECISION_RECORD_SCHEMA,
        "db_path": str(path),
        "enabled": persistence_enabled(),
        "exists": path.exists(),
        "records": 0,
        "outcomes": 0,
        "max_records": _max_records(),
        "modes": {},
    }
    if not path.exists():
        return status
    with closing(_connect(path)) as conn:
        status["records"] = conn.execute("select count(*) from decision_records").fetchone()[0]
        status["outcomes"] = conn.execute("select count(*) from decision_outcomes").fetchone()[0]
        rows = conn.execute(
            "select mode, count(*) as n from decision_records group by mode order by mode"
        ).fetchall()
        status["modes"] = {row["mode"]: row["n"] for row in rows}
    return status


def clear_decisions(db_path: str | Path | None = None) -> Dict[str, Any]:
    path = decision_db_path(db_path)
    if not path.exists():
        return {"removed": False, "db_path": str(path)}
    with _INIT_LOCK, closing(_connect(path)) as conn:
        with conn:
            conn.execute("delete from decision_outcomes")
            conn.execute("delete from decision_records")
    return {"removed": True, "db_path": str(path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="NASDX 决策记录（#68）")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="显示记录数量与数据库路径")
    lister = sub.add_parser("list", help="列出决策记录")
    lister.add_argument("--code")
    lister.add_argument("--mode")
    lister.add_argument("--limit", type=int, default=20)
    sub.add_parser("clear", help="清空决策记录与结果标签")
    args = parser.parse_args(argv)

    command = args.command or "status"
    if command == "status":
        print(json.dumps(decision_status(), ensure_ascii=False, indent=2))
    elif command == "list":
        for record in list_records(code=args.code, mode=args.mode, limit=args.limit):
            print(
                f"{record.data_as_of[:10]}  {record.code:<8} {record.mode:<16} "
                f"{record.evaluation_class:<6} {record.action:<12} ref={record.reference_price}"
            )
    elif command == "clear":
        print(json.dumps(clear_decisions(), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(_cli())

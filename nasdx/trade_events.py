"""
Trade event contract for the NASDX authoritative portfolio ledger (#66).

This module owns the *shape* of a trade event: normalization, validation,
idempotency keys, lot-size rules and CSV parsing. It deliberately holds no
storage logic — ``nasdx.portfolio_store`` persists these events and derives
portfolio snapshots from them.

Event side semantics
--------------------
``buy``        quantity>0, price>0 — position increases; cash out = qty*price + fee + tax.
``sell``       quantity>0, price>0 — position decreases; cash in = qty*price - fee - tax.
``fee``        quantity==0, price==0 — account level cost; cash out = fee + tax.
``dividend``   quantity==0, price>0 — ``price`` carries the *cash amount* of the payout;
               cash in = price - fee - tax.
``adjustment`` quantity may be negative (bonus/split share correction) and ``price``
               carries a signed cash adjustment; cash delta = price - fee - tax.

Privacy: events never carry broker accounts, credentials or API keys. Free-form
``note``/``name`` text is redacted through ``nasdx.decision_log.sanitize_text``
before it is stored.
"""
from __future__ import annotations

import csv
import hashlib
import math
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from io import StringIO
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from nasdx.decision_log import sanitize_text


SIDES: Tuple[str, ...] = ("buy", "sell", "fee", "dividend", "adjustment")
POSITION_SIDES: Tuple[str, ...] = ("buy", "sell", "adjustment")
CASH_ONLY_SIDES: Tuple[str, ...] = ("fee", "dividend")

_BUY_WORDS = {"buy", "b", "long", "买", "买入", "证券买入", "买进", "申购"}
_SELL_WORDS = {"sell", "s", "short", "卖", "卖出", "证券卖出", "卖掉", "赎回"}
_FEE_WORDS = {"fee", "commission", "手续费", "佣金", "费用"}
_DIVIDEND_WORDS = {"dividend", "分红", "红利", "派息"}
_ADJUSTMENT_WORDS = {"adjustment", "adjust", "correction", "调整", "修正", "送股", "转股"}

_CODE_RE = re.compile(r"(\d{6})")
_NOTE_LIMIT = 200
_NAME_LIMIT = 40
_QUANTITY_EPS = 1e-9

COLUMN_ALIASES: Dict[str, List[str]] = {
    "occurred_at": ["occurred_at", "datetime", "date", "trade_date", "time", "日期", "交易日期", "成交日期", "成交时间"],
    "code": ["code", "symbol", "ticker", "证券代码", "股票代码", "代码"],
    "name": ["name", "security_name", "证券名称", "股票名称", "名称"],
    "side": ["side", "action", "direction", "buy_sell", "买卖", "方向", "操作", "交易方向"],
    "quantity": ["quantity", "qty", "shares", "volume", "成交数量", "数量", "股数", "份额"],
    "price": ["price", "trade_price", "成交价", "成交价格", "价格", "成交均价"],
    "fee": ["fee", "commission", "fees", "手续费", "佣金", "费用"],
    "tax": ["tax", "stamp_tax", "印花税", "税费"],
    "event_id": ["event_id", "id", "成交编号", "委托编号", "流水号"],
    "note": ["note", "memo", "remark", "备注", "说明"],
}

REQUIRED_COLUMNS = ("occurred_at", "code", "side", "quantity", "price")


class TradeEventError(ValueError):
    """Raised when a trade event violates the ledger contract."""


class LotSizeError(TradeEventError):
    """Raised when a quantity breaks the configured lot-size rule."""


@dataclass(frozen=True)
class LotRule:
    """Board-specific quantity rule.

    ``min_buy_quantity`` is the smallest buyable size, ``lot_size`` the required
    increment above it. ``allow_odd_sell`` permits selling an odd remainder
    (produced by dividends/splits) when it closes the whole position.
    """

    lot_size: int = 100
    min_buy_quantity: int = 100
    allow_odd_sell: bool = True
    label: str = "A股普通股票"


DEFAULT_LOT_RULE = LotRule()
STAR_LOT_RULE = LotRule(lot_size=1, min_buy_quantity=200, label="科创板")
BSE_LOT_RULE = LotRule(lot_size=1, min_buy_quantity=100, label="北交所")
FUND_LOT_RULE = LotRule(lot_size=100, min_buy_quantity=100, label="ETF/LOF")

_ETF_PREFIXES = ("15", "16", "18", "50", "51", "52", "53", "56", "58", "588")
_STAR_PREFIXES = ("688", "689")
_BSE_PREFIXES = ("4", "8", "920")

_LOT_RULE_OVERRIDES: Dict[str, LotRule] = {}


def set_lot_rule_overrides(overrides: Mapping[str, LotRule] | None) -> None:
    """Replace the process-wide lot-rule overrides (code or code prefix keyed)."""
    _LOT_RULE_OVERRIDES.clear()
    for key, rule in (overrides or {}).items():
        if not isinstance(rule, LotRule):
            raise TradeEventError(f"lot rule override for {key!r} must be a LotRule, got {type(rule).__name__}")
        _LOT_RULE_OVERRIDES[str(key).strip()] = rule


def get_lot_rule_overrides() -> Dict[str, LotRule]:
    """Return a copy of the active lot-rule overrides."""
    return dict(_LOT_RULE_OVERRIDES)


def classify_asset_class(code: str) -> str:
    """Classify a 6-digit code as ``ETF`` or ``股票`` (fallback ``其他``)."""
    normalized = normalize_code(code)
    if not normalized:
        return "其他"
    if normalized.startswith(_STAR_PREFIXES):
        return "股票"
    if normalized.startswith(_ETF_PREFIXES):
        return "ETF"
    return "股票"


def resolve_lot_rule(code: str, overrides: Mapping[str, LotRule] | None = None) -> LotRule:
    """Resolve the lot rule for a code, honoring exact-code then prefix overrides."""
    normalized = normalize_code(code)
    table: Dict[str, LotRule] = dict(_LOT_RULE_OVERRIDES)
    table.update({str(key).strip(): value for key, value in (overrides or {}).items()})
    if normalized in table:
        return table[normalized]
    for key in sorted(table, key=len, reverse=True):
        if key and normalized.startswith(key):
            return table[key]
    if not normalized:
        return DEFAULT_LOT_RULE
    if normalized.startswith(_STAR_PREFIXES):
        return STAR_LOT_RULE
    if normalized.startswith(_ETF_PREFIXES):
        return FUND_LOT_RULE
    if normalized.startswith(_BSE_PREFIXES):
        return BSE_LOT_RULE
    return DEFAULT_LOT_RULE


def check_lot_size(
    code: str,
    side: str,
    quantity: float,
    held_quantity: float | None = None,
    overrides: Mapping[str, LotRule] | None = None,
) -> List[str]:
    """Return lot-rule violations for a quantity (empty list when compliant)."""
    normalized_side = normalize_side(side)
    if normalized_side not in ("buy", "sell"):
        return []
    rule = resolve_lot_rule(code, overrides)
    qty = float(quantity)
    problems: List[str] = []
    if abs(qty - round(qty)) > _QUANTITY_EPS:
        problems.append(f"{code}: 数量 {qty:g} 不是整数股，{rule.label} 不支持零碎股数。")
        return problems
    qty_int = int(round(qty))
    if normalized_side == "buy":
        if qty_int < rule.min_buy_quantity:
            problems.append(
                f"{code}: 买入数量 {qty_int} 低于 {rule.label} 最小买入 {rule.min_buy_quantity} 股。"
            )
        elif rule.lot_size > 1 and qty_int % rule.lot_size:
            problems.append(
                f"{code}: 买入数量 {qty_int} 不是 {rule.label} 整数手（{rule.lot_size} 股）的倍数。"
            )
        return problems
    closes_position = (
        held_quantity is not None and abs(float(held_quantity) - qty_int) <= _QUANTITY_EPS
    )
    if rule.lot_size > 1 and qty_int % rule.lot_size:
        if not (rule.allow_odd_sell and closes_position):
            problems.append(
                f"{code}: 卖出数量 {qty_int} 不是 {rule.label} 整数手（{rule.lot_size} 股）的倍数，"
                "且不是清空零碎持仓。"
            )
    return problems


@dataclass(frozen=True)
class TradeEvent:
    """A single immutable ledger fact."""

    event_id: str
    occurred_at: str
    code: str
    side: str
    quantity: float
    price: float
    name: str = ""
    fee: float = 0.0
    tax: float = 0.0
    source: str = "manual"
    note: str = ""
    corrects: str = ""
    extras: Dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def cash_delta(self) -> float:
        """Signed cash impact of this event."""
        charges = self.fee + self.tax
        if self.side == "buy":
            return -(self.quantity * self.price + charges)
        if self.side == "sell":
            return self.quantity * self.price - charges
        if self.side == "fee":
            return -charges
        if self.side == "dividend":
            return self.price - charges
        return self.price - charges  # adjustment

    def quantity_delta(self) -> float:
        """Signed position impact of this event."""
        if self.side == "buy":
            return self.quantity
        if self.side == "sell":
            return -self.quantity
        if self.side == "adjustment":
            return self.quantity
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "code": self.code,
            "name": self.name,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "fee": self.fee,
            "tax": self.tax,
            "source": self.source,
            "note": self.note,
            "corrects": self.corrects,
        }


def normalize_code(code: Any) -> str:
    """Normalize ``sh600000``/``600000.SH``/``600000`` into ``600000``."""
    text = str(code or "").strip()
    if not text:
        return ""
    match = _CODE_RE.search(text)
    return match.group(1) if match else text


def normalize_side(side: Any) -> str:
    """Normalize Chinese/English side words into a canonical side."""
    text = str(side or "").strip().lower()
    if text in SIDES:
        return text
    if text in _BUY_WORDS:
        return "buy"
    if text in _SELL_WORDS:
        return "sell"
    if text in _FEE_WORDS:
        return "fee"
    if text in _DIVIDEND_WORDS:
        return "dividend"
    if text in _ADJUSTMENT_WORDS:
        return "adjustment"
    raise TradeEventError(f"未知交易方向：{side!r}；支持 {', '.join(SIDES)} 或中文买入/卖出/分红/手续费/调整。")


def normalize_timestamp(value: Any) -> str:
    """Normalize a date/datetime input into an ISO-8601 string (second precision)."""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    text = str(value or "").strip()
    if not text:
        raise TradeEventError("occurred_at 不能为空")
    candidate = text.replace("/", "-").replace("T", " ").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(candidate, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).isoformat(timespec="seconds")
    except ValueError as exc:
        raise TradeEventError(f"无法解析成交时间：{value!r}") from exc


def _finite_number(value: Any, label: str, allow_negative: bool = False) -> float:
    if isinstance(value, bool):
        raise TradeEventError(f"{label} 不能是布尔值：{value!r}")
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0.0
    try:
        number = float(str(value).replace(",", "").strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError) as exc:
        raise TradeEventError(f"{label} 必须是数字，收到 {value!r}") from exc
    if math.isnan(number) or math.isinf(number):
        raise TradeEventError(f"{label} 必须是有限数值，收到 {value!r}")
    if not allow_negative and number < 0:
        raise TradeEventError(f"{label} 不能为负数，收到 {number!r}")
    return number


def derive_event_id(
    occurred_at: str,
    code: str,
    side: str,
    quantity: float,
    price: float,
    fee: float,
    tax: float,
    source: str,
) -> str:
    """Derive a deterministic idempotency key for an economically identical fill."""
    payload = "|".join(
        [
            occurred_at,
            code,
            side,
            f"{float(quantity):.6f}",
            f"{float(price):.6f}",
            f"{float(fee):.6f}",
            f"{float(tax):.6f}",
            str(source or "manual"),
        ]
    )
    return "auto-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def new_event_id() -> str:
    """Return a random event id for manual entries that need no dedupe key."""
    return f"evt-{uuid.uuid4().hex[:24]}"


def build_trade_event(
    code: Any,
    side: Any,
    quantity: Any = 0,
    price: Any = 0,
    occurred_at: Any = None,
    name: Any = "",
    fee: Any = 0,
    tax: Any = 0,
    source: Any = "manual",
    note: Any = "",
    event_id: Any = None,
    corrects: Any = "",
) -> TradeEvent:
    """Validate raw inputs and build a normalized :class:`TradeEvent`.

    Raises ``TradeEventError`` before any state change so callers fail fast.
    """
    normalized_side = normalize_side(side)
    stamp = normalize_timestamp(occurred_at if occurred_at is not None else datetime.now())
    normalized_code = normalize_code(code)
    allow_negative_qty = normalized_side == "adjustment"
    qty = _finite_number(quantity, "quantity", allow_negative=allow_negative_qty)
    px = _finite_number(price, "price", allow_negative=normalized_side == "adjustment")
    fee_value = _finite_number(fee, "fee")
    tax_value = _finite_number(tax, "tax")

    if normalized_side in ("buy", "sell"):
        if not normalized_code:
            raise TradeEventError("买入/卖出事件必须提供证券代码")
        if qty <= 0:
            raise TradeEventError(f"{normalized_side} 事件的 quantity 必须大于 0，收到 {qty!r}")
        if px <= 0:
            raise TradeEventError(f"{normalized_side} 事件的 price 必须大于 0，收到 {px!r}")
    elif normalized_side == "fee":
        if qty:
            raise TradeEventError("fee 事件的 quantity 必须为 0，费用金额放在 fee/tax 字段")
        if px:
            raise TradeEventError("fee 事件的 price 必须为 0，费用金额放在 fee/tax 字段")
        if fee_value + tax_value <= 0:
            raise TradeEventError("fee 事件必须提供大于 0 的 fee 或 tax")
    elif normalized_side == "dividend":
        if qty:
            raise TradeEventError("dividend 事件的 quantity 必须为 0，现金金额放在 price 字段")
        if px <= 0:
            raise TradeEventError("dividend 事件的 price 必须大于 0（本次派息现金总额）")
    else:  # adjustment
        if qty == 0 and px == 0 and fee_value + tax_value == 0:
            raise TradeEventError("adjustment 事件必须至少调整数量或现金")

    safe_name = sanitize_text(str(name or ""), limit=_NAME_LIMIT) if name else ""
    safe_note = sanitize_text(str(note or ""), limit=_NOTE_LIMIT) if note else ""
    source_text = sanitize_text(str(source or "manual"), limit=32) or "manual"

    resolved_id = str(event_id or "").strip() or derive_event_id(
        stamp, normalized_code, normalized_side, qty, px, fee_value, tax_value, source_text
    )
    return TradeEvent(
        event_id=resolved_id,
        occurred_at=stamp,
        code=normalized_code,
        side=normalized_side,
        quantity=qty,
        price=px,
        name=safe_name,
        fee=fee_value,
        tax=tax_value,
        source=source_text,
        note=safe_note,
        corrects=str(corrects or "").strip(),
    )


def _field_map(fieldnames: Sequence[str]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for raw in fieldnames:
        key = str(raw or "").strip().lower()
        for field_name, aliases in COLUMN_ALIASES.items():
            if field_name in lookup:
                continue
            if key in {alias.lower() for alias in aliases}:
                lookup[field_name] = raw
                break
    return lookup


def parse_trade_csv(csv_text: str, source: str = "csv_import") -> Tuple[List[TradeEvent], List[str]]:
    """Parse CSV text into normalized events plus per-row rejection messages.

    Rows without an explicit ``event_id`` receive a deterministic derived id so
    re-importing the same file is idempotent.
    """
    reader = csv.DictReader(StringIO(csv_text))
    if not reader.fieldnames:
        return [], ["未识别到 CSV 表头"]
    mapping = _field_map(reader.fieldnames)
    missing = [name for name in REQUIRED_COLUMNS if name not in mapping]
    if missing:
        hint = "；".join(f"{name}={','.join(COLUMN_ALIASES[name][:3])}" for name in missing)
        return [], [f"缺少必要列 {', '.join(missing)}；可用列名示例：{hint}"]

    events: List[TradeEvent] = []
    rejected: List[str] = []
    for line_no, row in enumerate(reader, start=2):
        try:
            events.append(
                build_trade_event(
                    code=row.get(mapping["code"]),
                    side=row.get(mapping["side"]),
                    quantity=row.get(mapping["quantity"]),
                    price=row.get(mapping["price"]),
                    occurred_at=row.get(mapping["occurred_at"]),
                    name=row.get(mapping["name"]) if "name" in mapping else "",
                    fee=row.get(mapping["fee"]) if "fee" in mapping else 0,
                    tax=row.get(mapping["tax"]) if "tax" in mapping else 0,
                    source=source,
                    note=row.get(mapping["note"]) if "note" in mapping else "",
                    event_id=row.get(mapping["event_id"]) if "event_id" in mapping else None,
                )
            )
        except TradeEventError as exc:
            rejected.append(f"第 {line_no} 行：{exc}")
    return events, rejected


def sort_events(events: Iterable[TradeEvent]) -> List[TradeEvent]:
    """Deterministically order events by time then event id (replay order)."""
    return sorted(events, key=lambda item: (item.occurred_at, item.event_id))


def with_event_id(event: TradeEvent, event_id: str) -> TradeEvent:
    """Return a copy of ``event`` carrying a different id."""
    return replace(event, event_id=str(event_id))

"""
Forward outcome labels for frozen decision records (#68).

What this answers
-----------------
"After the advice was given, what actually happened?" — T+1/3/5/10/20 returns,
maximum favourable / adverse excursion (MFE / MAE), path drawdown, which of the
stop / target conditions fired first, and the excess over a benchmark.

Correctness contract
--------------------
* **No look-ahead.** Only bars whose *date* is strictly after
  ``record.data_as_of`` are used. The bar of the decision day itself is never
  scored, even when the decision was produced after the close — the conservative
  choice, and it keeps intraday and end-of-day modes comparable.
* **Entry price is frozen.** ``record.reference_price`` is used as the entry; it
  is never re-derived from later data.
* **Trading-day alignment.** T+k is the k-th *available bar* after the decision.
  Weekends and holidays are absent from an OHLCV frame by construction, so no
  separate calendar is needed and no calendar drift can be introduced.
* **Special states are not silently scored as normal.** Suspended bars
  (missing close or non-positive volume) and limit-locked first bars mark the
  sample ``entry_executable=False`` / ``price_state`` so aggregation can exclude
  them. Missing future bars produce ``insufficient_data``, never ``0``.
* **Class-aware semantics.** ``buy``/``hold`` score upside as favourable;
  ``reduce``/``avoid`` score *downside* as favourable (the loss you avoided).
  Raw ``return_pct`` is always the plain price return so the two views stay
  auditable.
* **Deterministic.** Pure function of ``(record, bars, benchmark, policy)``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from nasdx.decision_record import (
    CLASS_SIGN,
    DECISION_OUTCOME_SCHEMA,
    DecisionRecord,
    save_outcome,
)

DEFAULT_HORIZONS: Tuple[int, ...] = (1, 3, 5, 10, 20)

STATE_NORMAL = "normal"
STATE_SUSPENDED = "suspended"
STATE_LIMIT_UP = "limit_up"
STATE_LIMIT_DOWN = "limit_down"

STATUS_OK = "ok"
STATUS_NO_PRICES = "no_price_data"
STATUS_INSUFFICIENT = "insufficient_data"


class OutcomeLabelError(RuntimeError):
    """Raised when price input cannot be interpreted."""


@dataclass(frozen=True)
class LabelPolicy:
    """Tunables for label computation. Frozen so runs stay reproducible."""

    horizons: Tuple[int, ...] = DEFAULT_HORIZONS
    #: Detection thresholds in percent (slightly inside the real board limit).
    main_board_limit_pct: float = 9.5
    growth_board_limit_pct: float = 19.5
    bse_limit_pct: float = 29.5
    #: When stop and target fall inside one bar, assume the adverse one first.
    same_bar_tie_break: str = "stop"
    #: Treat a bar with volume <= this value as suspended.
    min_volume: float = 0.0


@dataclass(frozen=True)
class Bar:
    date: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]

    @property
    def suspended(self) -> bool:
        return self.close is None or self.high is None or self.low is None


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------
def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _date_text(value: Any) -> str:
    text = str(value)
    if "T" in text:
        text = text.split("T", 1)[0]
    elif " " in text:
        text = text.split(" ", 1)[0]
    return text[:10]


def normalize_bars(prices: Any, *, policy: Optional[LabelPolicy] = None) -> List[Bar]:
    """Accept a pandas OHLCV frame or a sequence of mappings; return sorted bars.

    Rows are sorted by date and de-duplicated (last row of a duplicated date
    wins) so that repeated timestamps cannot produce double-counted horizons.
    """
    pol = policy or LabelPolicy()
    rows: List[Tuple[str, Mapping[str, Any]]] = []

    if prices is None:
        return []
    if hasattr(prices, "iterrows") and hasattr(prices, "columns"):  # pandas frame
        columns = {str(c).lower(): c for c in prices.columns}
        index = list(prices.index)
        for position in range(len(index)):
            raw = prices.iloc[position]
            payload = {key: raw[col] for key, col in columns.items()}
            date_value = payload.get("date") or payload.get("trade_date") or index[position]
            rows.append((_date_text(date_value), payload))
    elif isinstance(prices, Iterable):
        for item in prices:
            if not isinstance(item, Mapping):
                raise OutcomeLabelError("行情序列元素必须是映射类型")
            payload = {str(k).lower(): v for k, v in item.items()}
            if "date" not in payload:
                raise OutcomeLabelError("行情序列元素缺少 date 字段")
            rows.append((_date_text(payload["date"]), payload))
    else:
        raise OutcomeLabelError(f"无法解析的行情输入: {type(prices)!r}")

    merged: Dict[str, Bar] = {}
    for date_text, payload in rows:
        close = _to_float(payload.get("close"))
        volume = _to_float(payload.get("volume"))
        high = _to_float(payload.get("high"))
        low = _to_float(payload.get("low"))
        if close is not None and volume is not None and volume <= pol.min_volume:
            # Zero-turnover bar: exchange calendar day but not tradable.
            close = high = low = None
        if close is not None:
            if high is None:
                high = close
            if low is None:
                low = close
        merged[date_text] = Bar(
            date=date_text,
            open=_to_float(payload.get("open")),
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
    return [merged[key] for key in sorted(merged)]


def board_limit_pct(code: str, policy: Optional[LabelPolicy] = None) -> float:
    """Daily price-limit threshold used for limit-lock detection."""
    pol = policy or LabelPolicy()
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if digits.startswith(("300", "301", "688", "689")):
        return pol.growth_board_limit_pct
    if digits.startswith(("43", "83", "87", "88", "92")):
        return pol.bse_limit_pct
    return pol.main_board_limit_pct


def _price_state(
    bar: Bar, prev_close: Optional[float], code: str, policy: LabelPolicy
) -> str:
    if bar.suspended:
        return STATE_SUSPENDED
    if prev_close is None or prev_close <= 0 or bar.close is None:
        return STATE_NORMAL
    change_pct = (bar.close / prev_close - 1.0) * 100.0
    threshold = board_limit_pct(code, policy)
    if change_pct >= threshold:
        return STATE_LIMIT_UP
    if change_pct <= -threshold:
        return STATE_LIMIT_DOWN
    return STATE_NORMAL


def _empty_horizons(record: DecisionRecord, policy: LabelPolicy) -> Dict[str, Any]:
    """Placeholder horizon rows so the label schema stays stable when data is missing.

    Consumers index ``labels["horizons"]["T+5"]`` directly; returning an empty
    dict on the no-data paths would raise KeyError instead of surfacing the
    missing-data status.
    """

    out: Dict[str, Any] = {}
    for k in sorted({*policy.horizons, record.horizon_trading_days}):
        if k <= 0:
            continue
        out[f"T+{k}"] = {
            "trading_days": k,
            "status": STATUS_INSUFFICIENT,
            "date": None,
            "close": None,
            "return_pct": None,
            "class_return_pct": None,
            "benchmark_return_pct": None,
            "excess_pct": None,
            "price_state": None,
        }
    return out


# ---------------------------------------------------------------------------
# Label computation
# ---------------------------------------------------------------------------
def compute_forward_labels(
    record: DecisionRecord,
    prices: Any,
    *,
    benchmark: Any = None,
    policy: Optional[LabelPolicy] = None,
) -> Dict[str, Any]:
    """Compute frozen-entry forward labels for one decision record."""
    if not isinstance(record, DecisionRecord):
        raise OutcomeLabelError("record 必须是 DecisionRecord 实例")
    pol = policy or LabelPolicy()
    sign = CLASS_SIGN[record.evaluation_class]
    entry = float(record.reference_price)
    cutoff = record.data_as_of_date

    bars = normalize_bars(prices, policy=pol)
    forward = [bar for bar in bars if bar.date > cutoff]
    history = [bar for bar in bars if bar.date <= cutoff]
    prev_close = next(
        (bar.close for bar in reversed(history) if bar.close is not None), None
    )

    labels: Dict[str, Any] = {
        "schema": DECISION_OUTCOME_SCHEMA,
        "decision_id": record.decision_id,
        "code": record.code,
        "mode": record.mode,
        "evaluation_class": record.evaluation_class,
        "class_sign": sign,
        "entry_price": entry,
        "data_as_of": record.data_as_of,
        "first_forward_date": forward[0].date if forward else None,
        "bars_available": len(forward),
        "horizon_trading_days": record.horizon_trading_days,
        "horizons": {},
        "entry_state": STATE_SUSPENDED if not forward else STATE_NORMAL,
        "entry_executable": False,
        "status": STATUS_OK,
        "warnings": [],
    }

    if not bars:
        labels["status"] = STATUS_NO_PRICES
        labels["horizons"] = _empty_horizons(record, pol)
        labels["warnings"].append("无任何行情数据")
        return labels
    if not forward:
        labels["status"] = STATUS_INSUFFICIENT
        labels["horizons"] = _empty_horizons(record, pol)
        labels["warnings"].append(f"{cutoff} 之后没有可用交易日")
        return labels

    entry_state = _price_state(forward[0], prev_close, record.code, pol)
    labels["entry_state"] = entry_state
    blocked_up = entry_state == STATE_LIMIT_UP and sign > 0
    blocked_down = entry_state == STATE_LIMIT_DOWN and sign < 0
    labels["entry_executable"] = not (
        entry_state == STATE_SUSPENDED or blocked_up or blocked_down
    )
    if not labels["entry_executable"]:
        labels["warnings"].append(f"首个交易日不可成交: {entry_state}")

    window = forward[: max(1, record.horizon_trading_days)]

    # --- per-horizon returns -------------------------------------------------
    bench_bars = normalize_bars(benchmark, policy=pol) if benchmark is not None else []
    bench_by_date = {bar.date: bar.close for bar in bench_bars if bar.close is not None}
    bench_entry = _benchmark_entry(bench_bars, cutoff)

    running_prev = prev_close
    state_by_index: List[str] = []
    for bar in forward:
        state_by_index.append(_price_state(bar, running_prev, record.code, pol))
        if bar.close is not None:
            running_prev = bar.close

    horizons: Dict[str, Any] = {}
    for k in sorted({*pol.horizons, record.horizon_trading_days}):
        key = f"T+{k}"
        if k <= 0:
            continue
        if k > len(forward):
            horizons[key] = {
                "trading_days": k,
                "status": STATUS_INSUFFICIENT,
                "date": None,
                "close": None,
                "return_pct": None,
                "class_return_pct": None,
                "benchmark_return_pct": None,
                "excess_pct": None,
                "price_state": None,
            }
            continue
        bar = forward[k - 1]
        state = state_by_index[k - 1]
        close = bar.close
        status = STATUS_OK
        if close is None:
            close = _last_valid_close(forward, k - 1)
            if close is None:
                close = prev_close
            status = "stale_price"
        if close is None:
            horizons[key] = {
                "trading_days": k,
                "status": STATUS_INSUFFICIENT,
                "date": bar.date,
                "close": None,
                "return_pct": None,
                "class_return_pct": None,
                "benchmark_return_pct": None,
                "excess_pct": None,
                "price_state": state,
            }
            continue
        ret = (close / entry - 1.0) * 100.0
        bench_ret = None
        excess = None
        if bench_entry is not None:
            bench_close = bench_by_date.get(bar.date)
            if bench_close is None:
                bench_close = _nearest_prior(bench_by_date, bar.date)
            if bench_close is not None and bench_entry > 0:
                bench_ret = (bench_close / bench_entry - 1.0) * 100.0
                excess = sign * (ret - bench_ret)
        horizons[key] = {
            "trading_days": k,
            "status": status,
            "date": bar.date,
            "close": round(close, 6),
            "return_pct": round(ret, 6),
            "class_return_pct": round(sign * ret, 6),
            "benchmark_return_pct": None if bench_ret is None else round(bench_ret, 6),
            "excess_pct": None if excess is None else round(excess, 6),
            "price_state": state,
        }
    labels["horizons"] = horizons

    # --- excursions ----------------------------------------------------------
    highs = [bar.high for bar in window if bar.high is not None]
    lows = [bar.low for bar in window if bar.low is not None]
    if highs and lows:
        mfe = (max(highs) / entry - 1.0) * 100.0
        mae = (min(lows) / entry - 1.0) * 100.0
        labels["mfe_pct"] = round(mfe, 6)
        labels["mae_pct"] = round(mae, 6)
        labels["favorable_pct"] = round(mfe if sign > 0 else -mae, 6)
        labels["adverse_pct"] = round(mae if sign > 0 else -mfe, 6)
    else:
        labels["mfe_pct"] = None
        labels["mae_pct"] = None
        labels["favorable_pct"] = None
        labels["adverse_pct"] = None
        labels["warnings"].append("持有窗口内无有效高低价，MFE/MAE 不可计算")

    labels["max_drawdown_pct"] = _class_max_drawdown(window, entry, sign)
    labels["suspended_days"] = sum(1 for bar in window if bar.suspended)
    labels["window_bars"] = len(window)
    if len(forward) < record.horizon_trading_days:
        labels["status"] = STATUS_INSUFFICIENT
        labels["warnings"].append(
            f"仅有 {len(forward)} 个前瞻交易日，不足 {record.horizon_trading_days} 日期限"
        )

    # --- first trigger -------------------------------------------------------
    labels["first_trigger"] = _first_trigger(record, window, entry, sign, pol)
    return labels


def _benchmark_entry(bench_bars: Sequence[Bar], cutoff: str) -> Optional[float]:
    for bar in reversed([b for b in bench_bars if b.date <= cutoff]):
        if bar.close is not None:
            return bar.close
    return None


def _nearest_prior(closes: Mapping[str, float], date: str) -> Optional[float]:
    candidates = [d for d in closes if d <= date]
    if not candidates:
        return None
    return closes[max(candidates)]


def _last_valid_close(bars: Sequence[Bar], index: int) -> Optional[float]:
    for position in range(index, -1, -1):
        if bars[position].close is not None:
            return bars[position].close
    return None


def _class_max_drawdown(
    window: Sequence[Bar], entry: float, sign: int
) -> Optional[float]:
    path = [0.0]
    for bar in window:
        if bar.close is None:
            continue
        path.append(sign * (bar.close / entry - 1.0) * 100.0)
    if len(path) <= 1:
        return None
    peak = path[0]
    worst = 0.0
    for value in path:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return round(worst, 6)


def _first_trigger(
    record: DecisionRecord,
    window: Sequence[Bar],
    entry: float,
    sign: int,
    policy: LabelPolicy,
) -> Optional[Dict[str, Any]]:
    """Return the first stop/target hit, adverse-first inside a single bar."""
    # stop_loss_pct / take_profit_pct are percent magnitudes (5.0 == 5%).
    slp = None if record.stop_loss_pct is None else record.stop_loss_pct / 100.0
    tpp = None if record.take_profit_pct is None else record.take_profit_pct / 100.0
    if slp is None and tpp is None:
        return None
    stop_level = None if slp is None else entry * (1.0 - sign * slp)
    target_level = None if tpp is None else entry * (1.0 + sign * tpp)

    for offset, bar in enumerate(window, start=1):
        if bar.suspended or bar.high is None or bar.low is None:
            continue
        hit_stop = False
        hit_target = False
        if stop_level is not None:
            hit_stop = bar.low <= stop_level if sign > 0 else bar.high >= stop_level
        if target_level is not None:
            hit_target = bar.high >= target_level if sign > 0 else bar.low <= target_level
        if not hit_stop and not hit_target:
            continue
        if hit_stop and hit_target:
            kind = "stop" if policy.same_bar_tie_break == "stop" else "target"
            ambiguous = True
        else:
            kind = "stop" if hit_stop else "target"
            ambiguous = False
        return {
            "kind": kind,
            "trading_days": offset,
            "date": bar.date,
            "level": round(stop_level if kind == "stop" else target_level, 6),
            "same_bar_ambiguous": ambiguous,
        }
    return None


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------
def label_and_store(
    record: DecisionRecord,
    prices: Any,
    *,
    benchmark: Any = None,
    policy: Optional[LabelPolicy] = None,
    db_path: Any = None,
) -> Dict[str, Any]:
    """Compute labels and persist them next to (never inside) the frozen record."""
    labels = compute_forward_labels(record, prices, benchmark=benchmark, policy=policy)
    save_outcome(record.decision_id, labels, db_path=db_path)
    return labels


def compute_labels_batch(
    records: Sequence[DecisionRecord],
    price_map: Mapping[str, Any],
    *,
    benchmark: Any = None,
    policy: Optional[LabelPolicy] = None,
) -> List[Tuple[DecisionRecord, Dict[str, Any]]]:
    """Label many records deterministically (input order is preserved)."""
    pairs: List[Tuple[DecisionRecord, Dict[str, Any]]] = []
    for record in records:
        prices = price_map.get(record.code)
        labels = compute_forward_labels(
            record, prices, benchmark=benchmark, policy=policy
        )
        pairs.append((record, labels))
    return pairs


def with_horizons(policy: LabelPolicy, horizons: Iterable[int]) -> LabelPolicy:
    return replace(policy, horizons=tuple(sorted({int(h) for h in horizons if int(h) > 0})))

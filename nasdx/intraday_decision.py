# -*- coding: utf-8 -*-
"""盘中操作契约（Issue #67）：版本化 ``IntradayDecision`` schema + 确定性动作策略。

设计要点
--------
* **动作是枚举，不是自由文本。** UI、历史复盘、通知共用 :data:`ACTIONS`。
* **规则先行。** 动作由行情信号 + ``PortfolioSnapshot`` 闸门 + A 股手数规则
  确定性生成；LLM 只能在上层做解释或冲突消解，不得绕过这里的降级逻辑。
* **fail-closed。** 价格缺失、快照缺失、数据陈旧、新闻状态未知一律降级为
  ``wait`` / ``review_required``，绝不输出确定性买入。
* **可复现。** 同样的输入（含 ``now``）必须得到同样的 ``decision_id`` 与动作，
  便于测试与快照间对比。

本模块不读文件、不联网、不写盘，纯函数式，便于测试。
"""
from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from nasdx.evidence import CST, market_session, to_cst
from nasdx.trade_events import LotRule, check_lot_size, classify_asset_class, resolve_lot_rule

DECISION_SCHEMA = "nasdx_intraday_decision.v1"

# --- 稳定动作枚举 ---------------------------------------------------------
ACTION_HOLD = "hold"
ACTION_WAIT = "wait"
ACTION_BUY_FIRST_LOT = "buy_first_lot"
ACTION_ADD = "add"
ACTION_REDUCE = "reduce"
ACTION_TAKE_PROFIT = "take_profit"
ACTION_EXIT = "exit"
ACTION_NO_CHASE = "no_chase"
ACTION_REVIEW_REQUIRED = "review_required"

ACTIONS: Tuple[str, ...] = (
    ACTION_HOLD,
    ACTION_WAIT,
    ACTION_BUY_FIRST_LOT,
    ACTION_ADD,
    ACTION_REDUCE,
    ACTION_TAKE_PROFIT,
    ACTION_EXIT,
    ACTION_NO_CHASE,
    ACTION_REVIEW_REQUIRED,
)

ACTION_LABELS: Dict[str, str] = {
    ACTION_HOLD: "继续持有",
    ACTION_WAIT: "等待确认",
    ACTION_BUY_FIRST_LOT: "买入首仓",
    ACTION_ADD: "加仓",
    ACTION_REDUCE: "减仓",
    ACTION_TAKE_PROFIT: "止盈",
    ACTION_EXIT: "全部退出",
    ACTION_NO_CHASE: "禁止追涨",
    ACTION_REVIEW_REQUIRED: "人工复核",
}

RISK_INCREASING_ACTIONS = frozenset({ACTION_BUY_FIRST_LOT, ACTION_ADD})
RISK_REDUCING_ACTIONS = frozenset({ACTION_REDUCE, ACTION_TAKE_PROFIT, ACTION_EXIT})
DEGRADED_ACTIONS = frozenset({ACTION_WAIT, ACTION_REVIEW_REQUIRED})

#: 相对"看多程度"的稳定排序，用于快照间 upgrade / downgrade 判定。
STANCE_RANK: Dict[str, int] = {
    ACTION_EXIT: -4,
    ACTION_REDUCE: -3,
    ACTION_TAKE_PROFIT: -2,
    ACTION_NO_CHASE: -1,
    ACTION_REVIEW_REQUIRED: 0,
    ACTION_WAIT: 1,
    ACTION_HOLD: 2,
    ACTION_ADD: 3,
    ACTION_BUY_FIRST_LOT: 3,
}

#: 驾驶舱排序用的紧急度（越大越靠前）。
URGENCY_RANK: Dict[str, int] = {
    ACTION_EXIT: 90,
    ACTION_REVIEW_REQUIRED: 80,
    ACTION_TAKE_PROFIT: 70,
    ACTION_REDUCE: 65,
    ACTION_BUY_FIRST_LOT: 50,
    ACTION_ADD: 45,
    ACTION_NO_CHASE: 30,
    ACTION_WAIT: 20,
    ACTION_HOLD: 10,
}

# --- 快照差异分类 ---------------------------------------------------------
DIFF_NEW = "new"
DIFF_MAINTAIN = "maintain"
DIFF_UPGRADE = "upgrade"
DIFF_DOWNGRADE = "downgrade"
DIFF_EXPIRED = "expired"
DIFF_KINDS: Tuple[str, ...] = (DIFF_NEW, DIFF_MAINTAIN, DIFF_UPGRADE, DIFF_DOWNGRADE, DIFF_EXPIRED)

DIFF_LABELS: Dict[str, str] = {
    DIFF_NEW: "新增",
    DIFF_MAINTAIN: "维持",
    DIFF_UPGRADE: "升级",
    DIFF_DOWNGRADE: "降级",
    DIFF_EXPIRED: "失效",
}

# --- 交易时段与半小时检查点 ------------------------------------------------
PHASE_CONTINUOUS = "continuous_auction"
PHASE_LUNCH = "lunch_break"
PHASE_PRE_MARKET = "pre_market"
PHASE_POST_MARKET = "post_market"
PHASE_NON_TRADING = "non_trading"
PHASE_UNKNOWN = "unknown"

_MORNING_OPEN = dt_time(9, 30)
_MORNING_CLOSE = dt_time(11, 30)
_AFTERNOON_OPEN = dt_time(13, 0)
_AFTERNOON_CLOSE = dt_time(15, 0)

#: A 股连续竞价内的默认半小时检查点。
CHECKPOINTS: Tuple[str, ...] = (
    "09:59",
    "10:29",
    "10:59",
    "11:29",
    "13:29",
    "13:59",
    "14:29",
    "14:59",
)

CHECKPOINT_ENV = "NASDX_INTRADAY_CHECKPOINTS"
HOLIDAY_ENV = "NASDX_MARKET_HOLIDAYS"

_EPS = 1e-9

# 证据被视为"已核验"的状态；其余（含缺失）都算未知。
_VERIFIED_EVIDENCE_STATES = frozenset({"verified", "official", "ok", "fresh"})

SIGNAL_BULLISH = "bullish"
SIGNAL_BEARISH = "bearish"
SIGNAL_NEUTRAL = "neutral"
SIGNAL_UNKNOWN = "unknown"

_VALUATION_OK = frozenset({"", "ok", "valued", "priced", "normal"})

__all__ = [
    "DECISION_SCHEMA",
    "ACTIONS",
    "ACTION_LABELS",
    "ACTION_HOLD",
    "ACTION_WAIT",
    "ACTION_BUY_FIRST_LOT",
    "ACTION_ADD",
    "ACTION_REDUCE",
    "ACTION_TAKE_PROFIT",
    "ACTION_EXIT",
    "ACTION_NO_CHASE",
    "ACTION_REVIEW_REQUIRED",
    "RISK_INCREASING_ACTIONS",
    "RISK_REDUCING_ACTIONS",
    "DEGRADED_ACTIONS",
    "STANCE_RANK",
    "URGENCY_RANK",
    "DIFF_KINDS",
    "DIFF_LABELS",
    "CHECKPOINTS",
    "IntradayPolicy",
    "DEFAULT_POLICY",
    "PositionView",
    "IntradayDecision",
    "decide",
    "diff_decisions",
    "trading_phase",
    "checkpoint_times",
    "current_checkpoint",
    "next_checkpoint",
    "should_run",
    "is_trading_day",
]


# --------------------------------------------------------------------------
# 策略参数
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class IntradayPolicy:
    """盘中动作阈值。全部显式化，避免规则藏在函数体里。"""

    stale_seconds: float = 900.0
    """行情快照超过该秒数：风险增加类动作降级为 ``wait``。"""

    hard_stale_seconds: float = 3600.0
    """行情快照超过该秒数：整条决策降级为 ``review_required``。"""

    stop_loss_pct: float = -8.0
    """浮亏达到该百分比且信号转空：清仓。"""

    take_profit_pct: float = 20.0
    """浮盈达到该百分比且信号非多头：止盈减半。"""

    trim_ratio: float = 0.5
    """减仓/止盈的默认减仓比例。"""

    min_confidence: float = 0.55
    """低于该置信度不生成风险增加类动作。"""

    valid_minutes: int = 30
    """没有下一个检查点时的动作有效期（分钟）。"""


DEFAULT_POLICY = IntradayPolicy()


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PositionView:
    """一条决策所依赖的持仓视图；候选股用 ``quantity=0`` 表示未持有。"""

    code: str
    name: str = ""
    asset_class: str = ""
    industry: str = ""
    quantity: float = 0.0
    cost: Optional[float] = None
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    weight_pct: Optional[float] = None
    valuation_status: str = ""

    @property
    def held(self) -> bool:
        return _finite(self.quantity, 0.0) > _EPS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quantity": _round(self.quantity, 4),
            "cost": _round(self.cost, 4),
            "current_price": _round(self.current_price, 4),
            "market_value": _round(self.market_value, 2),
            "unrealized_pnl": _round(self.unrealized_pnl, 2),
            "unrealized_pnl_pct": _round(self.unrealized_pnl_pct, 2),
            "weight_pct": _round(self.weight_pct, 2),
            "valuation_status": self.valuation_status,
        }


@dataclass(frozen=True)
class IntradayDecision:
    """单标的盘中决策。动作字段取自 :data:`ACTIONS`，不是自由文本。"""

    decision_id: str
    generated_at: str
    data_as_of: str
    code: str
    action: str
    position: PositionView
    name: str = ""
    asset_class: str = ""
    industry: str = ""
    schema: str = DECISION_SCHEMA
    portfolio_version: int = 0
    snapshot_hash: str = ""
    quantity_delta: float = 0.0
    amount_delta: float = 0.0
    executable: bool = True
    trigger: str = ""
    invalidation: str = ""
    valid_until: str = ""
    confidence: float = 0.0
    risk_level: str = "medium"
    signal: str = SIGNAL_UNKNOWN
    session: str = PHASE_UNKNOWN
    degraded: bool = False
    reasons: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    evidence_as_of: Mapping[str, Any] = field(default_factory=dict)

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "decision_id": self.decision_id,
            "generated_at": self.generated_at,
            "data_as_of": self.data_as_of,
            "portfolio_version": self.portfolio_version,
            "snapshot_hash": self.snapshot_hash,
            "code": self.code,
            "name": self.name,
            "asset_class": self.asset_class,
            "industry": self.industry,
            "position": self.position.to_dict(),
            "action": self.action,
            "action_label": self.action_label,
            "quantity_delta": _round(self.quantity_delta, 4),
            "amount_delta": _round(self.amount_delta, 2),
            "executable": bool(self.executable),
            "trigger": self.trigger,
            "invalidation": self.invalidation,
            "valid_until": self.valid_until,
            "confidence": _round(self.confidence, 4),
            "risk_level": self.risk_level,
            "signal": self.signal,
            "session": self.session,
            "degraded": bool(self.degraded),
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "evidence_as_of": dict(self.evidence_as_of),
        }


# --------------------------------------------------------------------------
# 时段 / 检查点
# --------------------------------------------------------------------------
def _parse_holidays(
    holidays: Iterable[str] | None = None, environ: Mapping[str, str] | None = None
) -> frozenset:
    values: List[str] = []
    if holidays:
        values.extend(str(item).strip() for item in holidays)
    env = os.environ if environ is None else environ
    raw = str(env.get(HOLIDAY_ENV, "") or "")
    if raw:
        values.extend(part.strip() for part in raw.replace(";", ",").split(","))
    return frozenset(item for item in values if item)


def is_trading_day(
    moment: Any,
    holidays: Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """周一至周五且不在节假日清单里视为交易日。"""
    stamp = to_cst(moment)
    if stamp is None:
        return False
    if stamp.weekday() >= 5:
        return False
    return stamp.date().isoformat() not in _parse_holidays(holidays, environ)


def trading_phase(
    moment: Any,
    holidays: Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """在 :func:`nasdx.evidence.market_session` 之上补出午休与节假日。"""
    stamp = to_cst(moment)
    if stamp is None:
        return PHASE_UNKNOWN
    if not is_trading_day(stamp, holidays, environ):
        return PHASE_NON_TRADING
    base = market_session(stamp)
    if base in ("non_trading", "unknown"):
        return PHASE_NON_TRADING if base == "non_trading" else PHASE_UNKNOWN
    if base == "pre_market":
        return PHASE_PRE_MARKET
    if base == "post_market":
        return PHASE_POST_MARKET
    clock = stamp.timetz().replace(tzinfo=None)
    if _MORNING_CLOSE <= clock < _AFTERNOON_OPEN:
        return PHASE_LUNCH
    if _MORNING_OPEN <= clock < _MORNING_CLOSE or _AFTERNOON_OPEN <= clock < _AFTERNOON_CLOSE:
        return PHASE_CONTINUOUS
    return PHASE_POST_MARKET


def checkpoint_times(environ: Mapping[str, str] | None = None) -> Tuple[dt_time, ...]:
    """返回配置的检查点；``NASDX_INTRADAY_CHECKPOINTS`` 可覆盖，非法值回退默认。"""
    env = os.environ if environ is None else environ
    raw = str(env.get(CHECKPOINT_ENV, "") or "").strip()
    source: Sequence[str] = CHECKPOINTS
    if raw:
        parsed = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
        if parsed:
            source = parsed
    result: List[dt_time] = []
    for item in source:
        try:
            hour, minute = str(item).split(":")[:2]
            result.append(dt_time(int(hour), int(minute)))
        except (TypeError, ValueError):
            continue
    if not result:
        result = [dt_time(int(v[:2]), int(v[3:5])) for v in CHECKPOINTS]
    return tuple(sorted(set(result)))


def current_checkpoint(
    moment: Any,
    tolerance_seconds: float = 90.0,
    environ: Mapping[str, str] | None = None,
) -> Optional[str]:
    """若 ``moment`` 落在某个检查点的容差窗口内，返回 ``HH:MM``，否则 ``None``。"""
    stamp = to_cst(moment)
    if stamp is None:
        return None
    for slot in checkpoint_times(environ):
        anchor = stamp.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
        if abs((stamp - anchor).total_seconds()) <= max(tolerance_seconds, 0.0):
            return f"{slot.hour:02d}:{slot.minute:02d}"
    return None


def next_checkpoint(
    moment: Any, environ: Mapping[str, str] | None = None
) -> Optional[datetime]:
    """返回当日下一个检查点时间；当日已无检查点返回 ``None``。"""
    stamp = to_cst(moment)
    if stamp is None:
        return None
    for slot in checkpoint_times(environ):
        anchor = stamp.replace(hour=slot.hour, minute=slot.minute, second=0, microsecond=0)
        if anchor > stamp:
            return anchor
    return None


def should_run(
    moment: Any,
    tolerance_seconds: float = 90.0,
    holidays: Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Tuple[bool, str]:
    """调度闸门：仅在交易日的连续竞价检查点窗口内返回 ``True``。

    只负责"要不要生成快照"，不下任何单。
    """
    stamp = to_cst(moment)
    if stamp is None:
        return False, "时间无法解析，跳过本轮快照。"
    if not is_trading_day(stamp, holidays, environ):
        return False, "非交易日（周末或节假日），跳过盘中快照。"
    phase = trading_phase(stamp, holidays, environ)
    if phase != PHASE_CONTINUOUS:
        return False, f"当前处于 {phase}，不在连续竞价时段，跳过盘中快照。"
    slot = current_checkpoint(stamp, tolerance_seconds, environ)
    if not slot:
        return False, "不在配置的半小时检查点窗口内，跳过盘中快照。"
    return True, f"命中检查点 {slot}。"


# --------------------------------------------------------------------------
# 数值工具
# --------------------------------------------------------------------------
def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _round(value: Any, digits: int) -> Optional[float]:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    return round(parsed, digits)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _lots_for_budget(budget: float, price: float, rule: LotRule) -> float:
    """按预算和手数规则算出可买数量（向下取整到整手）。"""
    if budget <= 0 or price <= 0:
        return 0.0
    lot = max(int(rule.lot_size or 1), 1)
    raw = budget / price
    quantity = math.floor(raw / lot) * lot
    return float(max(quantity, 0))


def _trim_quantity(held: float, ratio: float, rule: LotRule) -> float:
    """减仓数量：向下取整到整手；不足一手时返回 0（由调用方决定是否清仓）。"""
    lot = max(int(rule.lot_size or 1), 1)
    target = held * max(min(ratio, 1.0), 0.0)
    quantity = math.floor(target / lot) * lot
    return float(max(min(quantity, held), 0))


# --------------------------------------------------------------------------
# 触发 / 失效条件
# --------------------------------------------------------------------------
def _levels(levels: Mapping[str, Any] | None, price: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    data = levels or {}
    support = _optional_float(data.get("support"))
    resistance = _optional_float(data.get("resistance"))
    if support is not None and price is not None and support >= price:
        support = None
    if resistance is not None and price is not None and resistance <= price:
        resistance = None
    return support, resistance


def _condition_text(
    action: str,
    price: Optional[float],
    support: Optional[float],
    resistance: Optional[float],
) -> Tuple[str, str]:
    """每个动作都必须有触发条件和失效条件，缺价位时退化为文字描述。"""
    sup = f"{support:.2f}" if support is not None else "关键支撑"
    res = f"{resistance:.2f}" if resistance is not None else "关键压力"
    now = f"{price:.2f}" if price is not None else "现价"

    if action in (ACTION_BUY_FIRST_LOT, ACTION_ADD):
        return (
            f"回踩 {sup} 附近不破且 30 分钟分时收回后执行",
            f"跌破 {sup} 且 30 分钟未收回，或本轮有效期到期未成交",
        )
    if action == ACTION_HOLD:
        return (
            f"现价 {now} 未跌破 {sup}，维持持有不动",
            f"放量跌破 {sup}，或触发下一轮减仓条件",
        )
    if action in (ACTION_REDUCE, ACTION_TAKE_PROFIT):
        return (
            f"现价 {now} 已满足减仓规则，可在 {now} 附近分批卖出",
            f"重新放量站上 {res} 并守住，则本次减仓失效",
        )
    if action == ACTION_EXIT:
        return (
            f"现价 {now} 已触发清仓纪律，按市价分批清出",
            f"重新站上 {res} 并确认止跌，需要重新评估后才可再买",
        )
    if action == ACTION_NO_CHASE:
        return (
            f"现价 {now} 不追高，只在回踩 {sup} 且不破时重新评估",
            f"突破 {res} 后放量站稳且组合限制解除，则重新评估",
        )
    if action == ACTION_REVIEW_REQUIRED:
        return (
            "数据或估值异常，先人工复核行情与账本后再决定",
            "数据补齐且组合快照可用后，本条复核要求自动失效",
        )
    return (
        f"等待下一轮检查点确认，现价 {now} 暂不操作",
        "出现明确放量突破或跌破关键位时，本轮等待失效",
    )


# --------------------------------------------------------------------------
# 核心决策
# --------------------------------------------------------------------------
def _evidence_unknown(evidence: Mapping[str, Any] | None) -> List[str]:
    """返回状态未知/未核验的证据维度。"""
    data = evidence or {}
    missing: List[str] = []
    for dimension in ("market", "sector", "news"):
        item = data.get(dimension)
        if isinstance(item, Mapping):
            status = str(item.get("status") or "").strip().lower()
        else:
            status = str(item or "").strip().lower()
        if status not in _VERIFIED_EVIDENCE_STATES:
            missing.append(dimension)
    return missing


def _base_action(
    signal: str,
    held: bool,
    pnl_pct: Optional[float],
    confidence: float,
    policy: IntradayPolicy,
) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    normalized = str(signal or SIGNAL_UNKNOWN).strip().lower()
    if normalized not in (SIGNAL_BULLISH, SIGNAL_BEARISH, SIGNAL_NEUTRAL):
        normalized = SIGNAL_UNKNOWN

    if held:
        if normalized == SIGNAL_BEARISH and pnl_pct is not None and pnl_pct <= policy.stop_loss_pct:
            reasons.append(
                f"信号转空且浮亏 {pnl_pct:.2f}% 已触及止损线 {policy.stop_loss_pct:.2f}%。"
            )
            return ACTION_EXIT, reasons
        if normalized == SIGNAL_BEARISH:
            reasons.append("信号转空，先降低敞口而不是继续等待。")
            return ACTION_REDUCE, reasons
        if pnl_pct is not None and pnl_pct >= policy.take_profit_pct and normalized != SIGNAL_BULLISH:
            reasons.append(
                f"浮盈 {pnl_pct:.2f}% 已达止盈线 {policy.take_profit_pct:.2f}% 且信号未继续走强。"
            )
            return ACTION_TAKE_PROFIT, reasons
        if normalized == SIGNAL_BULLISH and confidence >= policy.min_confidence:
            reasons.append(f"多头信号且置信度 {confidence:.2f} 达标，可按组合上限加仓。")
            return ACTION_ADD, reasons
        if normalized == SIGNAL_BULLISH:
            reasons.append(f"多头信号但置信度 {confidence:.2f} 不足 {policy.min_confidence:.2f}，只持有不加仓。")
            return ACTION_HOLD, reasons
        reasons.append("信号中性或不明确，维持现有仓位。")
        return ACTION_HOLD, reasons

    if normalized == SIGNAL_BULLISH and confidence >= policy.min_confidence:
        reasons.append(f"多头信号且置信度 {confidence:.2f} 达标，可建首仓。")
        return ACTION_BUY_FIRST_LOT, reasons
    if normalized == SIGNAL_BEARISH:
        reasons.append("信号偏空，不建仓也不追涨。")
        return ACTION_NO_CHASE, reasons
    reasons.append("信号不足以支撑建仓，等待下一轮确认。")
    return ACTION_WAIT, reasons


def decide(
    *,
    position: PositionView,
    signal: str = SIGNAL_UNKNOWN,
    confidence: float = 0.0,
    gate: Any = None,
    data_as_of: Any = None,
    evidence: Mapping[str, Any] | None = None,
    now: Any = None,
    policy: IntradayPolicy = DEFAULT_POLICY,
    levels: Mapping[str, Any] | None = None,
    cash: Optional[float] = None,
    total_assets: Optional[float] = None,
    lot_overrides: Mapping[str, LotRule] | None = None,
    holidays: Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
    extra_reasons: Sequence[str] = (),
) -> IntradayDecision:
    """把行情信号 + 组合闸门 + 手数规则压成一条确定性 :class:`IntradayDecision`。"""
    moment = to_cst(now) or datetime.now(CST)
    phase = trading_phase(moment, holidays, environ)
    stamp = to_cst(data_as_of)
    age = None if stamp is None else (moment - stamp).total_seconds()

    code = str(position.code or "").strip()
    asset_class = position.asset_class or classify_asset_class(code)
    rule = resolve_lot_rule(code, lot_overrides)
    price = _optional_float(position.current_price)
    held_qty = _finite(position.quantity, 0.0)
    held = held_qty > _EPS
    pnl_pct = _optional_float(position.unrealized_pnl_pct)

    reasons: List[str] = [str(item) for item in extra_reasons if item]
    blockers: List[str] = []
    degraded = False

    gate_enabled = bool(gate is not None and getattr(gate, "status", "unknown") != "unknown")
    gate_fail_closed = bool(getattr(gate, "fail_closed", False))
    allow_add = bool(getattr(gate, "allow_add", False)) if gate_enabled else False
    allow_new_entry = bool(getattr(gate, "allow_new_entry", False)) if gate_enabled else False
    gate_context = getattr(gate, "context", None)
    gate_context = gate_context if isinstance(gate_context, Mapping) else {}
    if cash is None:
        cash = _optional_float(gate_context.get("cash"))
    if total_assets is None:
        total_assets = _optional_float(gate_context.get("total_assets"))
    headroom_pct = _optional_float(getattr(gate, "max_new_position_pct", None))

    missing_evidence = _evidence_unknown(evidence)
    price_ok = price is not None and price > 0
    valuation_ok = str(position.valuation_status or "").strip().lower() in _VALUATION_OK

    # --- 1. 非连续竞价时段：不生成任何盘中交易动作 ---
    if phase != PHASE_CONTINUOUS:
        action = ACTION_WAIT
        degraded = True
        reasons.append(f"当前为 {phase}，不在连续竞价时段，本轮不生成盘中交易动作。")
    # --- 2. 价格缺失 / 无法估值 / 极端陈旧：人工复核 ---
    elif not price_ok:
        action = ACTION_REVIEW_REQUIRED
        degraded = True
        blockers.append("缺少可用现价，无法给出确定性动作。")
        reasons.append("行情价格缺失，先修复行情源或补录价格。")
    elif held and not valuation_ok:
        action = ACTION_REVIEW_REQUIRED
        degraded = True
        blockers.append(f"持仓估值状态异常：{position.valuation_status}")
        reasons.append("持仓无法正常估值，先修复账本或行情后再决策。")
    elif age is None:
        action = ACTION_REVIEW_REQUIRED
        degraded = True
        blockers.append("行情快照缺少 data_as_of，无法判断新鲜度。")
        reasons.append("数据时间未知，禁止输出确定性动作。")
    elif age > policy.hard_stale_seconds:
        action = ACTION_REVIEW_REQUIRED
        degraded = True
        blockers.append(f"行情已过期 {age / 60:.0f} 分钟，超过硬阈值 {policy.hard_stale_seconds / 60:.0f} 分钟。")
        reasons.append("行情严重陈旧，先刷新数据再决策。")
    else:
        action, base_reasons = _base_action(signal, held, pnl_pct, _finite(confidence, 0.0), policy)
        reasons.extend(base_reasons)

        if action in RISK_INCREASING_ACTIONS:
            stale = age > policy.stale_seconds
            if stale:
                action = ACTION_WAIT
                degraded = True
                blockers.append(
                    f"行情已过期 {age / 60:.0f} 分钟，超过 {policy.stale_seconds / 60:.0f} 分钟阈值。"
                )
                reasons.append("数据陈旧，买入类动作降级为等待。")
            elif missing_evidence:
                action = ACTION_WAIT
                degraded = True
                blockers.append("证据状态未知：" + "、".join(missing_evidence))
                reasons.append("新闻/板块证据未核验，买入类动作降级为等待。")
            elif not gate_enabled:
                action = ACTION_WAIT
                degraded = True
                blockers.append("缺少权威组合快照，无法校验组合上限。")
                reasons.append("组合快照缺失，买入类动作降级为等待。")
            elif gate_fail_closed:
                action = ACTION_WAIT
                degraded = True
                blockers.append("组合账本 fail-closed。")
                reasons.extend(str(item) for item in getattr(gate, "reasons", ()) or ())
            elif held and not allow_add:
                action = ACTION_HOLD
                degraded = True
                blockers.append("组合闸门禁止加仓。")
                reasons.extend(str(item) for item in getattr(gate, "reasons", ()) or ())
            elif not held and not allow_new_entry:
                action = ACTION_NO_CHASE
                degraded = True
                blockers.append("组合闸门禁止新开仓。")
                reasons.extend(str(item) for item in getattr(gate, "reasons", ()) or ())

    # --- 3. 数量换算 ---
    quantity_delta = 0.0
    amount_delta = 0.0

    if action in RISK_INCREASING_ACTIONS and price_ok:
        budget = _optional_float(cash)
        if headroom_pct is not None and total_assets is not None:
            cap_amount = max(total_assets * headroom_pct / 100.0, 0.0)
            budget = cap_amount if budget is None else min(budget, cap_amount)
        if budget is None:
            action = ACTION_WAIT
            degraded = True
            blockers.append("无法确认可用现金，不给出买入数量。")
        else:
            quantity = _lots_for_budget(budget, float(price), rule)
            min_buy = float(max(int(rule.min_buy_quantity or rule.lot_size or 1), 1))
            if quantity < min_buy - _EPS:
                need = min_buy * float(price)
                action = ACTION_NO_CHASE
                degraded = True
                quantity = 0.0
                blockers.append(
                    f"资金不足/不可执行：可用 {budget:,.0f} 元，"
                    f"买入最小 {min_buy:.0f} 股需 {need:,.0f} 元。"
                )
                reasons.append("不足一手，本轮不执行买入。")
            else:
                violations = check_lot_size(
                    code, "buy", quantity, held_quantity=held_qty, overrides=lot_overrides
                )
                if violations:
                    action = ACTION_NO_CHASE
                    degraded = True
                    quantity = 0.0
                    blockers.extend(violations)
                else:
                    quantity_delta = quantity
                    amount_delta = quantity * float(price)

    elif action in RISK_REDUCING_ACTIONS and held:
        if action == ACTION_EXIT:
            quantity = held_qty
        else:
            quantity = _trim_quantity(held_qty, policy.trim_ratio, rule)
            if quantity <= _EPS:
                if held_qty < float(max(int(rule.lot_size or 1), 1)) and rule.allow_odd_sell:
                    quantity = held_qty
                    reasons.append("持仓不足一手，减仓退化为一次性卖出零股。")
                else:
                    action = ACTION_HOLD
                    degraded = True
                    blockers.append("持仓数量不足以拆出整手，无法按比例减仓。")
                    quantity = 0.0
        if quantity > _EPS:
            violations = check_lot_size(
                code, "sell", quantity, held_quantity=held_qty, overrides=lot_overrides
            )
            if violations:
                action = ACTION_REVIEW_REQUIRED
                degraded = True
                blockers.extend(violations)
            else:
                quantity_delta = -quantity
                amount_delta = -quantity * float(price or 0.0)

    # ``executable`` 表示"本轮存在可直接下单的数量"，不代表系统会自动下单。
    executable = bool(abs(quantity_delta) > _EPS and action not in DEGRADED_ACTIONS)

    # --- 4. 有效期与条件 ---
    upcoming = next_checkpoint(moment, environ)
    if upcoming is None or upcoming <= moment:
        valid_until = moment + timedelta(minutes=max(policy.valid_minutes, 1))
    else:
        valid_until = upcoming
    support, resistance = _levels(levels, price)
    trigger, invalidation = _condition_text(action, price, support, resistance)

    evidence_as_of: Dict[str, Any] = {}
    for dimension in ("market", "sector", "news"):
        item = (evidence or {}).get(dimension)
        if isinstance(item, Mapping):
            evidence_as_of[dimension] = {
                "as_of": str(item.get("as_of") or ""),
                "status": str(item.get("status") or "unknown"),
            }
        else:
            evidence_as_of[dimension] = {"as_of": "", "status": str(item or "unknown")}
    evidence_as_of["data_age_seconds"] = None if age is None else round(age, 1)

    risk_level = _risk_level(action, degraded, pnl_pct)
    generated_at = _iso(moment)
    data_as_of_iso = _iso(stamp) if stamp is not None else ""
    snapshot_hash = str(getattr(gate, "snapshot_hash", "") or "")
    portfolio_version = int(_finite(getattr(gate, "portfolio_version", 0), 0.0))

    decision_id = _decision_id(code, generated_at, action, snapshot_hash, quantity_delta)

    return IntradayDecision(
        decision_id=decision_id,
        generated_at=generated_at,
        data_as_of=data_as_of_iso,
        code=code,
        action=action,
        position=position,
        name=position.name or str(gate_context.get("name") or ""),
        asset_class=asset_class,
        industry=position.industry or str(gate_context.get("industry") or ""),
        portfolio_version=portfolio_version,
        snapshot_hash=snapshot_hash,
        quantity_delta=quantity_delta,
        amount_delta=amount_delta,
        executable=executable,
        trigger=trigger,
        invalidation=invalidation,
        valid_until=_iso(valid_until),
        confidence=_finite(confidence, 0.0),
        risk_level=risk_level,
        signal=str(signal or SIGNAL_UNKNOWN).strip().lower(),
        session=phase,
        degraded=degraded,
        reasons=tuple(dict.fromkeys(reasons)),
        blockers=tuple(dict.fromkeys(blockers)),
        evidence_as_of=evidence_as_of,
    )


def _risk_level(action: str, degraded: bool, pnl_pct: Optional[float]) -> str:
    if action in (ACTION_EXIT, ACTION_REVIEW_REQUIRED):
        return "high"
    if action in RISK_INCREASING_ACTIONS:
        return "medium" if not degraded else "high"
    if action in RISK_REDUCING_ACTIONS:
        return "medium"
    if pnl_pct is not None and pnl_pct <= -5.0:
        return "medium"
    return "low"


def _decision_id(
    code: str, generated_at: str, action: str, snapshot_hash: str, quantity_delta: float
) -> str:
    payload = "|".join(
        [code, generated_at, action, snapshot_hash, f"{quantity_delta:.4f}"]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"itd-{code or 'na'}-{digest}"


# --------------------------------------------------------------------------
# 快照差异
# --------------------------------------------------------------------------
def _decision_action(item: Any) -> str:
    if isinstance(item, IntradayDecision):
        return item.action
    if isinstance(item, Mapping):
        return str(item.get("action") or "")
    return ""


def _decision_code(item: Any) -> str:
    if isinstance(item, IntradayDecision):
        return item.code
    if isinstance(item, Mapping):
        return str(item.get("code") or "")
    return ""


def diff_decisions(
    previous: Sequence[Any] | None, current: Sequence[Any] | None
) -> List[Dict[str, Any]]:
    """对比上一轮与本轮动作，输出 新增/维持/升级/降级/失效。"""
    prev_map: Dict[str, Any] = {}
    for item in previous or ():
        code = _decision_code(item)
        if code:
            prev_map[code] = item
    result: List[Dict[str, Any]] = []
    seen: set = set()

    for item in current or ():
        code = _decision_code(item)
        if not code:
            continue
        seen.add(code)
        action = _decision_action(item)
        before = prev_map.get(code)
        if before is None:
            kind = DIFF_NEW
            prev_action = ""
        else:
            prev_action = _decision_action(before)
            if prev_action == action:
                kind = DIFF_MAINTAIN
            elif action in RISK_INCREASING_ACTIONS and prev_action in RISK_INCREASING_ACTIONS:
                kind = DIFF_MAINTAIN
            else:
                now_rank = STANCE_RANK.get(action, 0)
                prev_rank = STANCE_RANK.get(prev_action, 0)
                if now_rank > prev_rank:
                    kind = DIFF_UPGRADE
                elif now_rank < prev_rank:
                    kind = DIFF_DOWNGRADE
                else:
                    kind = DIFF_MAINTAIN
        result.append(
            {
                "code": code,
                "kind": kind,
                "kind_label": DIFF_LABELS.get(kind, kind),
                "action": action,
                "action_label": ACTION_LABELS.get(action, action),
                "previous_action": prev_action,
                "previous_action_label": ACTION_LABELS.get(prev_action, prev_action),
            }
        )

    for code, before in prev_map.items():
        if code in seen:
            continue
        prev_action = _decision_action(before)
        result.append(
            {
                "code": code,
                "kind": DIFF_EXPIRED,
                "kind_label": DIFF_LABELS[DIFF_EXPIRED],
                "action": "",
                "action_label": "",
                "previous_action": prev_action,
                "previous_action_label": ACTION_LABELS.get(prev_action, prev_action),
            }
        )

    result.sort(key=lambda row: (DIFF_KINDS.index(row["kind"]), row["code"]))
    return result

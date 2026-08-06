# -*- coding: utf-8 -*-
"""盘中持仓驾驶舱（Issue #67）：把账本 + 行情 + 规则信号压成一份半小时快照。

产出契约 ``nasdx_intraday_snapshot.v1``：

* ``portfolio``  —— 组合级结论（盘面状态、健康度、禁止事项）；
* ``headline``   —— 本轮最重要的 1–3 个动作；
* ``decisions``  —— 每只持仓的 :class:`~nasdx.intraday_decision.IntradayDecision`；
* ``candidates`` —— 自选/候选标的的同构决策；
* ``diff``       —— 相较上一轮快照的 新增/维持/升级/降级/失效；
* ``evidence``   —— 数据时间与证据状态；
* ``performance``—— LLM 调用数与耗时（#65 的盘中目标）。

**本模块不下单、不联网、不调用 LLM**（``llm_calls`` 恒为 0），
调度层只负责触发快照。
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from nasdx.data_loader import get_stock_data, load_latest_data
from nasdx.evidence import CST, PRECISION_UNKNOWN, to_cst, to_cst_precise
from nasdx.intraday_decision import (
    ACTION_HOLD,
    ACTION_LABELS,
    ACTION_WAIT,
    DEFAULT_POLICY,
    FRESHNESS_STALE,
    FRESHNESS_VERIFIED,
    IntradayDecision,
    IntradayPolicy,
    PositionView,
    URGENCY_RANK,
    decide,
    diff_decisions,
    evaluate_data_freshness,
    should_run,
    trading_phase,
)
from nasdx.paths import get_runtime_dir
from nasdx.portfolio_gate import evaluate_portfolio_gate
from nasdx.portfolio_link import resolve_portfolio_auto
from nasdx.rule_based_analysis import fund_flow_signal, technical_signal

SNAPSHOT_SCHEMA = "nasdx_intraday_snapshot.v1"
SNAPSHOT_DIRNAME = "intraday"
LATEST_NAME = "intraday_latest.json"

NEWS_STATUS_ENV = "NASDX_INTRADAY_NEWS_STATUS"
WATCHLIST_ENV = "NASDX_INTRADAY_WATCHLIST"

MARKET_STATE_STRONG = "主升"
MARKET_STATE_REPAIR = "修复"
MARKET_STATE_SPLIT = "分歧"
MARKET_STATE_EBB = "退潮"
MARKET_STATE_UNKNOWN = "数据不足"

_EPS = 1e-9

__all__ = [
    "SNAPSHOT_SCHEMA",
    "build_intraday_snapshot",
    "run_checkpoint",
    "save_intraday_snapshot",
    "load_previous_snapshot",
    "format_intraday_snapshot",
    "snapshot_dir",
]


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
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


def _finite(value: Any, default: float = 0.0) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _as_dict(payload: Any) -> Dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, Mapping):
        return dict(payload)
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
        except Exception:  # pragma: no cover - 防御性
            return {}
        return dict(result) if isinstance(result, Mapping) else {}
    return {}


def snapshot_dir(directory: str | Path | None = None, create: bool = False) -> Path:
    base = Path(directory) if directory else Path(get_runtime_dir()) / SNAPSHOT_DIRNAME
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base


# --------------------------------------------------------------------------
# 行情信号
# --------------------------------------------------------------------------
def _combine_signal(stock: Mapping[str, Any] | None) -> Tuple[str, float, List[str]]:
    """技术面 + 资金面的确定性合成信号，复用规则引擎，不重复实现。"""
    if not stock:
        return "unknown", 0.0, ["未在本地行情快照中找到该标的"]
    technical = technical_signal(dict(stock))
    flow = fund_flow_signal(dict(stock))

    values = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}
    tech_conf = _finite(technical.confidence, 0.0)
    flow_conf = _finite(flow.confidence, 0.0)
    tech_value = values.get(technical.signal, 0.0)
    flow_value = values.get(flow.signal, 0.0)

    if flow_conf <= _EPS:
        score = tech_value * tech_conf
        confidence = tech_conf
    else:
        score = tech_value * tech_conf * 0.7 + flow_value * flow_conf * 0.3
        confidence = tech_conf * 0.7 + flow_conf * 0.3

    if score >= 0.25:
        signal = "bullish"
    elif score <= -0.25:
        signal = "bearish"
    else:
        signal = "neutral"

    points: List[str] = []
    points.extend(str(item) for item in (technical.key_points or [])[:2])
    if flow_conf > _EPS:
        points.extend(str(item) for item in (flow.key_points or [])[:1])
    return signal, round(min(max(confidence, 0.0), 1.0), 4), points


def _levels_from_indicators(stock: Mapping[str, Any] | None) -> Dict[str, Optional[float]]:
    ind = (stock or {}).get("indicators") or {}
    price = _optional_float(ind.get("close") or ind.get("current_price"))
    candidates_below = [
        value
        for value in (
            _optional_float(ind.get("ma5")),
            _optional_float(ind.get("ma10")),
            _optional_float(ind.get("ma20")),
            _optional_float(ind.get("boll_lower")),
        )
        if value is not None and price is not None and value < price
    ]
    candidates_above = [
        value
        for value in (
            _optional_float(ind.get("ma5")),
            _optional_float(ind.get("ma10")),
            _optional_float(ind.get("ma20")),
            _optional_float(ind.get("boll_upper")),
        )
        if value is not None and price is not None and value > price
    ]
    return {
        "support": max(candidates_below) if candidates_below else None,
        "resistance": min(candidates_above) if candidates_above else None,
    }


def _market_state(data: Mapping[str, Any] | None) -> Tuple[str, Optional[float]]:
    overview = (data or {}).get("market_overview")
    if not isinstance(overview, Mapping) or not overview:
        return MARKET_STATE_UNKNOWN, None
    values = [
        _optional_float((item or {}).get("change_pct"))
        for item in overview.values()
        if isinstance(item, Mapping)
    ]
    values = [item for item in values if item is not None]
    if not values:
        return MARKET_STATE_UNKNOWN, None
    average = sum(values) / len(values)
    if average >= 1.5:
        state = MARKET_STATE_STRONG
    elif average >= 0.3:
        state = MARKET_STATE_REPAIR
    elif average > -0.5:
        state = MARKET_STATE_SPLIT
    else:
        state = MARKET_STATE_EBB
    return state, round(average, 3)


def _data_as_of(data: Mapping[str, Any] | None) -> Tuple[Optional[datetime], str]:
    """读取行情快照时间并同时返回时间精度（Issue #84）。

    ``generated_at`` 优先于 ``date``。纯日期输入（``YYYY-MM-DD`` / ``YYYYMMDD``）
    统一解析为当天 ``00:00 CST`` 并标记为 :data:`~nasdx.evidence.PRECISION_DATE`；
    **不再**把 8 位日期猜测成收盘 15:00 —— 上午运行时那会凭空造出未来时间戳。
    日终文件的正确做法是写入真实的 ``generated_at`` 时刻。
    """
    raw = (data or {}).get("generated_at") or (data or {}).get("date")
    return to_cst_precise(raw)


def _news_status(explicit: Optional[str], environ: Mapping[str, str] | None) -> str:
    if explicit:
        return str(explicit).strip().lower()
    env = os.environ if environ is None else environ
    return str(env.get(NEWS_STATUS_ENV, "") or "unknown").strip().lower()


def _watchlist(explicit: Sequence[str] | None, environ: Mapping[str, str] | None) -> List[str]:
    if explicit:
        return [str(item).strip() for item in explicit if str(item).strip()]
    env = os.environ if environ is None else environ
    raw = str(env.get(WATCHLIST_ENV, "") or "")
    return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


# --------------------------------------------------------------------------
# 快照装配
# --------------------------------------------------------------------------
def build_intraday_snapshot(
    *,
    now: Any = None,
    portfolio: Any = None,
    data: Mapping[str, Any] | None = None,
    watchlist: Sequence[str] | None = None,
    policy: IntradayPolicy = DEFAULT_POLICY,
    previous: Mapping[str, Any] | None = None,
    news_status: Optional[str] = None,
    holidays: Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
    use_ledger: bool = True,
) -> Dict[str, Any]:
    """生成一轮盘中快照。纯计算 + 本地读取，不下单、不调用 LLM。"""
    started = time.perf_counter()
    moment = to_cst(now) or datetime.now(CST)
    phase = trading_phase(moment, holidays, environ)

    market = dict(data) if data is not None else _safe_market_data()
    stamp, precision = _data_as_of(market)
    # Issue #84：新鲜度统一由 evaluate_data_freshness 判定，未来时间戳 / 纯日期
    # 精度都不得被当作"已核验"，负年龄绝不能冒充"更新鲜"。
    freshness = evaluate_data_freshness(stamp, moment, policy, precision=precision)
    age = freshness.age_seconds
    market_stale = not freshness.usable

    if portfolio is None and use_ledger:
        portfolio = resolve_portfolio_auto(data=market, environ=environ)
    snapshot = _as_dict(portfolio)

    news = _news_status(news_status, environ)
    stamp_text = stamp.isoformat(timespec="seconds") if stamp else ""
    market_evidence_status = FRESHNESS_VERIFIED if freshness.usable else FRESHNESS_STALE
    evidence_base = {
        "market": {"as_of": stamp_text, "status": market_evidence_status},
        "sector": {"as_of": stamp_text, "status": market_evidence_status},
        "news": {"as_of": "", "status": news or "unknown"},
    }

    positions = snapshot.get("positions") or []
    positions = [item for item in positions if isinstance(item, Mapping)]

    decisions: List[IntradayDecision] = []
    for row in positions:
        decisions.append(
            _decide_row(
                row=row,
                snapshot=snapshot,
                market=market,
                moment=moment,
                stamp=stamp,
                precision=precision,
                policy=policy,
                evidence=evidence_base,
                holidays=holidays,
                environ=environ,
            )
        )

    held_codes = {str(row.get("code") or "").strip() for row in positions}
    candidates: List[IntradayDecision] = []
    for code in _watchlist(watchlist, environ):
        if code in held_codes:
            continue
        candidates.append(
            _decide_row(
                row={"code": code},
                snapshot=snapshot,
                market=market,
                moment=moment,
                stamp=stamp,
                precision=precision,
                policy=policy,
                evidence=evidence_base,
                holidays=holidays,
                environ=environ,
            )
        )

    # Issue #84：时间戳不可信时留下可审计的文字 blocker，即使当前没有任何持仓
    # 或候选（此时 decisions/candidates 为空，光靠决策级 blockers 看不出问题）。
    freshness_blockers: List[str] = [freshness.reason] if freshness.reason else []
    notes = ["本快照为研究辅助输出，所有委托需人工确认，系统不会自动下单。"]
    if freshness.future:
        notes.append(f"⚠️ 行情时间戳不可信（未来时间）：{freshness.reason}本轮不得据此建仓或加仓。")
    elif market_stale and freshness.reason:
        notes.append(f"⚠️ 行情数据未通过新鲜度校验：{freshness.reason}")

    state, average_change = _market_state(market)
    previous_decisions = (previous or {}).get("decisions") or []
    previous_candidates = (previous or {}).get("candidates") or []
    diff = diff_decisions(
        list(previous_decisions) + list(previous_candidates),
        [item.to_dict() for item in decisions + candidates],
    )

    payload: Dict[str, Any] = {
        "schema": SNAPSHOT_SCHEMA,
        "generated_at": moment.isoformat(timespec="seconds"),
        "data_as_of": stamp_text,
        "trading_day": moment.date().isoformat(),
        "session": phase,
        "checkpoint": _checkpoint_label(moment),
        "valid_until": _valid_until(decisions + candidates, moment, policy),
        "portfolio": _portfolio_block(snapshot, state, average_change, market_stale, age),
        "headline": _headline(decisions + candidates),
        "prohibitions": _prohibitions(snapshot, decisions + candidates),
        "decisions": [item.to_dict() for item in decisions],
        "candidates": [item.to_dict() for item in candidates],
        "diff": diff,
        "evidence": {
            **evidence_base,
            "data_age_seconds": None if age is None else round(age, 1),
            "stale_threshold_seconds": policy.stale_seconds,
            "market_stale": bool(market_stale),
            # Issue #84：把不可信时间戳的具体原因暴露出来，便于排障与回放。
            "clock_skew_seconds": (
                None
                if freshness.clock_skew_seconds is None
                else round(freshness.clock_skew_seconds, 1)
            ),
            "future_timestamp": bool(freshness.future),
            "data_precision": freshness.precision,
            "freshness_status": freshness.status,
            "max_future_skew_seconds": policy.max_future_skew_seconds,
            "freshness_reason": freshness.reason,
            "blockers": freshness_blockers,
        },
        "performance": {
            "depth": "intraday",
            "llm_calls": 0,
            "elapsed_ms": 0.0,
            "decision_count": len(decisions) + len(candidates),
        },
        "auto_trading": False,
        "notes": notes,
    }
    payload["performance"]["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
    return payload


def _safe_market_data() -> Dict[str, Any]:
    try:
        return dict(load_latest_data() or {})
    except Exception:
        return {}


def _decide_row(
    *,
    row: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    market: Mapping[str, Any],
    moment: datetime,
    stamp: Optional[datetime],
    policy: IntradayPolicy,
    evidence: Mapping[str, Any],
    holidays: Iterable[str] | None,
    environ: Mapping[str, str] | None,
    precision: str = PRECISION_UNKNOWN,
) -> IntradayDecision:
    code = str(row.get("code") or "").strip()
    stock = None
    try:
        stock = get_stock_data(dict(market), code) if market else None
    except Exception:
        stock = None

    indicator_price = None
    if stock:
        indicator_price = _optional_float(
            (stock.get("indicators") or {}).get("close")
            or (stock.get("indicators") or {}).get("current_price")
        )
    price = _optional_float(row.get("last_price"))
    if price is None:
        price = indicator_price

    quantity = _finite(row.get("quantity"), 0.0)
    cost = _optional_float(row.get("avg_cost"))
    market_value = _optional_float(row.get("market_value"))
    if market_value is None and price is not None and quantity > _EPS:
        market_value = price * quantity
    unrealized = _optional_float(row.get("unrealized_pnl"))
    if unrealized is None and price is not None and cost is not None and quantity > _EPS:
        unrealized = (price - cost) * quantity
    unrealized_pct = _optional_float(row.get("unrealized_pct"))
    if unrealized_pct is None and price is not None and cost is not None and cost > _EPS:
        unrealized_pct = (price / cost - 1.0) * 100.0

    industry = str(row.get("industry") or "")
    name = str(row.get("name") or (stock or {}).get("name") or "")
    valuation_status = str(row.get("valuation_status") or ("ok" if price is not None else "missing_price"))

    view = PositionView(
        code=code,
        name=name,
        asset_class=str(row.get("asset_class") or ""),
        industry=industry,
        quantity=quantity,
        cost=cost,
        current_price=price,
        market_value=market_value,
        unrealized_pnl=unrealized,
        unrealized_pnl_pct=unrealized_pct,
        weight_pct=_optional_float(row.get("weight_pct")),
        valuation_status=valuation_status,
    )

    gate = evaluate_portfolio_gate(code, snapshot or None, industry=industry or None)
    signal, confidence, points = _combine_signal(stock)

    return decide(
        position=view,
        signal=signal,
        confidence=confidence,
        gate=gate,
        data_as_of=stamp,
        data_precision=precision,
        evidence=evidence,
        now=moment,
        policy=policy,
        levels=_levels_from_indicators(stock),
        holidays=holidays,
        environ=environ,
        extra_reasons=points,
    )


def _checkpoint_label(moment: datetime) -> str:
    return f"{moment.hour:02d}:{moment.minute:02d}"


def _valid_until(
    decisions: Sequence[IntradayDecision], moment: datetime, policy: IntradayPolicy
) -> str:
    stamps = [item.valid_until for item in decisions if item.valid_until]
    if stamps:
        return min(stamps)
    return (moment + timedelta(minutes=max(policy.valid_minutes, 1))).isoformat(timespec="seconds")


def _portfolio_block(
    snapshot: Mapping[str, Any],
    market_state: str,
    average_change: Optional[float],
    market_stale: bool,
    age: Optional[float],
) -> Dict[str, Any]:
    linked = bool(snapshot)
    exposure = _optional_float(snapshot.get("exposure_pct"))
    total_assets = _optional_float(snapshot.get("total_assets"))
    cash = _optional_float(snapshot.get("cash"))
    industry_exposure = snapshot.get("industry_exposure")
    industry_exposure = dict(industry_exposure) if isinstance(industry_exposure, Mapping) else {}
    top_industry = ""
    top_industry_pct = None
    if industry_exposure:
        top_industry, top_value = max(
            industry_exposure.items(), key=lambda kv: _finite(kv[1], 0.0)
        )
        top_industry_pct = _optional_float(top_value)

    if not linked:
        health = "未接入账本"
    elif snapshot.get("fail_closed"):
        health = "账本异常（fail-closed）"
    elif exposure is not None and exposure >= 95.0:
        health = "接近满仓"
    elif exposure is not None and exposure <= 30.0:
        health = "仓位偏轻"
    else:
        health = "仓位正常"

    return {
        "linked": linked,
        "market_state": market_state if not market_stale else MARKET_STATE_UNKNOWN,
        "market_state_raw": market_state,
        "index_avg_change_pct": average_change,
        "health": health,
        "total_assets": total_assets,
        "cash": cash,
        "exposure_pct": exposure,
        "position_count": len(snapshot.get("positions") or []),
        "industry_exposure": industry_exposure,
        "top_industry": top_industry,
        "top_industry_pct": top_industry_pct,
        "portfolio_version": int(_finite(snapshot.get("portfolio_version"), 0.0)),
        "snapshot_hash": str(snapshot.get("snapshot_hash") or ""),
        "fail_closed": bool(snapshot.get("fail_closed")),
        "blocking_reasons": [str(item) for item in (snapshot.get("blocking_reasons") or [])],
        "data_age_seconds": None if age is None else round(age, 1),
    }


def _headline(decisions: Sequence[IntradayDecision], limit: int = 3) -> List[Dict[str, Any]]:
    ranked = sorted(
        decisions,
        key=lambda item: (
            -URGENCY_RANK.get(item.action, 0),
            -abs(_finite(item.amount_delta, 0.0)),
            item.code,
        ),
    )
    result: List[Dict[str, Any]] = []
    for item in ranked:
        if item.action in (ACTION_HOLD, ACTION_WAIT) and result:
            break
        result.append(
            {
                "code": item.code,
                "name": item.name,
                "action": item.action,
                "action_label": ACTION_LABELS.get(item.action, item.action),
                "quantity_delta": item.quantity_delta,
                "amount_delta": item.amount_delta,
                "trigger": item.trigger,
                "valid_until": item.valid_until,
                "executable": item.executable,
            }
        )
        if len(result) >= limit:
            break
    return result


def _prohibitions(
    snapshot: Mapping[str, Any], decisions: Sequence[IntradayDecision]
) -> List[str]:
    items: List[str] = []
    if snapshot.get("fail_closed"):
        items.extend(str(row) for row in (snapshot.get("blocking_reasons") or []))
        items.append("账本 fail-closed 期间禁止任何确定性买入或加仓。")
    for item in decisions:
        for blocker in item.blockers:
            text = f"{item.code} {item.name}：{blocker}".strip()
            items.append(text)
    if not snapshot:
        items.append("未接入权威组合账本，本轮不输出确定性买入动作。")
    return list(dict.fromkeys(items))[:12]


# --------------------------------------------------------------------------
# 调度 / 持久化
# --------------------------------------------------------------------------
def run_checkpoint(
    *,
    now: Any = None,
    force: bool = False,
    tolerance_seconds: float = 90.0,
    save: bool = True,
    directory: str | Path | None = None,
    holidays: Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """调度入口：只在交易日的检查点窗口生成快照，绝不下单。"""
    moment = to_cst(now) or datetime.now(CST)
    allowed, reason = should_run(moment, tolerance_seconds, holidays, environ)
    if not allowed and not force:
        return {
            "ran": False,
            "reason": reason,
            "session": trading_phase(moment, holidays, environ),
            "generated_at": moment.isoformat(timespec="seconds"),
            "snapshot": None,
        }

    previous = kwargs.pop("previous", None)
    if previous is None:
        previous = load_previous_snapshot(directory)
    snapshot = build_intraday_snapshot(
        now=moment,
        previous=previous,
        holidays=holidays,
        environ=environ,
        **kwargs,
    )
    paths: Dict[str, str] = {}
    if save:
        paths = save_intraday_snapshot(snapshot, directory)
    return {
        "ran": True,
        "reason": reason if allowed else "强制生成（--force），已跳过检查点校验。",
        "forced": bool(force and not allowed),
        "session": snapshot.get("session"),
        "generated_at": snapshot.get("generated_at"),
        "snapshot": snapshot,
        "paths": paths,
    }


def save_intraday_snapshot(
    snapshot: Mapping[str, Any], directory: str | Path | None = None
) -> Dict[str, str]:
    base = snapshot_dir(directory, create=True)
    stamp = str(snapshot.get("generated_at") or "").replace(":", "").replace("-", "")
    stamp = stamp.split("+")[0][:15] or datetime.now(CST).strftime("%Y%m%dT%H%M%S")
    target = base / f"intraday_{stamp}.json"
    payload = json.dumps(dict(snapshot), ensure_ascii=False, indent=2)
    target.write_text(payload, encoding="utf-8")
    latest = base / LATEST_NAME
    latest.write_text(payload, encoding="utf-8")
    return {"snapshot": str(target), "latest": str(latest)}


def load_previous_snapshot(
    directory: str | Path | None = None,
) -> Optional[Dict[str, Any]]:
    base = snapshot_dir(directory)
    latest = base / LATEST_NAME
    if not latest.exists():
        return None
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# --------------------------------------------------------------------------
# 文本渲染
# --------------------------------------------------------------------------
def format_intraday_snapshot(snapshot: Mapping[str, Any]) -> str:
    portfolio = snapshot.get("portfolio") or {}
    lines: List[str] = []
    lines.append(f"# 盘中驾驶舱 {snapshot.get('trading_day', '')} {snapshot.get('checkpoint', '')}")
    lines.append("")
    lines.append(
        f"- 时段：{snapshot.get('session', '')}　盘面：{portfolio.get('market_state', '')}"
        f"　组合：{portfolio.get('health', '')}"
    )
    lines.append(
        f"- 数据时间：{snapshot.get('data_as_of') or '未知'}　有效期至：{snapshot.get('valid_until', '')}"
    )
    # Issue #84：时间戳不可信时必须在人眼可见的位置给出告警，不能只留在 JSON 里。
    evidence = snapshot.get("evidence") or {}
    for blocker in evidence.get("blockers") or []:
        lines.append(f"- ⚠️ 数据时间告警：{blocker}")
    exposure = portfolio.get("exposure_pct")
    cash = portfolio.get("cash")
    lines.append(
        f"- 敞口：{exposure if exposure is not None else '未知'}%"
        f"　现金：{cash if cash is not None else '未知'}"
        f"　持仓数：{portfolio.get('position_count', 0)}"
    )
    lines.append("")

    headline = snapshot.get("headline") or []
    lines.append("## 本轮重点动作")
    if headline:
        for item in headline:
            lines.append(
                f"- {item.get('code')} {item.get('name')}：**{item.get('action_label')}**"
                f"　数量 {item.get('quantity_delta')}　触发：{item.get('trigger')}"
            )
    else:
        lines.append("- 本轮无需操作。")
    lines.append("")

    prohibitions = snapshot.get("prohibitions") or []
    if prohibitions:
        lines.append("## 当前禁止事项")
        for item in prohibitions:
            lines.append(f"- {item}")
        lines.append("")

    lines.append("## 持仓动作")
    decisions = snapshot.get("decisions") or []
    if not decisions:
        lines.append("- 暂无持仓或未接入账本。")
    else:
        lines.append("| 代码 | 名称 | 数量 | 成本 | 现价 | 浮盈亏% | 动作 | 计划变动 | 有效期至 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- |")
        for item in decisions:
            pos = item.get("position") or {}
            lines.append(
                "| {code} | {name} | {qty} | {cost} | {price} | {pnl} | {action} | {delta} | {valid} |".format(
                    code=item.get("code", ""),
                    name=item.get("name", ""),
                    qty=_fmt(pos.get("quantity"), 0),
                    cost=_fmt(pos.get("cost"), 3),
                    price=_fmt(pos.get("current_price"), 3),
                    pnl=_fmt(pos.get("unrealized_pnl_pct"), 2),
                    action=item.get("action_label", item.get("action", "")),
                    delta=_fmt(item.get("quantity_delta"), 0),
                    valid=str(item.get("valid_until", ""))[11:16],
                )
            )
    lines.append("")

    candidates = snapshot.get("candidates") or []
    if candidates:
        lines.append("## 自选候选")
        for item in candidates:
            lines.append(
                f"- {item.get('code')} {item.get('name')}：{item.get('action_label')}"
                f"（{'、'.join(item.get('blockers') or []) or item.get('trigger', '')}）"
            )
        lines.append("")

    diff = snapshot.get("diff") or []
    lines.append("## 相较上一轮")
    changed = [row for row in diff if row.get("kind") != "maintain"]
    if not diff:
        lines.append("- 首轮快照，无对比基准。")
    elif not changed:
        lines.append("- 全部维持不变。")
    else:
        for row in changed:
            lines.append(
                f"- {row.get('code')}：{row.get('kind_label')}"
                f"（{row.get('previous_action_label') or '无'} → {row.get('action_label') or '失效'}）"
            )
    lines.append("")

    performance = snapshot.get("performance") or {}
    lines.append(
        f"_LLM 调用 {performance.get('llm_calls', 0)} 次，耗时 {performance.get('elapsed_ms', 0)} ms；"
        "输出为研究辅助，系统不会自动下单。_"
    )
    return "\n".join(lines)


def _fmt(value: Any, digits: int) -> str:
    parsed = _optional_float(value)
    if parsed is None:
        return "-"
    return f"{parsed:,.{digits}f}"

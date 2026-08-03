"""
Portfolio-first gate for single-name decisions (Issue #66 acceptance criterion #7).

The gate is fully deterministic and runs *before* a single-name action is
finalized, so a bullish single-name view can never override portfolio-level
risk limits or a fail-closed ledger.

Inputs
------
``PortfolioSnapshot`` (or its ``to_dict()`` mapping) produced by
:mod:`nasdx.portfolio_store`. When no snapshot is supplied the gate stays in
``unknown`` status and permits everything, which keeps the pre-#66 behaviour of
callers that never wired a portfolio in.

Guarantees
----------
* ``fail_closed`` snapshots (broken ledger, missing price, unknown/negative
  cash) never yield a deterministic "buy / add" action.
* Single-name weight cap and industry concentration cap block *adding*, but
  never block reducing or exiting.
* Zero or unknown available cash blocks new money, not risk reduction.
* The evaluation depends only on the snapshot content, so the same snapshot
  always produces the same gate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping

GATE_SCHEMA = "nasdx_portfolio_gate.v1"

STATUS_UNKNOWN = "unknown"
STATUS_NORMAL = "normal"
STATUS_NO_ADD = "no_add"
STATUS_BLOCKED = "blocked"

UNCLASSIFIED_INDUSTRY = "未分类"

_EPS = 1e-9


@dataclass(frozen=True)
class PortfolioGate:
    """Deterministic portfolio-level verdict for one candidate code."""

    schema: str
    status: str
    allow_new_entry: bool
    allow_add: bool
    allow_reduce: bool
    max_new_position_pct: float | None
    snapshot_hash: str
    portfolio_version: int
    fail_closed: bool
    reasons: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_enabled(self) -> bool:
        """True when a portfolio snapshot was actually supplied."""
        return self.status != STATUS_UNKNOWN

    @property
    def held(self) -> bool:
        return bool(self.context.get("held_quantity"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_portfolio_gate(
    stock_code: str,
    portfolio: Any = None,
    industry: str | None = None,
) -> PortfolioGate:
    """Evaluate portfolio-level constraints for ``stock_code``.

    ``portfolio`` accepts a :class:`~nasdx.portfolio_store.PortfolioSnapshot`,
    the mapping produced by its ``to_dict()``, or ``None``.
    """
    code = _normalize_code(stock_code)
    snapshot = _as_mapping(portfolio)
    if snapshot is None:
        return PortfolioGate(
            schema=GATE_SCHEMA,
            status=STATUS_UNKNOWN,
            allow_new_entry=True,
            allow_add=True,
            allow_reduce=True,
            max_new_position_pct=None,
            snapshot_hash="",
            portfolio_version=0,
            fail_closed=False,
            reasons=["未接入组合快照，组合层闸门未启用，仅按单票证据给出建议。"],
            context={"code": code, "portfolio_linked": False},
        )

    policy = _as_dict(snapshot.get("policy"))
    single_cap = _finite(policy.get("single_name_cap_pct"), default=10.0)
    industry_cap = _finite(policy.get("industry_cap_pct"), default=30.0)

    position = _find_position(snapshot, code)
    total_assets = _optional_float(snapshot.get("total_assets"))
    cash = _optional_float(snapshot.get("cash"))
    resolved_industry = _resolve_industry(position, industry)
    industry_weight_pct = _industry_weight_pct(snapshot, resolved_industry, total_assets)
    weight_pct = _optional_float((position or {}).get("weight_pct"))
    held_quantity = _finite((position or {}).get("quantity"), default=0.0)

    context: Dict[str, Any] = {
        "code": code,
        "portfolio_linked": True,
        "name": str((position or {}).get("name") or ""),
        "held": held_quantity > _EPS,
        "held_quantity": held_quantity,
        "avg_cost": _optional_float((position or {}).get("avg_cost")),
        "cost_basis": _optional_float((position or {}).get("cost_basis")),
        "last_price": _optional_float((position or {}).get("last_price")),
        "price_as_of": str((position or {}).get("price_as_of") or ""),
        "market_value": _optional_float((position or {}).get("market_value")),
        "unrealized_pnl": _optional_float((position or {}).get("unrealized_pnl")),
        "unrealized_pct": _optional_float((position or {}).get("unrealized_pct")),
        "weight_pct": weight_pct,
        "valuation_status": str((position or {}).get("valuation_status") or ""),
        "industry": resolved_industry,
        "industry_weight_pct": industry_weight_pct,
        "asset_class": str((position or {}).get("asset_class") or ""),
        "cash": cash,
        "cash_status": str(snapshot.get("cash_status") or "unknown"),
        "total_assets": total_assets,
        "exposure_pct": _optional_float(snapshot.get("exposure_pct")),
        "single_name_cap_pct": single_cap,
        "industry_cap_pct": industry_cap,
        "position_count": len(_positions(snapshot)),
    }

    snapshot_hash = str(snapshot.get("snapshot_hash") or "")
    portfolio_version = int(_finite(snapshot.get("portfolio_version"), default=0))
    fail_closed = bool(snapshot.get("fail_closed"))

    if fail_closed:
        reasons = [
            f"组合账本 fail-closed：{item}"
            for item in _as_list(snapshot.get("blocking_reasons"))
        ] or ["组合账本 fail-closed，缺少可信持仓/现金/价格，禁止输出确定性买入或加仓。"]
        context["single_name_headroom_pct"] = 0.0
        return PortfolioGate(
            schema=GATE_SCHEMA,
            status=STATUS_BLOCKED,
            allow_new_entry=False,
            allow_add=False,
            allow_reduce=True,
            max_new_position_pct=0.0,
            snapshot_hash=snapshot_hash,
            portfolio_version=portfolio_version,
            fail_closed=True,
            reasons=reasons,
            context=context,
        )

    reasons: List[str] = []
    allow_new_entry = True
    allow_add = True

    headroom = _single_name_headroom(weight_pct, single_cap)
    context["single_name_headroom_pct"] = headroom

    if held_quantity > _EPS and weight_pct is None:
        allow_add = False
        reasons.append("持仓无法估值（缺最新价格），不新增仓位，先修复行情或补录价格。")

    if headroom is not None and headroom <= _EPS:
        allow_add = False
        allow_new_entry = False
        reasons.append(
            f"{code} 已占组合 {weight_pct:.2f}%，达到单票上限 {single_cap:.2f}%，禁止继续加仓。"
        )

    if (
        industry_weight_pct is not None
        and resolved_industry != UNCLASSIFIED_INDUSTRY
        and industry_weight_pct >= industry_cap - _EPS
    ):
        allow_add = False
        allow_new_entry = False
        reasons.append(
            f"{resolved_industry} 行业敞口 {industry_weight_pct:.2f}% 已达上限 "
            f"{industry_cap:.2f}%，禁止继续加同类标的。"
        )

    if cash is not None and cash <= _EPS:
        allow_add = False
        allow_new_entry = False
        reasons.append("可用现金为 0 或为负，账户没有加仓资金。")

    exposure_pct = context["exposure_pct"]
    if exposure_pct is not None and exposure_pct >= 100.0 - _EPS:
        allow_add = False
        allow_new_entry = False
        reasons.append(f"组合已满仓（敞口 {exposure_pct:.2f}%），新增前需先腾出仓位。")

    max_new = 0.0 if not (allow_add or allow_new_entry) else headroom
    status = STATUS_NORMAL if (allow_add and allow_new_entry) else STATUS_NO_ADD
    if status == STATUS_NORMAL and not reasons:
        reasons.append("组合层未触发硬约束，可按单票纪律执行。")

    return PortfolioGate(
        schema=GATE_SCHEMA,
        status=status,
        allow_new_entry=allow_new_entry,
        allow_add=allow_add,
        allow_reduce=True,
        max_new_position_pct=max_new,
        snapshot_hash=snapshot_hash,
        portfolio_version=portfolio_version,
        fail_closed=False,
        reasons=reasons,
        context=context,
    )


def format_portfolio_gate(gate: PortfolioGate) -> str:
    """Render the gate verdict as one compact Chinese line."""
    if not gate.is_enabled:
        return "组合闸门：未接入组合快照"
    context = gate.context
    parts = [f"组合闸门：{gate.status}"]
    if context.get("held"):
        parts.append(
            "当前持有 {qty:.0f} 股 · 成本 {cost} · 现价 {price} · 浮动 {pnl}".format(
                qty=float(context.get("held_quantity") or 0.0),
                cost=_fmt_number(context.get("avg_cost")),
                price=_fmt_number(context.get("last_price")),
                pnl=_fmt_pct(context.get("unrealized_pct")),
            )
        )
    else:
        parts.append("当前未持有")
    parts.append(f"可新增：{'是' if gate.allow_add or gate.allow_new_entry else '否'}")
    if gate.reasons:
        parts.append(gate.reasons[0])
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _as_mapping(portfolio: Any) -> Dict[str, Any] | None:
    if portfolio is None:
        return None
    if isinstance(portfolio, Mapping):
        return dict(portfolio)
    to_dict = getattr(portfolio, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        if isinstance(candidate, Mapping):
            return dict(candidate)
    raise TypeError(
        "portfolio must be a PortfolioSnapshot or mapping, got " f"{type(portfolio).__name__}"
    )


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _positions(snapshot: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = snapshot.get("positions")
    if not isinstance(rows, (list, tuple)):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _find_position(snapshot: Mapping[str, Any], code: str) -> Dict[str, Any] | None:
    for row in _positions(snapshot):
        if _normalize_code(row.get("code")) == code:
            return row
    return None


def _resolve_industry(position: Mapping[str, Any] | None, industry: str | None) -> str:
    explicit = str(industry or "").strip()
    if explicit:
        return explicit
    held = str((position or {}).get("industry") or "").strip()
    return held or UNCLASSIFIED_INDUSTRY


def _industry_weight_pct(
    snapshot: Mapping[str, Any], industry: str, total_assets: float | None
) -> float | None:
    if industry == UNCLASSIFIED_INDUSTRY:
        return None
    if total_assets is None or total_assets <= _EPS:
        return None
    exposure = _as_dict(snapshot.get("industry_exposure")).get(industry)
    amount = _optional_float(exposure)
    if amount is None:
        return 0.0
    return round(amount / total_assets * 100, 4)


def _single_name_headroom(weight_pct: float | None, cap_pct: float) -> float | None:
    if cap_pct <= 0:
        return 0.0
    current = weight_pct if weight_pct is not None else 0.0
    return round(max(cap_pct - current, 0.0), 4)


def _normalize_code(value: Any) -> str:
    return str(value or "").strip().upper()


def _finite(value: Any, default: float) -> float:
    number = _optional_float(value)
    return default if number is None else number


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _fmt_number(value: Any) -> str:
    number = _optional_float(value)
    return "未知" if number is None else f"{number:,.3f}".rstrip("0").rstrip(".")


def _fmt_pct(value: Any) -> str:
    number = _optional_float(value)
    return "未知" if number is None else f"{number:+.2f}%"

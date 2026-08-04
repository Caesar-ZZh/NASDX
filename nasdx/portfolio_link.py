"""权威持仓账本 → 分析入口的接线层（Issue #66 验收 #6/#7 的端到端补齐）。

背景
----
#66 已经交付了账本（``portfolio_store``）、组合闸门（``portfolio_gate``）和
决策层接入（``decision`` / ``position_sizing``），#65 也把
``portfolio_snapshot_hash`` 写进了缓存失效输入。但生产入口
（``run_analysis.py`` / ``analyze.py`` / ``NasdxAnalyzer.analyze_batch``）
从未传入 ``portfolio=``，因此真实运行时闸门永远是 ``unknown``、盘中缓存也
永远看不到成交变化。本模块负责把账本安全地接到这些入口上。

设计约束
--------
* **未初始化账本 = 不接入。** 没有库文件、或库里既无成交也无现金基线时返回
  ``None``，行为与 #66 之前完全一致，不会因为一个空账本把所有建议 fail-closed。
* **已初始化账本 = 强制接入。** 账本存在就必须参与决策；坏账本 / 缺价 / 缺现金
  由 ``portfolio_gate`` 按 fail-closed 处理，绝不静默放行。
* **解析过程零副作用。** 不新建数据库、不写盘、不抛异常；任何意外都退化为
  fail-closed 快照而不是"无组合"。
* **可关闭。** ``--no-portfolio`` 或 ``NASDX_PORTFOLIO_LINK=0`` 显式退回单票模式。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from nasdx.portfolio_store import (
    PortfolioSnapshot,
    build_snapshot,
    portfolio_db_path,
    portfolio_status,
)

LINK_ENV = "NASDX_PORTFOLIO_LINK"
_DISABLED_VALUES = {"0", "false", "off", "no"}

__all__ = [
    "LINK_ENV",
    "link_enabled",
    "ledger_is_initialized",
    "market_price_map",
    "market_industry_map",
    "load_market_snapshot",
    "resolve_portfolio",
    "resolve_portfolio_auto",
    "describe_portfolio_link",
]


def link_enabled(
    explicit: Optional[bool] = None, environ: Mapping[str, str] | None = None
) -> bool:
    """命令行开关优先，其次 ``NASDX_PORTFOLIO_LINK``，默认开启。"""
    if explicit is not None:
        return bool(explicit)
    env = os.environ if environ is None else environ
    raw = str(env.get(LINK_ENV, "")).strip().lower()
    if raw in _DISABLED_VALUES:
        return False
    return True


def ledger_is_initialized(db_path: str | Path | None = None) -> bool:
    """账本是否已被真正使用过（不创建数据库文件）。

    仅当库文件已存在，且其中已有成交事件或现金基线时才算"已接入"。
    库文件存在但读不出来（损坏 / schema 不兼容）同样算已接入，
    这样上层会拿到 fail-closed 快照，而不是被当成"没有组合"。
    """
    try:
        path = portfolio_db_path(db_path)
    except Exception:  # noqa: BLE001 - 路径解析失败按未接入处理
        return False
    if not path.exists():
        return False
    try:
        status = portfolio_status(db_path=path)
    except Exception:  # noqa: BLE001 - 读失败按已接入 + fail-closed 处理
        return True
    if not status.get("healthy"):
        return True
    return bool(status.get("event_count")) or status.get("cash_baseline") is not None


def _price_from_entry(entry: Mapping[str, Any]) -> Optional[float]:
    indicators = entry.get("indicators") or {}
    if isinstance(indicators, Mapping):
        for key in ("close", "current_price"):
            value = indicators.get(key)
            if isinstance(value, bool) or value is None:
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue
            if price == price and price > 0:  # NaN-safe
                return price
    fund_flow = entry.get("fund_flow") or []
    if isinstance(fund_flow, list) and fund_flow:
        last = fund_flow[-1]
        if isinstance(last, Mapping):
            value = last.get("收盘价")
            try:
                price = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            if price == price and price > 0:
                return price
    return None


def _iter_entries(data: Mapping[str, Any] | None):
    for sector in (data or {}).get("sectors", []) or []:
        if not isinstance(sector, Mapping):
            continue
        sector_name = str(sector.get("name") or "")
        for bucket in ("stocks", "etfs"):
            for entry in sector.get(bucket, []) or []:
                if isinstance(entry, Mapping) and entry.get("code"):
                    yield sector_name, str(entry["code"]), entry


def market_price_map(data: Mapping[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    """从行情快照抽取 ``code -> {"price", "as_of"}``，供账本估值使用。"""
    as_of = str((data or {}).get("date") or "")
    prices: Dict[str, Dict[str, Any]] = {}
    for _sector, code, entry in _iter_entries(data):
        price = _price_from_entry(entry)
        if price is None:
            continue
        prices[code] = {"price": price, "as_of": as_of}
    return prices


def market_industry_map(data: Mapping[str, Any] | None) -> Dict[str, str]:
    """从行情快照抽取 ``code -> 板块名``，供行业集中度闸门使用。"""
    industries: Dict[str, str] = {}
    for sector_name, code, _entry in _iter_entries(data):
        if sector_name:
            industries.setdefault(code, sector_name)
    return industries


def _fail_closed_snapshot(message: str) -> Dict[str, Any]:
    """解析层兜底：结构与 ``PortfolioSnapshot.to_dict()`` 兼容的 fail-closed 映射。

    哈希非空且由错误信息派生，保证它既不等于"未接入组合"（空哈希），
    也不会与任何真实快照哈希相同。
    """
    reason = f"组合账本无法解析，已 fail-closed：{message}"
    digest = hashlib.sha256(f"nasdx_portfolio_link_error.v1|{message}".encode("utf-8")).hexdigest()
    return {
        "schema": "nasdx_portfolio_snapshot.v1",
        "snapshot_hash": digest,
        "portfolio_version": 0,
        "fail_closed": True,
        "cash": None,
        "cash_status": "unavailable",
        "total_assets": None,
        "exposure_pct": None,
        "positions": [],
        "industry_exposure": {},
        "asset_class_exposure": {},
        "policy": {},
        "blocking_reasons": [reason],
        "warnings": [reason],
    }


def resolve_portfolio(
    data: Mapping[str, Any] | None = None,
    db_path: str | Path | None = None,
    enabled: Optional[bool] = None,
    environ: Mapping[str, str] | None = None,
) -> Optional[PortfolioSnapshot | Dict[str, Any]]:
    """返回可直接喂给 ``analyze()`` / 闸门的组合快照，未接入账本时返回 ``None``。

    Args:
        data: 已加载的行情快照，用于估值与行业归类；缺省则持仓无价 → fail-closed。
        db_path: 覆盖账本数据库路径（测试用）。
        enabled: 显式开关，``None`` 表示按环境变量决定。
        environ: 覆盖环境（测试用）。
    """
    if not link_enabled(enabled, environ=environ):
        return None
    if not ledger_is_initialized(db_path):
        return None
    try:
        return build_snapshot(
            prices=market_price_map(data),
            industry_map=market_industry_map(data),
            db_path=db_path,
        )
    except Exception as exc:  # noqa: BLE001 - 绝不因账本异常放行新增仓位
        return _fail_closed_snapshot(str(exc))


def load_market_snapshot() -> Dict[str, Any]:
    """读取最新行情快照用于账本估值，缺文件 / 解析失败时返回 ``{}``。

    调用方（Streamlit / CLI）不应因为行情文件缺失而崩溃：拿不到价格时账本会
    按"持仓无价"走 fail-closed，这比放行新增仓位安全。
    """
    try:
        from nasdx.data_loader import load_latest_data

        data = load_latest_data()
    except Exception:  # noqa: BLE001 - 缺行情文件 / JSON 损坏都退化为空快照
        return {}
    return data if isinstance(data, Mapping) else {}  # type: ignore[return-value]


def resolve_portfolio_auto(
    data: Mapping[str, Any] | None = None,
    db_path: str | Path | None = None,
    enabled: Optional[bool] = None,
    environ: Mapping[str, str] | None = None,
) -> Optional[PortfolioSnapshot | Dict[str, Any]]:
    """给没有行情快照在手的调用方（Streamlit / 独立 CLI）用的解析入口。

    与 :func:`resolve_portfolio` 的差别只有一点：``data is None`` 时会自己去
    加载最新行情快照做估值，而不是直接按"无价"处理。账本未接入时**不加载行情**
    也**不创建数据库**，保持零成本、零副作用。
    """
    if not link_enabled(enabled, environ=environ):
        return None
    if not ledger_is_initialized(db_path):
        return None
    market = data if data is not None else load_market_snapshot()
    return resolve_portfolio(market, db_path=db_path, enabled=True, environ=environ)


def _snapshot_field(snapshot: Any, key: str, default: Any = None) -> Any:
    if isinstance(snapshot, Mapping):
        return snapshot.get(key, default)
    return getattr(snapshot, key, default)


def describe_portfolio_link(snapshot: Any) -> str:
    """给 CLI 用的一行状态说明。"""
    if snapshot is None:
        return "组合账本：未接入（按单票证据分析）"
    if _snapshot_field(snapshot, "fail_closed", False):
        reasons = _snapshot_field(snapshot, "blocking_reasons", []) or []
        first = str(reasons[0]) if reasons else "账本存在阻断项"
        return f"组合账本：已接入但 fail-closed（{first}）"
    positions = _snapshot_field(snapshot, "positions", []) or []
    cash = _snapshot_field(snapshot, "cash", None)
    cash_text = "未知" if cash is None else f"{float(cash):,.2f}"
    return (
        f"组合账本：已接入 v{_snapshot_field(snapshot, 'portfolio_version', 0)}"
        f"，持仓 {len(positions)} 只，现金 {cash_text}"
        f"，快照 {str(_snapshot_field(snapshot, 'snapshot_hash', '') or '')[:12]}"
    )

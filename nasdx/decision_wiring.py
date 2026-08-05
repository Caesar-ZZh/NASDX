"""
Production-path wiring for the #68 decision-record infrastructure (Issue #74).

This module is the *only* place that knows how to turn a finished analysis
report / intraday snapshot / portfolio candidate into a frozen
``DecisionRecord`` and persist it. Every entry point (run_analysis.py,
analyze.py, run_intraday_copilot.py, run_investment_workflow.py) calls one of
the ``record_*_if_enabled`` helpers instead of reaching into
``nasdx.decision_record`` directly.

Design rules (from #74):
* **Fail-open.** A persistence error, a missing reference price or an invalid
  plan never propagates; the helper returns ``None`` and the caller moves on.
* **Switchable.** When ``NASDX_DECISION_RECORDS=0`` the helpers are no-ops and
  never create the database file (``record_decision`` itself is also
  persistence-gated, so double protection).
* **No behaviour change when off.** Callers are wrapped in their own
  try/except; wiring can never alter the analysis result or the snapshot.
* **One schema.** All three production modes (rules / full / intraday) and the
  portfolio path produce the same ``DecisionRecord`` schema via the shared
  ``record_from_decision_plan`` / ``record_from_intraday_decision`` adapters.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping as _MappingABC
from typing import Any, Mapping, Optional

from nasdx.analysis_cache import AGENT_CONFIG_VERSION, PROMPT_SCHEMA_VERSION
from nasdx.intraday_decision import DECISION_SCHEMA as INTRADAY_SCHEMA_VERSION
from nasdx.decision_record import (
    DecisionRecord,
    _position_price,
    persistence_enabled,
    record_decision,
    record_from_decision_plan,
    record_from_intraday_decision,
)


def normalize_data_as_of(value: Any) -> str:
    """Return ``YYYY-MM-DD`` regardless of ``YYYYMMDD`` / ISO input.

    Raises ``ValueError`` when nothing usable is supplied so callers can
    degrade gracefully (a record without a valid ``data_as_of`` is useless).
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("data_as_of 为空")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    raise ValueError(f"无法解析 data_as_of: {value!r}")


def market_snapshot_hash_from_data(data: Optional[Mapping[str, Any]]) -> str:
    """Stable fingerprint of the market dataset a decision was made against.

    Same market data on the same trade date yields the same hash, so records
    from one refresh share one ``market_snapshot_hash`` for evaluation grouping.
    """
    data = data or {}
    date = str(data.get("date") or "")
    codes: list[str] = []
    for sector in data.get("sectors", []) or []:
        for stock in (sector.get("stocks", []) or []) + (sector.get("etfs", []) or []):
            code = stock.get("code")
            if code:
                codes.append(str(code))
    codes.sort()
    blob = json.dumps(
        {
            "date": date,
            "n": len(codes),
            "first": codes[0] if codes else "",
            "last": codes[-1] if codes else "",
        },
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _finite_positive(price: Any) -> Optional[float]:
    try:
        number = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def record_report_if_enabled(
    report: Any,
    *,
    reference_price: Any,
    mode: str = "full",
    data_as_of: Optional[str] = None,
    industry: str = "",
    market_snapshot_hash: str = "",
    prompt_schema_version: str = PROMPT_SCHEMA_VERSION,
    agent_config_version: str = AGENT_CONFIG_VERSION,
) -> Optional[DecisionRecord]:
    """Freeze one analysis report as a decision record (rules / full path).

    Returns the persisted ``DecisionRecord`` on success, or ``None`` when the
    switch is off, the reference price is missing/invalid, or anything raises.
    Never raises.
    """
    if not persistence_enabled():
        return None
    price = _finite_positive(reference_price)
    if price is None:
        return None
    try:
        plan = getattr(report, "decision_plan", None) or {}
        perf = getattr(report, "performance", None) or {}
        das = normalize_data_as_of(data_as_of or getattr(report, "date", ""))
        overrides = {
            "industry": industry or "",
            "market_snapshot_hash": market_snapshot_hash or "",
            "provider": str(perf.get("provider") or ""),
            "model": str(perf.get("model") or ""),
            "prompt_schema_version": prompt_schema_version,
            "agent_config_version": agent_config_version,
            # Cost side of the ledger: compare_modes ranks modes on return *and*
            # mean LLM calls, so an ablation is meaningless without them.
            "llm_calls": int(_finite_positive(perf.get("llm_call_count")) or 0),
            "latency_ms": _finite_positive(perf.get("total_elapsed_ms")) or 0.0,
        }
        record = record_from_decision_plan(
            plan, reference_price=price, data_as_of=das, mode=mode, **overrides
        )
        return record_decision(record)
    except Exception:
        return None


def _intraday_reference_price(decision: Any) -> Optional[float]:
    """Pull the last traded price out of an IntradayDecision (object or dict).

    ``snapshot["decisions"]`` holds ``IntradayDecision.to_dict()`` results, so
    both the frozen dataclass and its serialised form must work here. The field
    is ``current_price`` on ``PositionView``.
    """
    position = (
        decision.get("position")
        if isinstance(decision, _MappingABC)
        else getattr(decision, "position", None)
    )
    return _position_price(position)


# 盘中闸门是本地确定性规则引擎，没有 LLM provider/model，用固定标识占位以保证可比性。
INTRADAY_PROVIDER = "local"
INTRADAY_MODEL = "intraday_gate"


def record_intraday_if_enabled(
    decision: Any,
    *,
    mode: str = "intraday",
    market_snapshot_hash: str = "",
    provider: str = INTRADAY_PROVIDER,
    model: str = INTRADAY_MODEL,
    prompt_schema_version: str = INTRADAY_SCHEMA_VERSION,
    agent_config_version: str = AGENT_CONFIG_VERSION,
) -> Optional[DecisionRecord]:
    """Freeze one intraday decision/candidate (mode="intraday").

    ``reference_price`` is taken from the decision's ``position.current_price``;
    everything else is read by the shared adapter. Returns ``None`` on
    disabled / missing-price / error — never raises.

    The intraday gate is a deterministic local rules engine, so there is no LLM
    provider/model to report. We still fill both fields (``local`` /
    ``intraday_gate``) plus the two version fields, because #74 requires all
    three production modes to produce *comparable* records — an empty provider
    would silently drop intraday out of every mode/ablation comparison.
    """
    if not persistence_enabled():
        return None
    price = _intraday_reference_price(decision)
    overrides = {
        "market_snapshot_hash": market_snapshot_hash or "",
        "provider": provider or INTRADAY_PROVIDER,
        "model": model or INTRADAY_MODEL,
        "prompt_schema_version": prompt_schema_version or INTRADAY_SCHEMA_VERSION,
        "agent_config_version": agent_config_version or AGENT_CONFIG_VERSION,
    }
    try:
        record = record_from_intraday_decision(
            decision, reference_price=price, mode=mode, **overrides
        )
        return record_decision(record)
    except Exception:
        return None


def record_candidate_if_enabled(
    plan: Mapping[str, Any],
    *,
    reference_price: Any,
    data_as_of: Any,
    mode: str = "portfolio",
    industry: str = "",
    market_snapshot_hash: str = "",
    evaluation_class: Optional[str] = None,
) -> Optional[DecisionRecord]:
    """Freeze one portfolio-route candidate (mode default "portfolio").

    Accepts a plan-like mapping (stock_code / stock_name / action / confidence /
    entry_conditions / exit_conditions / review_triggers). ``evaluation_class``
    can be supplied outright to avoid depending on action-string matching.
    Returns ``None`` on disabled / missing-price / invalid-plan — never raises.
    """
    if not persistence_enabled():
        return None
    price = _finite_positive(reference_price)
    if price is None:
        return None
    overrides: dict[str, Any] = {
        "industry": industry or "",
        "market_snapshot_hash": market_snapshot_hash or "",
    }
    if evaluation_class:
        overrides["evaluation_class"] = evaluation_class
    try:
        record = record_from_decision_plan(
            dict(plan or {}),
            reference_price=price,
            data_as_of=normalize_data_as_of(data_as_of),
            mode=mode,
            **overrides,
        )
        return record_decision(record)
    except Exception:
        return None

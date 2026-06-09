"""
Data freshness and quality checks for investment-facing reports.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


def assess_data_quality(data: Dict[str, Any], now: datetime | None = None) -> Dict[str, Any]:
    """Assess whether the loaded market data is fresh enough for action."""
    now = now or datetime.now()
    generated_at_raw = data.get("generated_at")
    date_raw = str(data.get("date", "") or "")

    data_dt = _parse_datetime(generated_at_raw) or _parse_date(date_raw)
    age_days = None
    if data_dt:
        age_days = max(0, (now.date() - data_dt.date()).days)

    if age_days is None:
        status = "unknown"
        severity = "warning"
        message = "未找到数据生成时间，行动前必须刷新行情。"
        action_gate = "refresh_required"
    elif age_days <= 2:
        status = "fresh"
        severity = "ok"
        message = f"行情数据距今 {age_days} 天，可用于短线研究。"
        action_gate = "normal"
    elif age_days <= 5:
        status = "aging"
        severity = "warning"
        message = f"行情数据距今 {age_days} 天，建议刷新后再提高仓位。"
        action_gate = "position_cap"
    else:
        status = "stale"
        severity = "danger"
        message = f"行情数据距今 {age_days} 天，不能直接作为交易执行依据。"
        action_gate = "refresh_required"

    return {
        "status": status,
        "severity": severity,
        "action_gate": action_gate,
        "data_date": date_raw,
        "generated_at": generated_at_raw,
        "age_days": age_days,
        "message": message,
    }


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None

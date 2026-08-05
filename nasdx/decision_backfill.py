"""
Decision-outcome backfill (Issue #74).

Once ``decision_records`` exist, their forward labels (``decision_outcomes``)
must be (re)computed whenever new K-line data arrives. This module reads every
frozen record, fetches the *forward* bars for its code, and stores the labels
next to — never inside — the frozen record.

Guarantees (acceptance #4):
* **Idempotent.** Re-running recomputes the same labels and overwrites the
  outcome row; the ``decision_records`` table is never written here.
* **No look-ahead.** ``compute_forward_labels`` only considers bars strictly
  after ``data_as_of``; this module only supplies bars from ``data_as_of`` on.
* **Fail-open.** A missing price series or a bad record is counted and skipped.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from nasdx.decision_record import list_records
from nasdx.outcome_labels import LabelPolicy, label_and_store


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_only(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if " " in text:
        text = text.split(" ", 1)[0]
    elif "T" in text:
        text = text.split("T", 1)[0]
    return text[:10] if len(text) >= 10 else None


def _frame_to_bars(df: Any) -> List[Dict[str, Any]]:
    """Convert an akshare-style Chinese-column DataFrame to label-ready rows."""
    rows: List[Dict[str, Any]] = []
    try:
        items = df.iterrows()
    except AttributeError:
        return rows
    for _, row in items:
        rows.append(
            {
                "date": _date_only(row.get("日期")),
                "open": _num(row.get("开盘")),
                "high": _num(row.get("最高")),
                "low": _num(row.get("最低")),
                "close": _num(row.get("收盘")),
                "volume": _num(row.get("成交量")),
            }
        )
    return rows


def _default_price_fn(code: str, start: str, end: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch A-share daily K-line via the project's resilient market source."""
    from nasdx.market_sources import fetch_stock_hist

    try:
        df, _source = fetch_stock_hist(code, start, end, min_rows=5)
    except Exception:
        return None
    if df is None:
        return None
    return _frame_to_bars(df)


def backfill_labels(
    *,
    db_path: Any = None,
    price_fn: Optional[Callable[[str, str, str], Optional[Sequence[Mapping[str, Any]]]]] = None,
    since: Optional[str] = None,
    code: Optional[str] = None,
    benchmark: Any = None,
    policy: Optional[LabelPolicy] = None,
    today: Optional[str] = None,
) -> Dict[str, int]:
    """Recompute forward labels for every stored decision record.

    Returns a summary dict (records / labeled / skipped / errors). The frozen
    ``decision_records`` rows are read-only here; only ``decision_outcomes`` is
    updated.
    """
    fetcher = price_fn or _default_price_fn
    records = list_records(code=code, since=since, db_path=db_path)
    end = today or _dt.date.today().isoformat()
    summary = {"records": len(records), "labeled": 0, "skipped": 0, "errors": 0}
    for record in records:
        try:
            prices = fetcher(record.code, record.data_as_of_date, end)
            if not prices:
                summary["skipped"] += 1
                continue
            label_and_store(
                record, prices, benchmark=benchmark, policy=policy, db_path=db_path
            )
            summary["labeled"] += 1
        except Exception:
            summary["errors"] += 1
    return summary

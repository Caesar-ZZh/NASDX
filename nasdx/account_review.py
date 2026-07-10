"""
Account ledger review for NASDX.

This layer turns a user-provided trade ledger into a realized/unrealized PnL
review and compares open holdings with the latest NASDX investment route. It
does not infer trades from research outputs.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from nasdx.history_store import record_artifact
from nasdx.paths import get_market_data_dir, get_reports_dir
from nasdx.position_sizing import parse_percent_band


PROJECT_DIR = Path(__file__).parent.parent

COLUMN_ALIASES = {
    "date": ["date", "trade_date", "日期", "交易日期", "成交日期"],
    "code": ["code", "symbol", "ticker", "证券代码", "股票代码", "代码"],
    "name": ["name", "security_name", "证券名称", "股票名称", "名称"],
    "side": ["side", "action", "direction", "buy_sell", "买卖", "方向", "操作", "交易方向"],
    "quantity": ["quantity", "qty", "shares", "volume", "成交数量", "数量", "股数", "份额"],
    "price": ["price", "trade_price", "成交价", "成交价格", "价格", "成交均价"],
    "fee": ["fee", "commission", "fees", "手续费", "佣金", "费用"],
    "tax": ["tax", "stamp_tax", "印花税", "税费"],
    "note": ["note", "memo", "remark", "备注", "说明"],
}

REQUIRED_COLUMNS = ["date", "code", "side", "quantity", "price"]
BUY_WORDS = {"buy", "b", "long", "买", "买入", "证券买入", "买进"}
SELL_WORDS = {"sell", "s", "short", "卖", "卖出", "证券卖出", "卖掉"}
CODE_RE = re.compile(r"(\d{6})")


def build_account_review(
    ledger_path: str | Path | None = None,
    total_capital: float | None = None,
    reports_dir: str | Path | None = None,
    project_dir: str | Path | None = None,
) -> Dict[str, Any]:
    """Build a review from a CSV ledger path."""
    if not ledger_path:
        return _missing_review("缺少成交流水 CSV，无法计算真实账户收益。")

    path = Path(ledger_path)
    if not path.exists():
        return _missing_review(f"成交流水文件不存在：{path}")

    text = _read_csv_text(path)
    return build_account_review_from_text(
        text,
        source_name=str(path),
        total_capital=total_capital,
        reports_dir=reports_dir,
        project_dir=project_dir,
    )


def build_account_review_from_text(
    csv_text: str,
    source_name: str = "<uploaded>",
    total_capital: float | None = None,
    reports_dir: str | Path | None = None,
    project_dir: str | Path | None = None,
) -> Dict[str, Any]:
    """Build a review from CSV text without saving the raw ledger."""
    trades, parse_warnings = parse_trade_ledger_text(csv_text, source_name=source_name)
    if not trades:
        review = _missing_review("成交流水为空或未能解析有效交易。")
        review["parse_warnings"] = parse_warnings
        review["markdown"] = format_account_review(review)
        return review

    root = Path(project_dir) if project_dir else PROJECT_DIR
    reports = Path(reports_dir) if reports_dir else (root / "reports" if project_dir else get_reports_dir())
    brief = _load_json(reports / "investment_brief_latest.json")
    market_map = _market_map(root if project_dir else get_market_data_dir())
    scan_map = _scan_map(reports)
    candidate_map = _candidate_map(brief)

    holdings, closed_positions, aggregate_warnings = _aggregate_trades(trades)
    holding_rows = [
        _holding_review(row, market_map, scan_map, candidate_map)
        for row in sorted(holdings, key=lambda item: item.get("code", ""))
    ]
    closed_rows = sorted(closed_positions, key=lambda item: item.get("code", ""))
    summary = _summary(holding_rows, closed_rows, trades, total_capital, brief)
    actions = _next_actions(holding_rows, summary, parse_warnings + aggregate_warnings, brief)

    review = {
        "schema": "nasdx_account_review.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "review_status": "reviewed",
        "ledger_source": source_name,
        "trade_count": len(trades),
        "source_brief_generated_at": brief.get("generated_at"),
        "risk_profile": brief.get("risk_profile"),
        "action_gate": brief.get("action_gate"),
        "posture": brief.get("posture"),
        "summary": summary,
        "holdings": holding_rows,
        "closed_positions": closed_rows,
        "warnings": parse_warnings + aggregate_warnings,
        "next_actions": actions,
        "assumptions": [
            "真实收益只来自用户导入的成交流水；研究简报和行情信号不会被当作成交。",
            "卖出收益按移动平均成本估算，若账户券商使用不同成本法，需以券商账单为准。",
            "未导入现金流水、申赎、分红、融资融券或转托管记录时，现金和收益可能不完整。",
            "本模块不保存原始成交明细；保存的 JSON/Markdown 是派生复盘结果。",
        ],
        "disclaimer": "账户复盘仍是研究辅助和纪律检查，不构成投资建议、收益承诺或下单指令。",
    }
    review["markdown"] = format_account_review(review)
    return review


def parse_trade_ledger_text(csv_text: str, source_name: str = "<uploaded>") -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse CSV text into normalized trade rows."""
    warnings: List[str] = []
    reader = csv.DictReader(StringIO(csv_text))
    if not reader.fieldnames:
        return [], [f"{source_name}: 未识别到 CSV 表头"]

    field_map = _field_map(reader.fieldnames)
    missing = [field for field in REQUIRED_COLUMNS if field not in field_map]
    if missing:
        aliases = "；".join(f"{field}={','.join(COLUMN_ALIASES[field][:3])}" for field in missing)
        return [], [f"{source_name}: 缺少必要列 {', '.join(missing)}；可用列名示例：{aliases}"]

    trades: List[Dict[str, Any]] = []
    for line_no, raw in enumerate(reader, start=2):
        try:
            trade = _normalize_trade(raw, field_map)
        except ValueError as exc:
            warnings.append(f"{source_name}:{line_no}: {exc}")
            continue
        if trade["quantity"] <= 0 or trade["price"] <= 0:
            warnings.append(f"{source_name}:{line_no}: 数量或价格必须大于 0")
            continue
        trades.append(trade)
    trades.sort(key=lambda item: (str(item.get("date", "")), str(item.get("code", ""))))
    return trades, warnings


def save_account_review(review: Dict[str, Any], output_dir: str | Path | None = None) -> Dict[str, str]:
    """Save derived account review Markdown/JSON files."""
    out_dir = Path(output_dir) if output_dir else get_reports_dir(create=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    md_path = out_dir / f"account_review_{stamp}.md"
    json_path = out_dir / f"account_review_{stamp}.json"
    latest_md = out_dir / "account_review_latest.md"
    latest_json = out_dir / "account_review_latest.json"
    payload = {key: value for key, value in review.items() if key != "markdown"}

    md_path.write_text(review.get("markdown") or format_account_review(review), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_md.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
    latest_json.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    record_artifact(
        "account_review",
        "latest",
        payload,
        generated_at=payload.get("generated_at"),
        source_path=json_path,
    )
    return {
        "markdown": str(md_path),
        "json": str(json_path),
        "latest_markdown": str(latest_md),
        "latest_json": str(latest_json),
    }


def build_and_save_account_review(
    ledger_path: str | Path,
    total_capital: float | None = None,
    output_dir: str | Path | None = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Build and save a derived account review."""
    review = build_account_review(ledger_path=ledger_path, total_capital=total_capital)
    return review, save_account_review(review, output_dir=output_dir)


def format_account_review(review: Dict[str, Any]) -> str:
    """Render account review as Markdown."""
    if review.get("review_status") != "reviewed":
        return "\n".join(
            [
                "# NASDX 真实账户复盘",
                "",
                f"- 状态：{review.get('message', '缺账户流水')}",
                "- 必要列：date/code/side/quantity/price，或中文列：日期/代码/方向/数量/成交价。",
                "",
                "> 真实收益必须来自成交流水，不能由研究简报或行情信号倒推。",
            ]
        )

    summary = review.get("summary", {})
    lines = [
        "# NASDX 真实账户复盘",
        "",
        f"- 生成时间：{review.get('generated_at', '')}",
        f"- 成交流水：{review.get('ledger_source', '')}",
        f"- 交易笔数：{review.get('trade_count', 0)}",
        f"- 行动闸门：{review.get('action_gate', '')}",
        f"- 持仓市值：{_fmt_money(summary.get('known_market_value'))}",
        f"- 已实现盈亏：{_fmt_money(summary.get('realized_pnl'))}",
        f"- 浮动盈亏：{_fmt_money(summary.get('unrealized_pnl'))}",
        f"- 已知总盈亏：{_fmt_money(summary.get('total_pnl'))}",
        f"- 仓位占比：{_fmt_pct(summary.get('exposure_pct'))}",
        "",
        "## 当前持仓",
        "",
        _format_holding_table(review.get("holdings", [])),
        "",
        "## 已清仓/已卖出",
        "",
        _format_closed_table(review.get("closed_positions", [])),
        "",
        "## 下一步",
        "",
        *[f"- {item}" for item in review.get("next_actions", [])],
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in review.get("assumptions", [])],
        "",
        f"> {review.get('disclaimer', '')}",
    ]
    return "\n".join(lines)


def dumps_account_review(review: Dict[str, Any]) -> str:
    """Serialize review with UTF-8 friendly JSON formatting."""
    return json.dumps({key: value for key, value in review.items() if key != "markdown"}, ensure_ascii=False, indent=2)


def template_csv() -> str:
    """Return a minimal UTF-8 CSV template."""
    return "\n".join(
        [
            "date,code,name,side,quantity,price,fee,tax,note",
            "2026-06-12,512890,红利低波ETF华泰柏瑞,buy,1000,1.000,1.00,0,示例",
        ]
    )


def _missing_review(message: str) -> Dict[str, Any]:
    review = {
        "schema": "nasdx_account_review.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "review_status": "missing_ledger",
        "message": message,
        "required_columns": REQUIRED_COLUMNS,
        "column_aliases": COLUMN_ALIASES,
        "template_rows": [template_csv().splitlines()[1]],
        "next_actions": ["导入成交流水后再计算真实收益、持仓敞口和路线匹配。"],
        "disclaimer": "没有成交流水时不能计算真实账户收益。",
    }
    review["markdown"] = format_account_review(review)
    return review


def _read_csv_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _field_map(fieldnames: Iterable[str]) -> Dict[str, str]:
    normalized = {_norm_header(name): name for name in fieldnames if name}
    result: Dict[str, str] = {}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = _norm_header(alias)
            if key in normalized:
                result[target] = normalized[key]
                break
    return result


def _normalize_trade(raw: Dict[str, Any], field_map: Dict[str, str]) -> Dict[str, Any]:
    side_raw = _value(raw, field_map, "side")
    quantity = _as_float(_value(raw, field_map, "quantity"))
    side = _normalize_side(side_raw, quantity)
    code = _normalize_code(_value(raw, field_map, "code"))
    if not code:
        raise ValueError("代码为空或无法识别")
    return {
        "date": str(_value(raw, field_map, "date")).strip(),
        "code": code,
        "name": str(_value(raw, field_map, "name") or "").strip(),
        "side": side,
        "quantity": abs(quantity),
        "price": abs(_as_float(_value(raw, field_map, "price"))),
        "fee": abs(_as_float(_value(raw, field_map, "fee"))),
        "tax": abs(_as_float(_value(raw, field_map, "tax"))),
        "note": str(_value(raw, field_map, "note") or "").strip(),
    }


def _aggregate_trades(trades: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    positions: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []
    for trade in trades:
        code = trade["code"]
        pos = positions.setdefault(
            code,
            {
                "code": code,
                "name": trade.get("name", ""),
                "quantity": 0.0,
                "cost_basis": 0.0,
                "gross_buy": 0.0,
                "gross_sell": 0.0,
                "fees": 0.0,
                "tax": 0.0,
                "realized_pnl": 0.0,
                "buy_qty": 0.0,
                "sell_qty": 0.0,
                "last_trade_date": "",
            },
        )
        if trade.get("name") and not pos.get("name"):
            pos["name"] = trade["name"]
        pos["fees"] += trade.get("fee", 0.0)
        pos["tax"] += trade.get("tax", 0.0)
        pos["last_trade_date"] = str(trade.get("date") or pos.get("last_trade_date") or "")
        qty = float(trade["quantity"])
        price = float(trade["price"])
        cost = qty * price
        charges = float(trade.get("fee", 0.0)) + float(trade.get("tax", 0.0))
        if trade["side"] == "buy":
            pos["quantity"] += qty
            pos["cost_basis"] += cost + charges
            pos["gross_buy"] += cost
            pos["buy_qty"] += qty
            continue

        pos["gross_sell"] += cost
        pos["sell_qty"] += qty
        held_qty = float(pos["quantity"])
        if held_qty <= 0:
            warnings.append(f"{code}: 出现无持仓卖出记录，无法准确计算该笔成本。")
            pos["realized_pnl"] += cost - charges
            continue
        matched_qty = min(qty, held_qty)
        avg_cost = float(pos["cost_basis"]) / held_qty if held_qty else 0.0
        sold_cost = avg_cost * matched_qty
        matched_proceeds = matched_qty * price - charges
        pos["realized_pnl"] += matched_proceeds - sold_cost
        pos["quantity"] = max(held_qty - matched_qty, 0.0)
        pos["cost_basis"] = max(float(pos["cost_basis"]) - sold_cost, 0.0)
        if qty > held_qty:
            warnings.append(f"{code}: 卖出数量 {qty:g} 超过已记录持仓 {held_qty:g}，超出部分未纳入成本。")

    open_positions = []
    closed_positions = []
    for pos in positions.values():
        qty = float(pos.get("quantity", 0.0))
        row = _rounded_position(pos)
        if qty > 0.000001:
            open_positions.append(row)
        else:
            row["quantity"] = 0.0
            row["avg_cost"] = 0.0
            closed_positions.append(row)
    return open_positions, closed_positions, warnings


def _holding_review(
    row: Dict[str, Any],
    market_map: Dict[str, Dict[str, Any]],
    scan_map: Dict[str, Dict[str, Any]],
    candidate_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    code = row.get("code", "")
    market = market_map.get(code, {})
    scan = scan_map.get(code, {})
    candidate = candidate_map.get(code, {})
    latest_price = _first_float(scan.get("spot_price"), scan.get("close"), market.get("close"))
    quantity = float(row.get("quantity", 0.0))
    cost_basis = float(row.get("cost_basis", 0.0))
    market_value = latest_price * quantity if latest_price is not None else None
    unrealized = market_value - cost_basis if market_value is not None else None
    route_status, route_action = _route_status(candidate)
    return {
        **row,
        "latest_price": _round(latest_price),
        "market_value": _round(market_value),
        "unrealized_pnl": _round(unrealized),
        "unrealized_pct": _round((unrealized / cost_basis * 100) if unrealized is not None and cost_basis else None),
        "data_date": scan.get("data_date") or market.get("data_date") or "",
        "scan_signal": scan.get("signal") or "",
        "scan_score": _first_float(scan.get("score"), scan.get("adjusted_score"), scan.get("quant_score")),
        "route_status": route_status,
        "route_audit": candidate.get("audit_status", ""),
        "route_action": route_action,
    }


def _summary(
    holdings: List[Dict[str, Any]],
    closed: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    total_capital: float | None,
    brief: Dict[str, Any],
) -> Dict[str, Any]:
    realized = sum(float(row.get("realized_pnl") or 0.0) for row in holdings + closed)
    known_market_value = sum(float(row.get("market_value") or 0.0) for row in holdings if row.get("market_value") is not None)
    missing_value_count = sum(1 for row in holdings if row.get("market_value") is None)
    unrealized = sum(float(row.get("unrealized_pnl") or 0.0) for row in holdings if row.get("unrealized_pnl") is not None)
    fees = sum(float(row.get("fee") or 0.0) for row in trades)
    taxes = sum(float(row.get("tax") or 0.0) for row in trades)
    gross_buy = sum(float(row.get("quantity") or 0.0) * float(row.get("price") or 0.0) for row in trades if row.get("side") == "buy")
    gross_sell = sum(float(row.get("quantity") or 0.0) * float(row.get("price") or 0.0) for row in trades if row.get("side") == "sell")
    capital = _positive_or_none(total_capital)
    exposure_pct = known_market_value / capital * 100 if capital else None
    max_total = parse_percent_band((brief.get("allocation") or {}).get("max_total"))[1] * 100 if brief else 0.0
    return {
        "open_position_count": len(holdings),
        "closed_position_count": len(closed),
        "realized_pnl": _round(realized),
        "unrealized_pnl": _round(unrealized),
        "known_market_value": _round(known_market_value),
        "missing_market_value_count": missing_value_count,
        "total_pnl": _round(realized + unrealized),
        "gross_buy": _round(gross_buy),
        "gross_sell": _round(gross_sell),
        "fees": _round(fees),
        "tax": _round(taxes),
        "total_capital": _round(capital),
        "exposure_pct": _round(exposure_pct),
        "route_max_total_pct": _round(max_total),
        "exposure_status": _exposure_status(exposure_pct, max_total),
    }


def _next_actions(
    holdings: List[Dict[str, Any]],
    summary: Dict[str, Any],
    warnings: List[str],
    brief: Dict[str, Any],
) -> List[str]:
    actions: List[str] = []
    if warnings:
        actions.append("先修正无法解析、超卖或缺字段的成交记录，再以券商账单校验结果。")
    missing_price = [row for row in holdings if row.get("market_value") is None]
    if missing_price:
        actions.append("部分持仓缺最新行情，先刷新行情和扫描后再确认浮动盈亏。")
    off_route = [row for row in holdings if row.get("route_status") == "not_in_current_route"]
    if off_route:
        actions.append(f"{_name_list(off_route[:3])} 不在当前投资路线中，单独复核继续持有理由。")
    blocked = [
        row
        for row in holdings
        if row.get("route_status") in {"needs_report", "watch", "avoid", "refresh_data"}
    ]
    if blocked:
        actions.append(f"{_name_list(blocked[:3])} 未通过当前路线试错条件，先暂停新增并补复核。")
    if summary.get("exposure_status") == "over_route_limit":
        actions.append("当前已知仓位超过路线总仓位上限，优先评估降仓或停止新增。")
    if brief.get("action_gate") != "normal":
        actions.append("当前行动闸门未打开，账户复盘只能用于减法管理和风险控制。")
    if not actions:
        actions.append("账户流水、行情和当前路线未发现硬阻断，继续按执行队列分批复核。")
    actions.append("真实收益结论以券商账单为准；本页只做研究路线和账户纪律交叉检查。")
    return actions[:6]


def _market_map(project_dir: Path) -> Dict[str, Dict[str, Any]]:
    files = sorted(project_dir.glob("stock_data_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    data = _load_json(files[0]) if files else {}
    result: Dict[str, Dict[str, Any]] = {}
    for sector in data.get("sectors", []):
        for group in ("stocks", "etfs"):
            for item in sector.get(group, []) or []:
                code = _normalize_code(item.get("code"))
                indicators = item.get("indicators") or {}
                if code:
                    result[code] = {
                        "close": indicators.get("close"),
                        "change_pct": indicators.get("change_pct"),
                        "data_date": item.get("data_date") or data.get("date"),
                    }
    return result


def _scan_map(reports_dir: Path) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for pattern in ("etf50_[0-9]*_[0-9]*.json", "stocks60_*.json"):
        files = sorted(reports_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
        data = _load_json(files[0]) if files else {}
        rows = data.get("results") or data.get("top3") or []
        for row in rows if isinstance(rows, list) else []:
            code = _normalize_code(row.get("code"))
            if code:
                result[code] = dict(row)
    return result


def _candidate_map(brief: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in brief.get("candidate_audits", []) if isinstance(brief, dict) else []:
        if not isinstance(item, dict):
            continue
        code = _normalize_code(item.get("code")) or _normalize_code(str(item.get("candidate", "")).split(" ", 1)[0])
        if code:
            result[code] = item
    return result


def _route_status(candidate: Dict[str, Any]) -> Tuple[str, str]:
    if not candidate:
        return "not_in_current_route", "不在当前最终简报候选中，单独复核持有理由。"
    status = str(candidate.get("status_code") or "")
    if status == "trial_candidate":
        return "trial_candidate", "属于当前小仓试错候选，仍按仓位换算和人工复核执行。"
    if status == "needs_report":
        return "needs_report", "当前缺深度报告，先补报告再决定是否继续持有或新增。"
    if status == "watch":
        return "watch", "当前只观察，不放大仓位。"
    if status == "avoid":
        return "avoid", "当前路线要求回避或降级，优先复核减仓。"
    if status == "refresh_data":
        return "refresh_data", "当前数据闸门关闭，先刷新数据。"
    return status or "candidate", "属于当前候选，但状态需人工复核。"


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _rounded_position(pos: Dict[str, Any]) -> Dict[str, Any]:
    qty = float(pos.get("quantity", 0.0))
    cost_basis = float(pos.get("cost_basis", 0.0))
    return {
        "code": pos.get("code", ""),
        "name": pos.get("name", ""),
        "quantity": _round(qty),
        "avg_cost": _round(cost_basis / qty if qty else 0.0),
        "cost_basis": _round(cost_basis),
        "gross_buy": _round(pos.get("gross_buy")),
        "gross_sell": _round(pos.get("gross_sell")),
        "fees": _round(pos.get("fees")),
        "tax": _round(pos.get("tax")),
        "realized_pnl": _round(pos.get("realized_pnl")),
        "buy_qty": _round(pos.get("buy_qty")),
        "sell_qty": _round(pos.get("sell_qty")),
        "last_trade_date": pos.get("last_trade_date", ""),
    }


def _format_holding_table(rows: Iterable[Dict[str, Any]]) -> str:
    rows = list(rows)
    if not rows:
        return "暂无持仓。"
    lines = [
        "| 标的 | 数量 | 成本 | 最新价 | 市值 | 浮盈亏 | 路线状态 | 动作 |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {name} | {qty} | {avg} | {price} | {value} | {pnl} | {status} | {action} |".format(
                name=_safe(f"{row.get('code', '')} {row.get('name', '')}".strip()),
                qty=_fmt_num(row.get("quantity")),
                avg=_fmt_money(row.get("avg_cost")),
                price=_fmt_money(row.get("latest_price")),
                value=_fmt_money(row.get("market_value")),
                pnl=_fmt_money(row.get("unrealized_pnl")),
                status=_safe(row.get("route_audit") or row.get("route_status")),
                action=_safe(row.get("route_action")),
            )
        )
    return "\n".join(lines)


def _format_closed_table(rows: Iterable[Dict[str, Any]]) -> str:
    rows = list(rows)
    if not rows:
        return "暂无已清仓或已卖出记录。"
    lines = [
        "| 标的 | 买入额 | 卖出额 | 已实现盈亏 | 费用 | 最后交易日 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {name} | {buy} | {sell} | {pnl} | {fees} | {date} |".format(
                name=_safe(f"{row.get('code', '')} {row.get('name', '')}".strip()),
                buy=_fmt_money(row.get("gross_buy")),
                sell=_fmt_money(row.get("gross_sell")),
                pnl=_fmt_money(row.get("realized_pnl")),
                fees=_fmt_money(row.get("fees")),
                date=_safe(row.get("last_trade_date")),
            )
        )
    return "\n".join(lines)


def _normalize_side(value: Any, quantity: float) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    if text in BUY_WORDS:
        return "buy"
    if text in SELL_WORDS:
        return "sell"
    if quantity < 0:
        return "sell"
    raise ValueError(f"无法识别买卖方向：{value}")


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    match = CODE_RE.search(text)
    if match:
        return match.group(1)
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return ""


def _value(row: Dict[str, Any], field_map: Dict[str, str], field: str) -> Any:
    source = field_map.get(field)
    return row.get(source, "") if source else ""


def _norm_header(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "")


def _as_float(value: Any) -> float:
    text = str(value if value is not None else "").strip()
    if not text:
        return 0.0
    text = text.replace(",", "").replace("￥", "").replace("元", "").replace("%", "")
    text = text.replace("（", "(").replace("）", ")")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return -number if negative else number


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _positive_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _exposure_status(exposure_pct: float | None, max_total_pct: float) -> str:
    if exposure_pct is None:
        return "capital_missing"
    if max_total_pct and exposure_pct > max_total_pct:
        return "over_route_limit"
    return "within_route_limit"


def _round(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _fmt_money(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "NA"


def _fmt_num(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "NA"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "NA"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "NA"


def _name_list(rows: List[Dict[str, Any]]) -> str:
    return "、".join(
        f"{row.get('code', '')} {row.get('name', '')}".strip()
        for row in rows
        if row
    )


def _safe(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/")

#!/usr/bin/env python3
"""
run_stock_selector.py — 动态选股引擎主入口

用法：
    python run_stock_selector.py                  # 默认：全 A 选股 + 输出报告
    python run_stock_selector.py --top 30         # 只取 Top 30
    python run_stock_selector.py --limit 300      # 全 A 最多抓取 300 只
    python run_stock_selector.py --top 30 --html  # 生成 HTML 报告

输出：
    reports/stock_selector_latest.json   — 结构化数据
    reports/stock_selector_latest.md     — Markdown 报告
    reports/stock_selector_latest.html   — HTML 报告（暗色主题）
"""
from __future__ import annotations

# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# 确保项目根目录在 path 中
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nasdx.selector.universe import (
    filter_universe,
    get_universe_coverage,
    load_full_a_stocks,
)
from nasdx.selector.market_regime import assess_market_regime
from nasdx.selector.factors import compute_factors_for_stocks
from nasdx.selector.scoring import compute_all_scores
from nasdx.selector.risk_filter import risk_filter
from nasdx.selector.watchlist import generate_watchlist
from nasdx.data_quality import assess_data_quality
from nasdx.paths import get_reports_dir


def run_selector(
    top_n: int = 30,
    max_fetch: int = 200,
    output_dir: str | None = None,
) -> Dict[str, Any]:
    """
    执行完整的选股引擎流程。

    Args:
        top_n: 输出候选股数量
        max_fetch: 全 A 最大抓取数量（限速保护）
        output_dir: 输出目录（默认 reports/）

    Returns:
        选股结果字典，可直接序列化为 JSON
    """
    if output_dir is None:
        output_dir = get_reports_dir(create=True)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n{'='*60}")
    print(f"  NASDX 动态选股引擎  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # ── Step 1: 加载全 A 股票池并做实时预筛 ─────────────
    print("[1/6] 加载全 A 实时股票池...")
    t0 = time.time()
    all_stocks = load_full_a_stocks()
    universe_coverage = get_universe_coverage()
    print(f"  原始股票数：{len(all_stocks)}")
    listed_counts = universe_coverage.get("counts", {})
    quoted_counts = universe_coverage.get("quoted_counts", {})
    print(
        "  交易所覆盖："
        + " / ".join(
            f"{exchange} {quoted_counts.get(exchange, 0)}/{listed_counts.get(exchange, 0)}"
            for exchange in ("SSE", "SZSE", "BSE")
        )
    )
    if not universe_coverage.get("complete"):
        missing = universe_coverage.get("unavailable_exchanges", [])
        quote_missing = universe_coverage.get("quote_unavailable_exchanges", [])
        print(f"  警告：股票池覆盖不完整（列表缺失 {missing or '无'}；行情缺失 {quote_missing or '无'}）")
    eligible_stocks = filter_universe(
        all_stocks,
        min_amount=3e7,   # 3000 万成交额
        min_price=2.0,
        max_price=200.0,
        exclude_st=True,
        exclude_kcb=True,  # 科创板资金流数据不完整
    )
    filtered_stocks = sorted(
        eligible_stocks,
        key=lambda stock: (
            float(stock.get("amount", 0) or 0) >= 3e8,
            -abs(float(stock.get("change_pct", 0) or 0) - 2.0),
            float(stock.get("amount", 0) or 0),
        ),
        reverse=True,
    )[:max_fetch]
    print(f"  过滤后：{len(eligible_stocks)} 只；实时预筛 {len(filtered_stocks)} 只进入历史因子")
    print(f"  耗时：{time.time()-t0:.1f}s\n")

    # ── Step 2: 市场环境判断 ─────────────────────────────
    print("[2/6] 判断市场环境...")
    t0 = time.time()
    market = assess_market_regime(all_stocks)
    print(f"  市场状态：{market['regime']}（综合分 {market['score']}/100）")
    print(f"  耗时：{time.time()-t0:.1f}s\n")

    # ── Step 3: 计算因子 ───────────────────────────────
    print(f"[3/6] 计算 {len(filtered_stocks)} 只股票的技术因子...")
    t0 = time.time()
    stocks_with_factors = compute_factors_for_stocks(filtered_stocks)
    print(f"  因子计算完成，耗时：{time.time()-t0:.1f}s\n")

    # ── Step 4: 综合评分 ────────────────────────────────
    print("[4/6] 综合评分...")
    t0 = time.time()
    scored_stocks = compute_all_scores(stocks_with_factors)
    print(f"  最高分：{scored_stocks[0]['final_score'] if scored_stocks else 0}")
    print(f"  最低分：{scored_stocks[-1]['final_score'] if scored_stocks else 0}")
    print(f"  耗时：{time.time()-t0:.1f}s\n")

    # ── Step 5: 风险过滤 ───────────────────────────────
    print("[5/6] 风险过滤...")
    t0 = time.time()
    risk_result = risk_filter(scored_stocks)
    passed = risk_result["passed"]
    filtered_out = risk_result["filtered"]
    print(f"  通过：{len(passed)} 只")
    print(f"  剔除：{len(filtered_out)} 只")
    if filtered_out:
        reason_counts: Dict[str, int] = {}
        for s in filtered_out:
            for r in s.get("risk_reasons", []):
                reason_counts[r] = reason_counts.get(r, 0) + 1
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])[:5]:
            print(f"    - {reason}：{count} 只")
    print(f"  耗时：{time.time()-t0:.1f}s\n")

    # ── Step 6: 生成观察池 ─────────────────────────────
    print("[6/6] 分类观察池...")
    t0 = time.time()
    watchlist = generate_watchlist(passed, n_a=top_n, n_b=top_n * 2)
    print(f"  A 级候选：{len(watchlist['tier_a'])} 只")
    print(f"  B 级候选：{len(watchlist['tier_b'])} 只")
    print(f"  回踩候选：{len(watchlist['pullback'])} 只")
    print(f"  突破候选：{len(watchlist['breakout'])} 只")
    print(f"  回避池：{len(watchlist['avoid'])} 只")
    print(f"  耗时：{time.time()-t0:.1f}s\n")

    # ── 组装输出 ────────────────────────────────────────
    all_passed = passed[:top_n * 5]  # 多取一些用于分类

    # 数据新鲜度检查
    data_quality = assess_data_quality(
        {"generated_at": datetime.now().isoformat()},
        now=datetime.now(),
    )

    output = {
        "generated_at": datetime.now().isoformat(),
        "date": datetime.now().strftime("%Y%m%d"),
        "market_regime": market,
        "data_quality": data_quality,
        "universe_coverage": universe_coverage,
        "summary": {
            "total_stocks": len(all_stocks),
            "after_filter": len(filtered_stocks),
            "after_risk_filter": len(passed),
            "tier_a": len(watchlist["tier_a"]),
            "tier_b": len(watchlist["tier_b"]),
            "pullback": len(watchlist["pullback"]),
            "breakout": len(watchlist["breakout"]),
            "avoid": len(watchlist["avoid"]),
        },
        "candidates": {
            "tier_a": [
                _format_stock(s) for s in watchlist.get("tier_a", [])
            ],
            "tier_b": [
                _format_stock(s) for s in watchlist.get("tier_b", [])
            ],
            "pullback": [
                _format_stock(s) for s in watchlist.get("pullback", [])
            ],
            "breakout": [
                _format_stock(s) for s in watchlist.get("breakout", [])
            ],
            "avoid": [
                _format_stock(s) for s in watchlist.get("avoid", [])
            ],
        },
    }

    # ── 保存输出 ────────────────────────────────────────
    json_path = out / f"stock_selector_{timestamp}.json"
    json_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 最新文件（覆盖写）
    latest_json = out / "stock_selector_latest.json"
    latest_json.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Markdown 报告
    md_path = out / f"stock_selector_{timestamp}.md"
    md_text = _format_markdown(output)
    md_path.write_text(md_text, encoding="utf-8")

    latest_md = out / "stock_selector_latest.md"
    latest_md.write_text(md_text, encoding="utf-8")

    # HTML 报告
    html_path = out / f"stock_selector_{timestamp}.html"
    html_text = _format_html(output)
    html_path.write_text(html_text, encoding="utf-8")

    latest_html = out / "stock_selector_latest.html"
    latest_html.write_text(html_text, encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  选股完成！")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
    print(f"  HTML: {html_path}")
    print(f"  最新: {latest_json} / {latest_md} / {latest_html}")
    print(f"{'='*60}\n")

    return output


def _format_stock(s: Dict[str, Any]) -> Dict[str, Any]:
    """格式化单只股票输出字段。"""
    return {
        "code": s.get("code", ""),
        "name": s.get("name", ""),
        "exchange": s.get("exchange", ""),
        "data_source": s.get("data_source", ""),
        "close": s.get("close", 0),
        "change_pct": s.get("change_pct", 0),
        "amount": round(s.get("amount", 0) / 1e8, 2),  # 转为亿
        "turnover": s.get("turnover", 0),
        "ma5": s.get("ma5", 0),
        "ma10": s.get("ma10", 0),
        "ma20": s.get("ma20", 0),
        "ma60": s.get("ma60", 0),
        "rsi": s.get("rsi", 0),
        "macd_bar": s.get("macd_bar", 0),
        "vol_ratio": s.get("vol_ratio", 1),
        "technical_score": s.get("technical_score", 0),
        "sector_score": s.get("sector_score", 0),
        "momentum_score": s.get("momentum_score", 0),
        "liquidity_score": s.get("liquidity_score", 0),
        "risk_score": s.get("risk_score", 0),
        "final_score": s.get("final_score", 0),
        "candidate_type": s.get("candidate_type", ""),
        "entry_condition": s.get("entry_condition", ""),
        "risk_condition": s.get("risk_condition", ""),
        "action_level": s.get("action_level", ""),
    }


def _format_markdown(output: Dict[str, Any]) -> str:
    """生成 Markdown 报告。"""
    coverage = output.get("universe_coverage", {})
    counts = coverage.get("counts", {})
    quoted = coverage.get("quoted_counts", {})
    coverage_status = "完整" if coverage.get("complete") else "不完整"
    lines = [
        "# NASDX 动态选股报告",
        "",
        f"- 生成时间：{output.get('generated_at', '')}",
        f"- 数据日期：{output.get('date', '')}",
        f"- 股票池覆盖：{coverage_status}（SSE {quoted.get('SSE', 0)}/{counts.get('SSE', 0)}，SZSE {quoted.get('SZSE', 0)}/{counts.get('SZSE', 0)}，BSE {quoted.get('BSE', 0)}/{counts.get('BSE', 0)}）",
        "",
        "## 市场环境",
        "",
        f"- 状态：{output['market_regime']['regime']}",
        f"- 综合分：{output['market_regime']['score']}/100",
        f"- 描述：{output['market_regime']['summary']}",
        "",
        "## 选股统计",
        "",
        f"| 指标 | 数量 |",
        f"|---|---|",
        f"| 原始全 A | {output['summary']['total_stocks']} |",
        f"| 过滤后 | {output['summary']['after_filter']} |",
        f"| 风险过滤后 | {output['summary']['after_risk_filter']} |",
        "",
        "## A 级候选",
        "",
        _md_table(output["candidates"]["tier_a"], [
            "code", "name", "close", "chg", "amount", "final_score",
            "technical_score", "momentum_score", "risk_score", "type", "entry", "action",
        ]),
        "",
        "## B 级候选",
        "",
        _md_table(output["candidates"]["tier_b"][:15], [
            "code", "name", "close", "chg", "amount", "final_score",
            "technical_score", "momentum_score", "risk_score", "type", "entry", "action",
        ]),
        "",
        "## 回踩候选",
        "",
        _md_table(output["candidates"]["pullback"][:10], [
            "code", "name", "close", "chg", "final_score", "type", "entry", "action",
        ]),
        "",
        "## 突破候选",
        "",
        _md_table(output["candidates"]["breakout"][:10], [
            "code", "name", "close", "chg", "final_score", "type", "entry", "action",
        ]),
        "",
        "## 回避池",
        "",
        _md_table(output["candidates"]["avoid"][:10], [
            "code", "name", "close", "chg", "final_score", "risk_condition", "action",
        ]),
        "",
        "> 本报告由 NASDX 动态选股引擎自动生成，仅供学习研究，不构成投资建议。",
    ]
    return "\n".join(lines)


def _md_table(rows: List[Dict], cols: List[str]) -> str:
    """生成 Markdown 表格。"""
    if not rows:
        return "暂无数据。"

    header = "| " + " | ".join({
        "code": "代码",
        "name": "名称",
        "close": "收盘价",
        "chg": "涨跌幅",
        "amount": "成交额(亿)",
        "final_score": "综合分",
        "technical_score": "技术分",
        "sector_score": "板块分",
        "momentum_score": "动量分",
        "liquidity_score": "流动性分",
        "risk_score": "风险分",
        "type": "类型",
        "entry": "入场条件",
        "risk_condition": "风险条件",
        "action": "操作",
    }.get(c, c) for c in cols)
    sep = "| " + " | ".join("---" for _ in cols)

    cell_map = {
        "code": "{code}",
        "name": "{name}",
        "close": "{close:.2f}",
        "chg": "{change_pct:+.2f}%",
        "amount": "{amount:.2f}",
        "turnover": "{turnover:.2f}%",
        "final_score": "{final_score:.0f}",
        "technical_score": "{technical_score:.0f}",
        "sector_score": "{sector_score:.0f}",
        "momentum_score": "{momentum_score:.0f}",
        "liquidity_score": "{liquidity_score:.0f}",
        "risk_score": "{risk_score:.0f}",
        "type": "{candidate_type}",
        "entry": "{entry_condition}",
        "risk_condition": "{risk_condition}",
        "action": "{action_level}",
    }

    body = ""
    for r in rows:
        cells = []
        for c in cols:
            fmt = cell_map.get(c, "{}")
            try:
                cells.append(fmt.format(r))
            except Exception:
                cells.append(str(r.get(c, "-")))
        body += "| " + " | ".join(cells) + "\n"

    return header + "\n" + sep + "\n" + body


def _format_html(output: Dict[str, Any]) -> str:
    """生成暗色主题 HTML 报告。"""
    regime = output.get("market_regime", {})
    summary = output.get("summary", {})
    candidates = output.get("candidates", {})
    coverage = output.get("universe_coverage", {})
    coverage_counts = coverage.get("counts", {})
    quoted_counts = coverage.get("quoted_counts", {})
    coverage_status = "完整" if coverage.get("complete") else "不完整"

    def _render_table(title: str, rows: List[Dict], show_cols: List[str]) -> str:
        if not rows:
            return f'<div class="section"><h2>{title}</h2><p style="color:#8b949e">暂无数据</p></div>'

        header_html = "".join(f"<th>{_col_label(c)}</th>" for c in show_cols)
        rows_html = ""
        for r in rows:
            cells = []
            for c in show_cols:
                val = r.get(c, "")
                if c == "change_pct":
                    color = "#22c55e" if val > 0 else "#ef4444"
                    val = f'<span style="color:{color}">{val:+.2f}%</span>'
                elif c == "final_score":
                    color = "#22c55e" if val >= 65 else "#f59e0b" if val >= 45 else "#ef4444"
                    val = f'<span style="color:{color};font-weight:700">{val:.0f}</span>'
                elif c == "amount":
                    val = f"{val:.2f}亿"
                cells.append(f"<td>{val}</td>")
            rows_html += f"<tr>{''.join(cells)}</tr>\n"

        return f"""
        <div class="section">
          <h2>{title}</h2>
          <table>
            <thead><tr>{header_html}</tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        """

    show_main = ["code", "name", "close", "change_pct", "amount", "final_score",
                  "technical_score", "momentum_score", "risk_score", "candidate_type", "action_level"]
    show_detail = show_main + ["entry_condition", "risk_condition"]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NASDX · 动态选股报告 {output.get('date', '')}</title>
<style>
  :root {{
    --bg: #0d1117; --card: #161b22; --border: #30363d;
    --text: #c9d1d9; --muted: #8b949e;
    --green: #22c55e; --red: #ef4444; --yellow: #f59e0b; --blue: #3b82f6;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, monospace; font-size: 13px; line-height: 1.6; }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 24px 16px; }}
  .header {{ border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }}
  .header h1 {{ font-size: 22px; color: #fff; }}
  .header .meta {{ color: var(--muted); font-size: 12px; }}

  .stats {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .stat-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px; text-align: center; }}
  .stat-card .val {{ font-size: 24px; font-weight: 700; color: #fff; }}
  .stat-card .lbl {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}

  .regime-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
  .regime-card h3 {{ color: var(--blue); font-size: 13px; margin-bottom: 8px; }}

  .section {{ margin-bottom: 28px; }}
  .section h2 {{ font-size: 16px; color: var(--blue); margin-bottom: 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }}

  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ color: var(--muted); font-weight: 600; text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.06); white-space: nowrap; }}
  tr:hover {{ background: rgba(255,255,255,0.02); }}

  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
  .badge-green {{ background: rgba(34,197,94,0.15); color: var(--green); }}
  .badge-yellow {{ background: rgba(245,158,11,0.15); color: var(--yellow); }}
  .badge-red {{ background: rgba(239,68,68,0.15); color: var(--red); }}

  .footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: 11px; text-align: center; }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1>📊 NASDX 动态选股报告</h1>
    <div class="meta">生成时间 {output.get('generated_at', '')} · 数据日期 {output.get('date', '')}</div>
  </div>

  <div class="stats">
    <div class="stat-card"><div class="val">{summary.get('total_stocks', 0)}</div><div class="lbl">原始全A</div></div>
    <div class="stat-card"><div class="val">{summary.get('after_filter', 0)}</div><div class="lbl">过滤后</div></div>
    <div class="stat-card"><div class="val">{summary.get('after_risk_filter', 0)}</div><div class="lbl">风险通过后</div></div>
    <div class="stat-card"><div class="val" style="color:var(--green)">{summary.get('tier_a', 0)}</div><div class="lbl">A 级候选</div></div>
    <div class="stat-card"><div class="val" style="color:var(--blue)">{summary.get('tier_b', 0)}</div><div class="lbl">B 级候选</div></div>
    <div class="stat-card"><div class="val" style="color:var(--yellow)">{summary.get('breakout', 0)}</div><div class="lbl">突破候选</div></div>
    <div class="stat-card"><div class="val" style="color:var(--muted)">{summary.get('avoid', 0)}</div><div class="lbl">回避池</div></div>
  </div>

  <div class="regime-card">
    <h3>市场环境</h3>
    <div>状态：<strong>{regime.get('regime', '')}</strong> · 综合分 {regime.get('score', 0)}/100</div>
    <div style="color:var(--muted); font-size:12px; margin-top:4px">{regime.get('summary', '')}</div>
  </div>

  <div class="regime-card">
    <h3>股票池覆盖</h3>
    <div>状态：<strong>{coverage_status}</strong></div>
    <div style="color:var(--muted); font-size:12px; margin-top:4px">
      SSE {quoted_counts.get('SSE', 0)}/{coverage_counts.get('SSE', 0)} ·
      SZSE {quoted_counts.get('SZSE', 0)}/{coverage_counts.get('SZSE', 0)} ·
      BSE {quoted_counts.get('BSE', 0)}/{coverage_counts.get('BSE', 0)}
    </div>
  </div>

  {_render_table("A 级候选（重点跟踪）", candidates.get("tier_a", []), show_detail)}
  {_render_table("B 级候选（观察试错）", candidates.get("tier_b", []), show_main)}
  {_render_table("回踩候选", candidates.get("pullback", []), show_main)}
  {_render_table("突破候选", candidates.get("breakout", []), show_main)}
  {_render_table("回避池", candidates.get("avoid", []), show_main)}

  <div class="footer">
    ⚠️ 本报告由 NASDX 动态选股引擎自动生成，仅供学习研究，不构成投资建议。<br>
    Generated by NASDX · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
  </div>

</div>
</body>
</html>"""
    return html


def _col_label(key: str) -> str:
    labels = {
        "code": "代码", "name": "名称", "close": "收盘价",
        "change_pct": "涨跌幅", "amount": "成交额",
        "final_score": "综合分", "technical_score": "技术分",
        "sector_score": "板块分", "momentum_score": "动量分",
        "liquidity_score": "流动性分", "risk_score": "风险分",
        "candidate_type": "类型", "entry_condition": "入场条件",
        "risk_condition": "风险条件", "action_level": "操作",
        "turnover": "换手率", "ma5": "MA5", "ma20": "MA20",
        "rsi": "RSI", "macd_bar": "MACD", "vol_ratio": "量比",
    }
    return labels.get(key, key)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NASDX 动态选股引擎")
    parser.add_argument("--top", type=int, default=30, help="A 级候选数量（默认 30）")
    parser.add_argument("--limit", type=int, default=200, help="进入历史因子计算的候选数（默认 200）")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录（默认 reports/）")
    args = parser.parse_args()

    run_selector(top_n=args.top, max_fetch=args.limit, output_dir=args.output_dir)

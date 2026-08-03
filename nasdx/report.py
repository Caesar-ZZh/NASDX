"""
HTML 报告生成器 — 生成完整的交互式分析报告
风格：暗色主题，类似 FinGenius 的专业报告
"""
import json
from datetime import datetime
from typing import Any, Dict, List
from nasdx.schema import AnalysisResult, BattleVote, FinalReport


def generate_html_report(report: FinalReport) -> str:
    """生成完整 HTML 报告"""

    signal_color = {
        "bullish": "#00C853",
        "bearish": "#FF1744",
        "neutral": "#FFD600",
    }.get(report.final_signal, "#888")

    signal_label = {
        "bullish": "📈 看多",
        "bearish": "📉 看空",
        "neutral": "➡️ 中性",
    }.get(report.final_signal, "中性")

    # 各维度卡片（带 #65 新鲜度标注）
    dim_cards = _build_dim_cards(report.research_results, report.freshness)

    # 辩论记录
    battle_html = _build_battle_html(report.battle_transcript)

    # 投票结果
    vote_html = _build_vote_html(report.votes, report.bullish_pct)
    data_quality_html = _build_data_quality_html(report.data_quality)
    freshness_note = _build_freshness_note(report)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NASDX — {report.stock_code} {report.stock_name} 分析报告</title>
<style>
  :root {{
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --text-muted: #8b949e;
    --green: #00C853;
    --red: #FF1744;
    --yellow: #FFD600;
    --blue: #58a6ff;
    --purple: #bc8cff;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", monospace;
    font-size: 14px;
    line-height: 1.6;
  }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px; }}

  /* Header */
  .header {{
    border-bottom: 1px solid var(--border);
    padding-bottom: 20px;
    margin-bottom: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .header-left h1 {{ font-size: 24px; color: #fff; }}
  .header-left .subtitle {{ color: var(--text-muted); font-size: 13px; margin-top: 4px; }}
  .signal-badge {{
    display: inline-block;
    padding: 8px 20px;
    border-radius: 20px;
    font-size: 18px;
    font-weight: bold;
    background: {signal_color}22;
    color: {signal_color};
    border: 2px solid {signal_color};
  }}

  /* Summary */
  .summary-box {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 24px;
  }}
  .summary-box h3 {{ color: var(--blue); margin-bottom: 8px; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
  .summary-text {{ white-space: pre-wrap; color: var(--text); }}

  /* 维度网格 */
  .dim-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }}
  .dim-card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    position: relative;
    overflow: hidden;
  }}
  .dim-card::before {{
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
  }}
  .dim-card.bullish::before {{ background: var(--green); }}
  .dim-card.bearish::before {{ background: var(--red); }}
  .dim-card.neutral::before {{ background: var(--yellow); }}
  .dim-card .dim-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }}
  .dim-card .dim-title {{ font-weight: bold; color: #fff; font-size: 15px; }}
  .dim-card .dim-signal {{
    font-size: 12px;
    padding: 2px 8px;
    border-radius: 12px;
    font-weight: bold;
  }}
  .bullish .dim-signal {{ background: var(--green)22; color: var(--green); }}
  .bearish .dim-signal {{ background: var(--red)22; color: var(--red); }}
  .neutral .dim-signal {{ background: var(--yellow)22; color: var(--yellow); }}
  .dim-confidence {{ color: var(--text-muted); font-size: 12px; margin-bottom: 8px; }}
  .dim-freshness {{ font-size: 11px; margin-bottom: 8px; }}
  .dim-freshness .fresh {{ color: var(--green); }}
  .dim-freshness .reused {{ color: var(--yellow); }}
  .freshness-note {{
    border: 1px solid var(--border);
    border-left: 3px solid var(--blue);
    background: var(--card);
    border-radius: 6px;
    padding: 10px 14px;
    margin-bottom: 20px;
    font-size: 12px;
    color: var(--text-muted);
  }}
  .dim-points {{ list-style: none; }}
  .dim-points li {{ color: var(--text-muted); font-size: 13px; padding: 2px 0; }}
  .dim-points li::before {{ content: "•  "; color: var(--blue); }}

  /* 辩论 */
  .section {{ margin-bottom: 24px; }}
  .section h2 {{
    font-size: 16px;
    color: var(--blue);
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }}
  .battle-msg {{
    padding: 10px 14px;
    margin-bottom: 8px;
    border-radius: 6px;
    font-size: 13px;
    border-left: 3px solid transparent;
  }}
  .battle-msg.bull {{ border-left-color: var(--green); background: #00C85308; }}
  .battle-msg.bear {{ border-left-color: var(--red); background: #FF174408; }}
  .battle-msg.judge {{ border-left-color: var(--purple); background: #bc8cff08; }}

  /* 投票 */
  .vote-bar-wrap {{ margin-bottom: 16px; }}
  .vote-bar-label {{ display: flex; justify-content: space-between; margin-bottom: 4px; }}
  .vote-bar {{ height: 12px; background: var(--border); border-radius: 6px; overflow: hidden; }}
  .vote-bar-fill {{
    height: 100%;
    background: linear-gradient(90deg, var(--green), #00E676);
    border-radius: 6px;
    transition: width 0.5s ease;
  }}
  .vote-table {{ width: 100%; border-collapse: collapse; }}
  .vote-table th, .vote-table td {{
    text-align: left;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
  }}
  .vote-table th {{ color: var(--text-muted); font-weight: normal; }}
  .vote-bull {{ color: var(--green); }}
  .vote-bear {{ color: var(--red); }}
  .vote-neutral {{ color: var(--yellow); }}

  /* Footer */
  .footer {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
    color: var(--text-muted);
    font-size: 12px;
    text-align: center;
  }}
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <h1>📊 {report.stock_code} {report.stock_name}</h1>
      <div class="subtitle">NASDX 多智能体分析报告 · {report.date}</div>
    </div>
    <div class="signal-badge">{signal_label}</div>
  </div>

  <!-- 综合摘要 -->
  {freshness_note}
  {data_quality_html}

  <div class="summary-box">
    <h3>综合研判 · 看多占比 {report.bullish_pct:.1f}%</h3>
    <div class="summary-text">{_escape_html(report.summary)}</div>
  </div>

  <!-- 操作建议 -->
  <div class="summary-box">
    <h3>操作建议</h3>
    <div class="summary-text">{_escape_html(report.operation_advice)}</div>
  </div>

  <!-- 各维度分析 -->
  <div class="section">
    <h2>📐 多维度专家分析</h2>
    <div class="dim-grid">
      {dim_cards}
    </div>
  </div>

  <!-- 辩论记录 -->
  <div class="section">
    <h2>⚔️ Battle 辩论记录</h2>
    {battle_html}
  </div>

  <!-- 投票结果 -->
  <div class="section">
    <h2>🗳️ 专家投票</h2>
    {vote_html}
  </div>

  <div class="footer">
    ⚠️ 本报告由 NASDX AI 多智能体系统生成，仅供学习研究，不构成任何投资建议。
    <br>Generated by NASDX · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
  </div>

</div>
</body>
</html>"""
    return html


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
        if text
        else ""
    )


_DEPTH_LABELS = {
    "full": "完整分析（全部维度本次重跑）",
    "intraday": "盘中增量（复用慢变量结论，仅刷新失效的行情维度）",
    "refresh": "增量刷新（仅重跑被失效规则命中的维度）",
}


def _format_age(age_seconds: Any) -> str:
    try:
        value = float(age_seconds)
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    if value < 60:
        return f"{value:.0f}秒前"
    if value < 3600:
        return f"{value / 60:.0f}分钟前"
    return f"{value / 3600:.1f}小时前"


def _build_freshness_badge(entry: Any) -> str:
    """Render the per-dimension 本次刷新 / 复用 badge."""
    if not isinstance(entry, dict):
        return ""
    status = str(entry.get("status") or "")
    refreshed_at = _escape_html(str(entry.get("refreshed_at") or ""))
    if status == "reused":
        age = _format_age(entry.get("age_seconds"))
        suffix = f"（{age}）" if age else ""
        return (
            '<div class="dim-freshness"><span class="reused">♻️ 复用上一轮结论</span> '
            f"· 上次刷新 {refreshed_at}{suffix}</div>"
        )
    if status == "refreshed":
        return (
            '<div class="dim-freshness"><span class="fresh">🟢 本次刷新</span> '
            f"· {refreshed_at}</div>"
        )
    return ""


def _build_freshness_note(report: FinalReport) -> str:
    """Top-of-report banner: depth, reuse boundary and observability counters."""
    freshness = report.freshness or {}
    performance = report.performance or {}
    if not freshness and not performance:
        return ""

    depth = str(report.analysis_depth or performance.get("effective_depth") or "full")
    parts: List[str] = [f"<b>执行深度</b>：{_escape_html(depth)} — {_DEPTH_LABELS.get(depth, '')}"]

    degraded = str(performance.get("degraded_reason") or "")
    if degraded:
        requested = _escape_html(str(performance.get("requested_depth") or ""))
        parts.append(f"⚠️ 请求 {requested} 已安全回退为 {depth}：{_escape_html(degraded)}")

    reused = [k for k, v in freshness.items() if isinstance(v, dict) and v.get("status") == "reused"]
    refreshed = [
        k for k, v in freshness.items() if isinstance(v, dict) and v.get("status") == "refreshed"
    ]
    if refreshed:
        parts.append("本次刷新：" + _escape_html("、".join(sorted(refreshed))))
    if reused:
        parts.append(
            "复用缓存：" + _escape_html("、".join(sorted(reused)))
            + " —— 这些维度未在本次重新核验，不代表公告/基本面已被重新检查。"
        )
    snapshot_as_of = performance.get("snapshot_data_as_of")
    if snapshot_as_of:
        parts.append(f"缓存数据日期：{_escape_html(str(snapshot_as_of))}")
    if performance:
        parts.append(
            "观测：LLM 调用 {calls} 次 · 总耗时 {ms} ms".format(
                calls=performance.get("llm_call_count", "-"),
                ms=performance.get("total_elapsed_ms", "-"),
            )
        )
    return '<div class="freshness-note">' + "<br>".join(parts) + "</div>"


def _build_dim_cards(research_results: Dict[str, Any], freshness: Dict[str, Any] | None = None) -> str:
    freshness = freshness or {}
    dim_labels = {
        "technical": ("📈 技术面", "均线/MACD/RSI/布林带"),
        "fund_flow": ("💰 资金流向", "主力/超大单/大单"),
        "risk":      ("🛡️ 风险评估", "超买超卖/波动/背离"),
        "sector":    ("🏭 板块分析", "板块轮动/相对强弱"),
        "chokepoint": ("🧭 供应链瓶颈", "需求冲击/产业链卡点/贝叶斯"),
        "synthesis": ("🎯 综合研判", "多维度整合"),
    }

    cards = []
    for dim, result in research_results.items():
        if not result or not isinstance(result, AnalysisResult):
            continue
        title, subtitle = dim_labels.get(dim, (dim, ""))
        signal = result.signal or "neutral"
        conf_pct = f"{result.confidence:.0%}"

        points_html = ""
        if result.key_points:
            items = "".join(f"<li>{_escape_html(p)}</li>" for p in result.key_points[:4])
            points_html = f'<ul class="dim-points">{items}</ul>'

        signal_text = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(signal, signal)

        cards.append(f"""
<div class="dim-card {signal}">
  <div class="dim-header">
    <span class="dim-title">{title}</span>
    <span class="dim-signal">{signal_text}</span>
  </div>
  <div class="dim-confidence">置信度 {conf_pct} · {subtitle}</div>
  {_build_freshness_badge(freshness.get(dim))}
  {points_html}
</div>""")

    return "\n".join(cards)


def _build_data_quality_html(data_quality: Dict[str, Any]) -> str:
    if not data_quality:
        return ""
    color = {
        "ok": "#00C853",
        "warning": "#FFD600",
        "danger": "#FF1744",
    }.get(data_quality.get("severity"), "#FFD600")
    message = _escape_html(data_quality.get("message", "数据状态未评估"))
    return f"""
<div class="summary-box" style="border-color:{color}66">
  <h3 style="color:{color}">数据新鲜度</h3>
  <div class="summary-text" style="color:{color}">{message}</div>
</div>"""


def _build_battle_html(transcript: List[str]) -> str:
    if not transcript:
        return '<p style="color:#8b949e">暂无辩论记录</p>'

    items = []
    for msg in transcript:
        if msg.startswith("🟢"):
            cls = "bull"
        elif msg.startswith("🔴"):
            cls = "bear"
        else:
            cls = "judge"
        items.append(
            f'<div class="battle-msg {cls}">{_escape_html(msg)}</div>'
        )
    return "\n".join(items)


def _build_vote_html(votes: List[BattleVote], bullish_pct: float) -> str:
    if not votes:
        return '<p style="color:#8b949e">暂无投票数据</p>'

    bar_width = min(100, max(0, bullish_pct))
    bull_count = sum(1 for v in votes if v.vote == "bullish")
    bear_count = sum(1 for v in votes if v.vote == "bearish")

    vote_rows = ""
    for v in votes:
        cls = {"bullish": "vote-bull", "bearish": "vote-bear", "neutral": "vote-neutral"}.get(v.vote, "")
        label = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(v.vote, v.vote)
        vote_rows += f"""<tr>
  <td>{v.agent_name}</td>
  <td class="{cls}">{label}</td>
  <td style="color:#8b949e">{_escape_html(v.reasoning)}</td>
</tr>"""

    return f"""
<div class="vote-bar-wrap">
  <div class="vote-bar-label">
    <span style="color:#00C853">看多 {bull_count}票</span>
    <span style="color:#8b949e">看多占比 {bullish_pct:.1f}%</span>
    <span style="color:#FF1744">看空 {bear_count}票</span>
  </div>
  <div class="vote-bar">
    <div class="vote-bar-fill" style="width:{bar_width}%"></div>
  </div>
</div>
<table class="vote-table">
  <thead>
    <tr><th>投票者</th><th>立场</th><th>理由</th></tr>
  </thead>
  <tbody>{vote_rows}</tbody>
</table>"""

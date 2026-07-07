"""
selector_page.py - Streamlit 今日选股页面

渲染 reports/stock_selector_latest.json 的内容，
展示 A/B 级候选、市场环境、评分分布。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import threading as _threading
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

ROOT = Path(__file__).parent


def _render_selector_table(rows: List[Dict], tab_name: str) -> str:
    """Render selector table as HTML."""
    if not rows:
        return '<div class="n-card" style="text-align:center;padding:24px;color:#48484a">No data</div>'

    head = "<th>Code</th><th>Name</th><th>Close</th><th>Chg%</th><th>Score</th><th>Tech</th><th>Mom</th><th>Risk</th><th>Type</th><th>Action</th>"
    body = ""
    for r in rows:
        score = r.get("final_score", 0)
        sc_color = "#22c55e" if score >= 65 else "#f59e0b" if score >= 45 else "#ef4444"
        chg = r.get("change_pct", 0)
        chg_color = "#22c55e" if chg > 0 else "#ef4444"
        chg_str = f"{chg:+.2f}%"
        type_map = {
            "trend_breakout": "Breakout", "trend_pullback": "Pullback",
            "sector_leader": "Leader", "value_repair": "Repair",
            "etf_alternative": "ETF Alt", "avoid": "Avoid",
        }
        ctype = type_map.get(r.get("candidate_type", ""), r.get("candidate_type", ""))
        body += (
            f"<tr>"
            f'<td>{r.get("code","")}</td>'
            f'<td>{r.get("name","")}</td>'
            f'<td>{r.get("close",0):.2f}</td>'
            f'<td style="color:{chg_color};font-weight:600">{chg_str}</td>'
            f'<td style="color:{sc_color};font-weight:700">{score:.0f}</td>'
            f'<td>{r.get("technical_score",0):.0f}</td>'
            f'<td>{r.get("momentum_score",0):.0f}</td>'
            f'<td>{r.get("risk_score",0):.0f}</td>'
            f'<td>{ctype}</td>'
            f'<td><span style="color:#8b949e;font-size:11px">{r.get("action_level","")}</span></td>'
            f"</tr>"
        )

    return (
        '<div style="overflow:auto">'
        f"<table style='width:100%;border-collapse:collapse;font-size:12px'>"
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody>"
        f"</table></div>"
        "<style>"
        "table th{color:rgba(255,255,255,0.42);font-weight:600;text-align:left;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.08);white-space:nowrap}"
        "table td{color:rgba(255,255,255,0.75);padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.06)}"
        "table tr:last-child td{border-bottom:0}"
        "</style>"
    )


def render_selector_page(st_module, root_path=None):
    """Render the stock selector page."""
    st = st_module
    root = root_path or ROOT

    st.markdown(
        '<div style="padding:24px 0 20px">'
        '<div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">Today\'s Stock Selection</div>'
        '<div style="font-size:13px;color:#636366;margin-top:4px">Full A-share Universe &middot; Multi-dimensional Scoring &middot; Market Regime</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    sel_running = st.session_state.get("selector_scan_running", False)
    c_btn, c_status, _ = st.columns([1, 2, 3])

    with c_btn:
        if st.button("Pick Stocks", use_container_width=True,
                     disabled=sel_running, key="selector_scan_btn"):
            def _run_selector():
                subprocess.run(
                    [sys.executable, str(root / "run_stock_selector.py"), "--top", "20"],
                    capture_output=True,
                )
                st.session_state["selector_scan_running"] = False
                st.session_state["selector_scan_thread"] = None
                try:
                    load_stock_selector.clear()
                except Exception:
                    pass

            _t = _threading.Thread(target=_run_selector, daemon=True)
            _t.start()
            st.session_state["selector_scan_running"] = True
            st.session_state["selector_scan_thread"] = _t
            st.session_state["selector_scan_start"] = time.time()
            st.rerun()

    with c_status:
        if sel_running:
            _scan_t = st.session_state.get("selector_scan_thread")
            _elapsed = int(time.time() - st.session_state.get("selector_scan_start", time.time()))
            _done = _scan_t is None or not _scan_t.is_alive()
            if _done:
                st.session_state["selector_scan_running"] = False
                try:
                    load_stock_selector.clear()
                except Exception:
                    pass
                st.rerun()
            else:
                estr = f"{_elapsed // 60}m{_elapsed % 60}s" if _elapsed >= 60 else f"{_elapsed}s"
                st.markdown(
                    '<div style="padding-top:8px;font-size:12px;color:#f59e0b">'
                    f"Scanning... Elapsed: {estr}</div>",
                    unsafe_allow_html=True,
                )

    d = load_stock_selector()
    if not d:
        st.markdown(
            '<div class="n-card" style="text-align:center;padding:48px;color:#48484a">'
            'No data yet. Click "Pick Stocks" or wait for scheduled task.</div>',
            unsafe_allow_html=True,
        )
    else:
        # Market regime
        regime = d.get("market_regime", {})
        regime_color = {
            "bullish": "#22c55e", "bearish": "#ef4444",
            "neutral": "#f59e0b", "structural": "#3b82f6",
            "mixed": "#8b949e",
        }.get(regime.get("regime", ""), "#f59e0b")
        st.markdown(
            f'<div style="background:{regime_color}15;border:1px solid {regime_color}40;'
            f'border-radius:8px;padding:14px 16px;margin-bottom:20px">'
            f'<div style="font-size:13px;font-weight:600;color:{regime_color}">'
            f'Market: {regime.get("regime", "")} ({regime.get("score", 0)}/100)</div>'
            f'<div style="font-size:12px;color:rgba(255,255,255,0.55);margin-top:4px">'
            f"{regime.get('summary', '')}</div>"
            "</div>",
            unsafe_allow_html=True,
        )

        # Stats
        s = d.get("summary", {})
        sc1, sc2, sc3, sc4, sc5 = st.columns(5, gap="small")
        stats = [
            ("Filtered", s.get("after_filter", 0), "#3b82f6"),
            ("Tier A", s.get("tier_a", 0), "#22c55e"),
            ("Tier B", s.get("tier_b", 0), "#58a6ff"),
            ("Breakout", s.get("breakout", 0), "#f59e0b"),
            ("Avoid", s.get("avoid", 0), "#ef4444"),
        ]
        for col, (lb, val, color) in zip([sc1, sc2, sc3, sc4, sc5], stats):
            with col:
                st.markdown(
                    f'<div class="n-card" style="text-align:center;padding:12px 8px">'
                    f'<div style="font-size:22px;font-weight:600;color:{color}">{val}</div>'
                    f'<div style="font-size:11px;color:rgba(255,255,255,0.40);margin-top:4px">{lb}</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # Tabs
        tab_names = ["Tier A", "Tier B", "Breakout", "Pullback", "Avoid"]
        tab_keys = ["tier_a", "tier_b", "breakout", "pullback", "avoid"]
        tabs = st.tabs(tab_names)
        tab_data = {k: d.get("candidates", {}).get(k, []) for k in tab_keys}

        for tab_name, tab, key in zip(tab_names, tabs, tab_keys):
            with tab:
                rows = tab_data[key]
                st.markdown(_render_selector_table(rows, tab_name), unsafe_allow_html=True)

                # Quick deep analysis buttons
                for i, r in enumerate(rows[:5]):
                    code = r.get("code", "")
                    name = r.get("name", "")
                    if st.button(
                        f"Deep Analysis {code} {name}",
                        key=f"deep_{key}_{i}",
                        use_container_width=True,
                    ):
                        st.session_state["_quick"] = code
                        st.query_params["page"] = "deep"
                        st.rerun()


@st.cache_data(ttl=60, show_spinner=False)
def load_stock_selector():
    """Load latest stock selector result JSON."""
    path = ROOT / "reports" / "stock_selector_latest.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

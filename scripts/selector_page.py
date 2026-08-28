"""
selector_page.py - Streamlit 今日选股页面

渲染 reports/stock_selector_latest.json 的内容，
展示 A/B 级候选、市场环境、评分分布。
"""
from __future__ import annotations

# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import html
import json
import subprocess
import sys
import time
import threading as _threading
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

from nasdx.paths import get_reports_dir

ROOT = Path(__file__).resolve().parents[1]
_LOCAL_TASKS = {}
_LOCAL_TASK_LOCK = _threading.Lock()


def _new_local_task_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


def _register_local_task(task_id: str, thread: _threading.Thread) -> None:
    with _LOCAL_TASK_LOCK:
        _LOCAL_TASKS[task_id] = {"thread": thread}


def _set_local_task_result(task_id: str, result: dict) -> None:
    with _LOCAL_TASK_LOCK:
        item = _LOCAL_TASKS.get(task_id)
        if item is not None:
            item["result"] = result


def _take_local_task_result(task_id: str | None) -> dict | None:
    if not task_id:
        return None
    with _LOCAL_TASK_LOCK:
        item = _LOCAL_TASKS.pop(task_id, None)
    return item.get("result") if item else None


def _local_task_alive(task_id: str | None) -> bool:
    if not task_id:
        return False
    with _LOCAL_TASK_LOCK:
        item = _LOCAL_TASKS.get(task_id)
    thread = item.get("thread") if item else None
    alive = bool(thread and thread.is_alive())
    if item and not alive and "result" not in item:
        with _LOCAL_TASK_LOCK:
            _LOCAL_TASKS.pop(task_id, None)
    return alive


def _render_selector_table(rows: List[Dict], tab_name: str) -> str:
    """Render selector table as HTML."""
    if not rows:
        return f'<div class="n-card n-empty">暂无 {html.escape(tab_name)} 数据</div>'

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
        code = html.escape(str(r.get("code", "")))
        name = html.escape(str(r.get("name", "")))
        action = html.escape(str(r.get("action_level", "")))
        body += (
            f"<tr>"
            f"<td>{code}</td>"
            f"<td>{name}</td>"
            f'<td>{r.get("close",0):.2f}</td>'
            f'<td style="color:{chg_color};font-weight:600">{chg_str}</td>'
            f'<td style="color:{sc_color};font-weight:700">{score:.0f}</td>'
            f'<td>{r.get("technical_score",0):.0f}</td>'
            f'<td>{r.get("momentum_score",0):.0f}</td>'
            f'<td>{r.get("risk_score",0):.0f}</td>'
            f"<td>{html.escape(str(ctype))}</td>"
            f'<td><span style="color:var(--text-muted);font-size:11px">{action}</span></td>'
            f"</tr>"
        )

    return (
        '<div class="n-card n-table-shell" style="padding:0;overflow:auto">'
        f'<table class="n-data-table">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table></div>"
    )


def render_selector_page(st_module, root_path=None, task_helpers=None):
    """Render the stock selector page."""
    st = st_module
    root = root_path or ROOT
    helpers = task_helpers or {}
    new_task_id = helpers.get("new_task_id", _new_local_task_id)
    register_task = helpers.get("register_task", _register_local_task)
    task_alive = helpers.get("task_alive", _local_task_alive)
    set_task_result = helpers.get("set_task_result", _set_local_task_result)
    take_task_result = helpers.get("take_task_result", _take_local_task_result)

    def _default_navigate(page: str, stock_code: str | None = None) -> None:
        if stock_code:
            st.session_state["_quick"] = stock_code
        st.session_state.page = page
        st.query_params["page"] = page

    navigate = helpers.get("navigate", _default_navigate)

    st.markdown(
        '<div class="n-page-head">'
        '<div class="n-head-kicker">Selector</div>'
        '<div class="n-page-title">今日选股</div>'
        '<div class="n-page-sub">全 A 动态候选池 · 多维度评分 · 市场环境过滤</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    sel_running = st.session_state.get("selector_scan_running", False)
    c_limit, c_timeout, c_btn, _ = st.columns([1, 1, 1, 3])

    with c_limit:
        selector_limit = st.number_input(
            "最多抓取",
            min_value=30,
            max_value=1000,
            value=int(st.session_state.get("selector_limit", 50)),
            step=10,
            key="selector_limit",
        )
    with c_timeout:
        selector_timeout = st.number_input(
            "超时秒数",
            min_value=60,
            max_value=3600,
            value=int(st.session_state.get("selector_timeout", 180)),
            step=60,
            key="selector_timeout",
        )

    def _start_selector_scan() -> None:
        if st.session_state.get("selector_scan_running", False):
            return
        task_id = new_task_id("selector_scan")

        def _run_selector():
            command = [
                sys.executable,
                str(root / "scripts" / "run_stock_selector.py"),
                "--top",
                "20",
                "--limit",
                str(int(selector_limit)),
                "--output-dir",
                str(get_reports_dir(create=True)),
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    timeout=int(selector_timeout),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                detail = (completed.stderr or completed.stdout or "").strip()
                if completed.returncode == 0:
                    result = {"ok": True, "message": "今日选股完成，结果已刷新。"}
                else:
                    suffix = detail[-300:] if detail else "无错误输出"
                    result = {
                        "ok": False,
                        "message": f"今日选股失败（返回码 {completed.returncode}）：{suffix}",
                    }
            except subprocess.TimeoutExpired:
                result = {
                    "ok": False,
                    "message": f"今日选股超时（{int(selector_timeout)} 秒），请缩小抓取范围后重试。",
                }
            except Exception as exc:
                result = {"ok": False, "message": f"今日选股失败：{str(exc)[:300]}"}
            finally:
                try:
                    load_stock_selector.clear()
                except Exception:
                    pass
                set_task_result(task_id, result)

        thread = _threading.Thread(target=_run_selector, daemon=True)
        register_task(task_id, thread)
        thread.start()
        st.session_state.update({
            "selector_scan_running": True,
            "selector_scan_task_id": task_id,
            "selector_scan_start": time.time(),
            "selector_scan_result": None,
        })

    with c_btn:
        st.button(
            "开始选股",
            use_container_width=True,
            disabled=sel_running,
            key="selector_scan_btn",
            on_click=_start_selector_scan,
        )

    @st.fragment(run_every=3)
    def _render_selector_scan_status():
        if st.session_state.get("selector_scan_running", False):
            _elapsed = int(time.time() - st.session_state.get("selector_scan_start", time.time()))
            _scan_task_id = st.session_state.get("selector_scan_task_id")
            _done = not task_alive(_scan_task_id)
            if _done:
                st.session_state["selector_scan_result"] = take_task_result(_scan_task_id) or {
                    "ok": False,
                    "message": "今日选股异常结束，未收到任务结果。",
                }
                st.session_state["selector_scan_running"] = False
                st.session_state["selector_scan_task_id"] = None
                try:
                    load_stock_selector.clear()
                except Exception:
                    pass
                st.rerun()
            else:
                estr = f"{_elapsed // 60}m{_elapsed % 60}s" if _elapsed >= 60 else f"{_elapsed}s"
                st.markdown(
                    '<div class="n-status-line" style="--status-color:var(--yellow)">'
                    f'<span class="n-status-dot"></span><span>扫描中 · 已用时 {estr}</span></div>',
                    unsafe_allow_html=True,
                )
        scan_result = st.session_state.get("selector_scan_result")
        if scan_result:
            if scan_result.get("ok"):
                st.success(scan_result.get("message", "选股完成。"))
            else:
                st.error(scan_result.get("message", "选股失败。"))

    _render_selector_scan_status()

    d = load_stock_selector()
    if not d:
        st.markdown(
            '<div class="n-card n-empty">'
            "暂无数据。点击「开始选股」或等待定时任务。</div>",
            unsafe_allow_html=True,
        )
    else:
        coverage = d.get("universe_coverage", {})
        if coverage:
            counts = coverage.get("counts", {})
            quoted = coverage.get("quoted_counts", {})
            coverage_text = " · ".join(
                f"{exchange} {quoted.get(exchange, 0)}/{counts.get(exchange, 0)}"
                for exchange in ("SSE", "SZSE", "BSE")
            )
            if coverage.get("complete"):
                st.caption(f"股票池覆盖完整 · {coverage_text}")
            else:
                missing = coverage.get("unavailable_exchanges", [])
                quote_missing = coverage.get("quote_unavailable_exchanges", [])
                details = "、".join(dict.fromkeys([*missing, *quote_missing])) or "未知来源"
                st.warning(f"股票池覆盖不完整：{details} 暂不可用 · {coverage_text}")

        # Market regime
        regime = d.get("market_regime", {})
        regime_color = {
            "bullish": "#22c55e", "bearish": "#ef4444",
            "neutral": "#f59e0b", "structural": "#3b82f6",
            "mixed": "#8b949e",
        }.get(regime.get("regime", ""), "#f59e0b")
        st.markdown(
            f'<div class="n-card n-card-accent-line" style="--accent-line:{regime_color};margin-bottom:20px">'
            f'<div style="font-size:13px;font-weight:700;color:{regime_color}">'
            f'市场环境：{html.escape(str(regime.get("regime", "")))} ({regime.get("score", 0)}/100)</div>'
            f'<div style="font-size:12px;color:rgba(255,255,255,0.55);margin-top:4px">'
            f"{html.escape(str(regime.get('summary', '')))}</div>"
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
                    f'<div class="n-card n-kpi" style="--kpi-color:{color}">'
                    f'<div class="n-kpi-value">{val}</div>'
                    f'<div class="n-kpi-label">{lb}</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # Tabs
        tab_names = ["A级候选", "B级候选", "突破", "回踩", "回避"]
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
                    st.button(
                        f"深度分析 {code} {name}",
                        key=f"deep_{key}_{i}",
                        use_container_width=True,
                        on_click=navigate,
                        args=("deep", code),
                    )


@st.cache_data(ttl=60, show_spinner=False)
def load_stock_selector():
    """Load latest stock selector result JSON."""
    path = get_reports_dir() / "stock_selector_latest.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

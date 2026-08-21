"""
NASDX V2 — 量化策略页面
整合：QLib Alpha158 因子 + VnPy 回测/绩效 + FinRL 强化学习环境

⚡ 性能优化：
  - pandas/numpy 延迟导入（仅在 render_quant_page() 和 Tab 渲染时）
  - quant.etf50_quant 已优化为延迟加载（见 etf50_quant.py）
  - 结果：import quant_page 耗时从 ~1150ms → ~12ms
"""
from __future__ import annotations
# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import sys, json, glob, threading, time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── 样式工具 ──────────────────────────────────────────
def _sc(v):   return "#22c55e" if v>=60 else "#ef4444" if v<=40 else "#f59e0b"
def _bar(v,h=4):
    c=_sc(v)
    return(f'<div class="bar-wrap" style="height:{h}px">'
           f'<div style="width:{min(v,100):.0f}%;height:100%;background:{c};border-radius:999px"></div></div>')
def _sig(sig):
    cfg={"bullish":("#22c55e","rgba(34,197,94,0.12)","↑ 看多"),
         "bearish":("#ef4444","rgba(239,68,68,0.12)","↓ 看空"),
         "neutral":("#f59e0b","rgba(245,158,11,0.10)","→ 中性")}
    c,bg,lb=cfg.get(sig,("#9ca3af","rgba(156,163,175,0.1)","— 无"))
    return(f'<span style="color:{c};background:{bg};border:1px solid {c}40;'
           f'border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600">{lb}</span>')
def _card(body,accent=None):
    cls = "n-card n-card-accent-line" if accent else "n-card"
    accent_style = f' style="--accent-line:{accent}"' if accent else ""
    return f'<div class="{cls}"{accent_style}>{body}</div>'
def _metric(label,value,color="#fff",sub=""):
    sub_html = f'<div class="n-kpi-sub">{sub}</div>' if sub else ''
    return(f'<div class="n-card n-kpi" style="--kpi-color:{color}">'
           f'<div class="n-kpi-value">{value}</div>'
           f'<div class="n-kpi-label">{label}</div>'
           f'{sub_html}</div>')

# ── 缓存 etf50_pool.json 的 ETF 列表 ──────────────────────
def _load_etf50_pool() -> list:
    """懒加载 etf50_pool.json，避免重复读盘"""
    try:
        with open(ROOT / "etf50_pool.json", encoding="utf-8") as f:
            return json.load(f).get("etfs", [])
    except Exception:
        return []


# ── 进度芯片构建 ────────────────────────────────────────
def _build_recent_chips(lines: list) -> str:
    """把最近扫描的进度行渲染为小芯片"""
    import re
    chips = []
    for line in lines:
        m = re.search(r'\[(\d+)/(\d+)\]\s+(\S+)\s+(.*?)(?:\.\.\.|$)', line)
        if not m:
            continue
        idx, total, code, name_raw = m.group(1), m.group(2), m.group(3), m.group(4)
        name = name_raw.strip("...").strip()[:8]
        if "📈" in line:
            color, bg = "#22c55e", "rgba(34,197,94,0.10)"
        elif "📉" in line:
            color, bg = "#ef4444", "rgba(239,68,68,0.10)"
        else:
            color, bg = "#f59e0b", "rgba(245,158,11,0.08)"
        chips.append(
            f'<span style="background:{bg};border:1px solid {color}30;border-radius:4px;'
            f'padding:3px 8px;font-size:11px;color:{color};white-space:nowrap">'
            f'{idx} {name or code}</span>'
        )
    return "".join(chips) if chips else '<span style="color:rgba(255,255,255,0.2);font-size:11px">等待数据...</span>'


# ── 后台线程运行 ETF50 量化 ──────────────────────────────
def _run_etf50_bg(days, top_n, freq, log_path):
    import sys, builtins, io
    sys.path.insert(0, str(ROOT))
    with open(log_path, "w", encoding="utf-8", buffering=1) as log:
        try:
            import quant.patch_requests  # noqa
            from quant.etf50_quant import run_etf50_quant
            _orig = builtins.print
            def _tee(*a, **k):
                buf=io.StringIO(); _orig(*a, file=buf, **k)
                log.write(buf.getvalue()); log.flush(); _orig(*a, **k)
            builtins.print = _tee
            result = run_etf50_quant(days=days, top_n=top_n,
                                     rebalance_freq=freq, verbose=True)
            builtins.print = _orig
            log.write(f"\n__DONE__:{result.get('_saved_to','')}\n")
        except Exception as e:
            import traceback
            log.write(f"\n__ERROR__:{e}\n{traceback.format_exc()}\n")

# ══════════════════════════════════════════════════════
#  主页面入口
# ══════════════════════════════════════════════════════
def render_quant_page(st):
    # ── 启动时清理僵死线程状态 ──────────────────────────
    for tk in ["etf50q_thread", "conf_thread"]:
        t = st.session_state.get(tk)
        if t is not None and not t.is_alive():
            st.session_state.pop(tk, None)
            running_key = "etf50q_running" if "etf50" in tk else "conf_running"
            if st.session_state.get(running_key):
                st.session_state[running_key] = False

    st.markdown(
        '<div class="n-page-head">'
        '<div class="n-head-kicker">Quant Lab</div>'
        '<div class="n-page-title">量化策略引擎</div>'
        '<div class="n-page-sub">QLib Alpha158 因子 · VnPy 回测引擎 · FinRL 强化学习 · ETF50 全量分析</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # ── VnPy 状态条（session 缓存，避免重复 import）───────
    if "vnpy_info" not in st.session_state:
        try:
            from quant.vnpy_bridge import get_vnpy_info
            st.session_state["vnpy_info"] = get_vnpy_info()
        except Exception:
            st.session_state["vnpy_info"] = {"available": False, "version": None}
    vinfo = st.session_state["vnpy_info"]
    if vinfo.get("available"):
        gw_str = "、".join(vinfo.get("gateways",[])) or "无 Gateway"
        st.markdown(
            f'<div style="background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.2);'
            f'border-radius:6px;padding:7px 14px;margin-bottom:12px;font-size:12px;'
            f'color:rgba(34,197,94,0.85)">'
            f'✓ VnPy {vinfo.get("version","")} &nbsp;·&nbsp; {gw_str}'
            f'</div>',
            unsafe_allow_html=True,
        )

    tab0, tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "💼 持仓顾问", "🚀 ETF50 全量", "📐 因子分析",
        "📈 VnPy 回测", "⚙️ 参数优化", "🛡️ 过拟合诊断", "🧬 置信度训练"
    ])

    # ════════════════════════════════════════
    #  Tab0: 持仓调仓顾问
    # ════════════════════════════════════════
    with tab0:
        try:
            from position_page import render_position_advisor
            render_position_advisor(st)
        except Exception as _e:
            import traceback
            st.error(f"持仓顾问加载失败：{_e}")
            st.code(traceback.format_exc())

    # ════════════════════════════════════════
    #  Tab1: ETF50 全量量化
    # ════════════════════════════════════════
    with tab1:
        st.markdown(_card(
            '<div style="font-size:12px;color:rgba(255,255,255,0.45);line-height:1.9">'
            '对 <b style="color:#fff">50 只主流 ETF</b> 执行完整量化流程：'
            '数据获取 → Alpha158 因子(80个) → 多因子评分 → Walk-Forward 回测 → 排行榜'
            '</div>', accent="#3b82f6"
        ), unsafe_allow_html=True)

        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

        p1,p2,p3 = st.columns(3)
        with p1: days = st.select_slider("历史天数",[90,180,252,365],value=252)
        with p2: top_n = st.slider("Top-N 组合",3,10,5)
        with p3: freq = st.selectbox(
            "调仓频率", ["W 每周", "M 每月", "D 每日"],
            help="按实际交易日历调仓：周频=每个交易周第一个交易日（周一休市顺延周二）；"
                 "月频=每月第一个交易日（1 日休市顺延）。信号只用调仓日之前的数据。"
        ).split()[0]

        pool_n = len(_load_etf50_pool())
        est = max(3, pool_n * days // 5000)
        st.caption(f"预计 {est}-{est*2} 分钟 · {pool_n} 只 ETF · {days} 天历史")

        is_running = st.session_state.get("etf50q_running", False)

        if not is_running:
            if st.button("▶  开始全量分析", key="run_etf50_quant",
                         type="primary", use_container_width=False):
                lp = ROOT / "etf50_quant_log.txt"
                lp.write_text("", encoding="utf-8")
                t = threading.Thread(target=_run_etf50_bg,
                                     args=(days,top_n,freq,str(lp)), daemon=True)
                t.start()
                st.session_state.update({"etf50q_running":True,"etf50q_thread":t,
                                          "etf50q_log":str(lp),
                                          "etf50q_start_ts":time.time()})
                st.rerun()
        else:
            # ── 运行中：精美进度面板 ──────────────────────────
            import re as _re
            lp = Path(st.session_state.get("etf50q_log",""))
            log_text = lp.read_text(encoding="utf-8") if lp.exists() else ""
            all_lines = [l for l in log_text.splitlines() if l.strip()]

            # 精确解析进度行 [xx/50]
            progress_lines = [l for l in all_lines if _re.search(r'\[\d+/\d+\]', l)]
            done_n = len(progress_lines)
            pct    = done_n / max(pool_n, 1)

            # 估算剩余时间（基于已用时间推算）
            start_ts = st.session_state.get("etf50q_start_ts", time.time())
            elapsed  = time.time() - start_ts
            if done_n > 0:
                eta_sec = elapsed / done_n * (pool_n - done_n)
                if eta_sec < 60:
                    eta_str = f"{int(eta_sec)}秒"
                else:
                    eta_str = f"{int(eta_sec/60)}分{int(eta_sec%60)}秒"
            else:
                eta_str = "计算中..."

            elapsed_str = f"{int(elapsed//60)}分{int(elapsed%60)}秒" if elapsed >= 60 else f"{int(elapsed)}秒"

            # ── 状态卡片 ───────────────────────────────────────
            pct_display = int(pct * 100)
            bar_color   = "#22c55e" if pct > 0.7 else "#3b82f6" if pct > 0.3 else "#f59e0b"

            st.markdown(f"""
            <div style="background:#111;border:1px solid rgba(255,255,255,0.08);border-radius:10px;
                        padding:20px 24px;margin-bottom:14px;position:relative;overflow:hidden">
              <div style="position:absolute;top:0;left:0;right:0;height:2px;
                          background:linear-gradient(90deg,transparent,{bar_color},transparent)"></div>

              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                <div>
                  <div style="font-size:13px;font-weight:600;color:#fff">
                    ⚗️ ETF50 量化分析进行中
                  </div>
                  <div style="font-size:11px;color:rgba(255,255,255,0.35);margin-top:3px">
                    {done_n} / {pool_n} 只完成 &nbsp;·&nbsp; 已用时 {elapsed_str} &nbsp;·&nbsp; 预计剩余 {eta_str}
                  </div>
                </div>
                <div style="font-size:28px;font-weight:800;color:{bar_color};
                            font-variant-numeric:tabular-nums;letter-spacing:0">
                  {pct_display}<span style="font-size:14px;color:rgba(255,255,255,0.3)">%</span>
                </div>
              </div>

              <!-- 进度条 -->
              <div style="background:rgba(255,255,255,0.06);border-radius:4px;height:8px;overflow:hidden;margin-bottom:16px">
                <div style="width:{pct_display}%;height:100%;background:linear-gradient(90deg,{bar_color},{bar_color}99);
                            border-radius:4px;transition:width 0.5s ease"></div>
              </div>

              <!-- 最近扫描的 ETF -->
              <div style="font-size:10px;color:rgba(255,255,255,0.25);text-transform:uppercase;
                          letter-spacing:0;margin-bottom:8px">最近扫描</div>
              <div style="display:flex;flex-wrap:wrap;gap:6px">
                {_build_recent_chips(progress_lines[-8:])}
              </div>
            </div>
            """, unsafe_allow_html=True)

            # ── 实时信号统计（从日志解析）─────────────────────
            bull_n = sum(1 for l in progress_lines if "📈" in l)
            bear_n = sum(1 for l in progress_lines if "📉" in l)
            neut_n = sum(1 for l in progress_lines if "➡️" in l)
            if done_n > 0:
                sc1, sc2, sc3 = st.columns(3)
                with sc1:
                    st.markdown(f'<div style="background:#111;border:1px solid rgba(34,197,94,0.2);border-radius:6px;padding:10px;text-align:center"><div style="font-size:20px;font-weight:700;color:#22c55e">{bull_n}</div><div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:2px">📈 看多</div></div>', unsafe_allow_html=True)
                with sc2:
                    st.markdown(f'<div style="background:#111;border:1px solid rgba(245,158,11,0.2);border-radius:6px;padding:10px;text-align:center"><div style="font-size:20px;font-weight:700;color:#f59e0b">{neut_n}</div><div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:2px">➡️ 中性</div></div>', unsafe_allow_html=True)
                with sc3:
                    st.markdown(f'<div style="background:#111;border:1px solid rgba(239,68,68,0.2);border-radius:6px;padding:10px;text-align:center"><div style="font-size:20px;font-weight:700;color:#ef4444">{bear_n}</div><div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:2px">📉 看空</div></div>', unsafe_allow_html=True)

            # ── 完整日志（折叠）───────────────────────────────
            with st.expander("📋 详细日志", expanded=False):
                st.code("\n".join(all_lines[-20:]) if all_lines else "启动中...", language=None)

            # ── 完成/失败/轮询判断 ─────────────────────────────
            thread   = st.session_state.get("etf50q_thread")
            finished = thread is None or not thread.is_alive()

            if "__DONE__" in log_text:
                st.session_state["etf50q_running"] = False
                st.session_state.pop("etf50q_thread", None)
                st.session_state.pop("etf50q_start_ts", None)
                st.success(f"✅ 全量分析完成！{done_n} 只 ETF · 耗时 {elapsed_str}")
                time.sleep(0.5)
                st.rerun()
            elif "__ERROR__" in log_text:
                st.session_state["etf50q_running"] = False
                st.session_state.pop("etf50q_thread", None)
                st.session_state.pop("etf50q_start_ts", None)
                err = [l for l in all_lines if "ERROR" in l or "Traceback" in l]
                st.error("分析失败：" + "\n".join(err[:4]))
            elif finished and done_n >= pool_n:
                st.session_state["etf50q_running"] = False
                st.session_state.pop("etf50q_thread", None)
                st.session_state.pop("etf50q_start_ts", None)
                st.rerun()
            else:
                # JS 3秒自动刷新
                import streamlit.components.v1 as _cv1
                _cv1.html(
                    '<script>setTimeout(function(){window.parent.location.reload();},3000);</script>',
                    height=0,
                )

        # ── 只有不在运行时才展示结果 ──────────────────────────
        if not st.session_state.get("etf50q_running", False):
            if "load_latest_quant" not in st.session_state:
                from quant.etf50_quant import load_latest_quant as _llq
                st.session_state["load_latest_quant"] = _llq
            data = st.session_state["load_latest_quant"]()
            if data:
                _render_etf50_result(st, data)

    # ════════════════════════════════════════
    #  Tab2: 单只因子分析
    # ════════════════════════════════════════
    with tab2:
        st.markdown('<div style="font-size:11px;font-weight:600;letter-spacing:0;'
                    'text-transform:uppercase;color:rgba(255,255,255,0.3);padding:0 0 10px">'
                    'Alpha158 单只因子诊断</div>', unsafe_allow_html=True)

        c1,c2 = st.columns([2,1])
        with c1: fa_code = st.text_input("ETF/股票代码",value="159611",key="fa_code")
        with c2: fa_days = st.select_slider("天数",[90,180,252],value=180,key="fa_days")

        if st.button("计算因子",key="calc_factor",type="primary"):
            with st.spinner("获取数据并计算 Alpha158 因子..."):
                try:
                    import quant.patch_requests  # noqa
                    from quant.data import get_ohlcv
                    from quant.factors import compute_alpha158
                    df = get_ohlcv(fa_code, days=fa_days)
                    if df.empty:
                        st.error("无数据，请确认代码正确且网络畅通")
                    else:
                        factors = compute_alpha158(df)
                        latest  = factors.iloc[-1].dropna()
                        st.success(f"计算完成：{len(factors.columns)} 个因子 · {len(df)} 天数据")

                        # VnPy ArrayManager 验证
                        try:
                            from quant.vnpy_bridge import FastIndicators
                            fi = FastIndicators(size=300).from_dataframe(df)
                            rsi_vn = fi.rsi(14)
                            macd_vn,_,hist_vn = fi.macd()
                            st.markdown(
                                f'<div style="background:rgba(59,130,246,0.06);border:1px solid rgba(59,130,246,0.2);'
                                f'border-radius:6px;padding:8px 14px;margin:8px 0;font-size:12px;color:rgba(59,130,246,0.9)">'
                                f'VnPy ArrayManager 验证 — RSI(14)={rsi_vn:.1f}  MACD柱={hist_vn:.4f}'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                        except Exception:
                            pass

                        GROUPS = [
                            ("动量",[c for c in latest.index if "ROC" in c]),
                            ("均线偏离",[c for c in latest.index if "BIAS" in c or ("MA" in c and "MACD" not in c)]),
                            ("震荡",[c for c in latest.index if "RSI" in c or "MACD" in c or "BOLL" in c]),
                            ("量价",[c for c in latest.index if "VOL" in c or "VPT" in c or "CORR" in c]),
                            ("波动",[c for c in latest.index if "STD" in c or "ATR" in c]),
                            ("形态",[c for c in latest.index if "SHADOW" in c or "BODY" in c or "MOM" in c]),
                        ]
                        for gname,cols in GROUPS:
                            if not cols: continue
                            vals = latest[cols].sort_values(ascending=False)
                            st.markdown(f'<div style="font-size:11px;color:rgba(255,255,255,0.3);'
                                        f'text-transform:uppercase;letter-spacing:0;padding:10px 0 6px">'
                                        f'{gname}</div>', unsafe_allow_html=True)
                            gcols = st.columns(min(len(vals),6))
                            for col,(fn,fv) in zip(gcols*10, vals.items()):
                                fc="#22c55e" if fv>0.5 else "#ef4444" if fv<-0.5 else "rgba(255,255,255,0.5)"
                                with col:
                                    st.markdown(
                                        f'<div class="n-card n-kpi" style="padding:8px;min-height:66px">'
                                        f'<div style="font-size:10px;color:rgba(255,255,255,0.3);margin-bottom:4px">{fn}</div>'
                                        f'<div style="font-size:15px;font-weight:700;color:{fc}">{fv:.2f}</div></div>',
                                        unsafe_allow_html=True,
                                    )
                except Exception as e:
                    st.error(f"失败：{e}")

    # ════════════════════════════════════════
    #  Tab3: VnPy 专业回测
    # ════════════════════════════════════════
    with tab3:
        st.markdown(
            _card('<div style="font-size:12px;color:rgba(255,255,255,0.45);line-height:1.8">'
                  '<b style="color:#3b82f6">VnPy 回测引擎</b>：专业级绩效计算（含无风险利率修正）+ '
                  'Walk-Forward 时序验证 + 等权/因子两种组合策略</div>', accent="#3b82f6"),
            unsafe_allow_html=True,
        )
        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

        bc1,bc2 = st.columns(2)
        with bc1:
            bt_input = st.text_input("回测标的（逗号分隔）",
                                     value="159611,513160,515880,588200,512480",key="bt_codes")
            bt_strat = st.selectbox("策略",
                                    ["factor_rank","momentum","mean_reversion"],
                                    format_func={"factor_rank":"多因子排名","momentum":"动量","mean_reversion":"均值回归"}.get,
                                    key="bt_strat")
            bt_cap   = st.number_input("初始资金",value=100000,step=10000,min_value=1000,key="bt_cap")
        with bc2:
            bt_days  = st.select_slider("历史天数",[180,252,365,500,730],value=365,key="bt_days")
            bt_rebal = st.selectbox("调仓频率",["W","M","D"],key="bt_rebal",
                                    format_func={"W":"每周","M":"每月","D":"每日"}.get)

        if st.button("▶  VnPy 回测",key="run_bt",type="primary"):
            codes = [c.strip() for c in bt_input.split(",") if c.strip()]
            with st.spinner(f"VnPy 回测 {len(codes)} 只 · {bt_days} 天..."):
                try:
                    import quant.patch_requests  # noqa
                    from quant.data import get_batch_ohlcv
                    from quant.backtest import (Backtester, strategy_factor_rank,
                                                strategy_momentum, strategy_mean_reversion)
                    from quant.vnpy_bridge import calc_performance_vnpy
                    price_data = get_batch_ohlcv(codes, days=bt_days)
                    if not price_data:
                        st.error("无法获取数据，请检查网络")
                    else:
                        fn_map = {"factor_rank":strategy_factor_rank,
                                  "momentum":strategy_momentum,
                                  "mean_reversion":strategy_mean_reversion}
                        bt = Backtester(initial_capital=bt_cap)
                        r  = bt.run(price_data, fn_map[bt_strat], rebalance_freq=bt_rebal)
                        # 用 VnPy 标准绩效计算
                        perf = calc_performance_vnpy(r.equity_curve)
                        st.session_state["bt_result"]  = r
                        st.session_state["bt_perf_vn"] = perf
                except Exception as e:
                    import traceback; st.error(f"回测失败：{e}\n{traceback.format_exc()[-400:]}")

        if "bt_result" in st.session_state:
            r    = st.session_state["bt_result"]
            perf = st.session_state.get("bt_perf_vn", {})
            st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
            st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.3);'
                        'text-transform:uppercase;letter-spacing:0;margin-bottom:8px">'
                        'VnPy 绩效分析</div>', unsafe_allow_html=True)

            m = st.columns(6)
            metrics = [
                ("总收益",   f"{perf.get('total_return', r.total_return):.1%}",
                 "#22c55e" if r.total_return>0 else "#ef4444"),
                ("年化收益", f"{perf.get('annual_return', r.annual_return):.1%}",
                 "#22c55e" if r.annual_return>0 else "#ef4444"),
                ("最大回撤", f"{perf.get('max_drawdown', r.max_drawdown):.1%}", "#ef4444"),
                ("夏普比率", f"{perf.get('sharpe_ratio', r.sharpe_ratio):.2f}",
                 "#22c55e" if r.sharpe_ratio>1 else "#f59e0b"),
                ("卡玛比率", f"{perf.get('calmar_ratio', 0):.2f}",
                 "#22c55e" if perf.get('calmar_ratio',0)>1 else "#f59e0b"),
                ("最长亏损", f"{perf.get('max_losing_streak',0)}天", "#9ca3af"),
            ]
            for col,(lb,v,c) in zip(m, metrics):
                with col: st.markdown(_metric(lb,v,c), unsafe_allow_html=True)

            if not r.equity_curve.empty:
                import pandas as pd

                eq = r.equity_curve
                st.line_chart(pd.DataFrame({"净值":eq/eq.iloc[0]}),color=["#3b82f6"],height=200)

            # Walk-Forward 验证
            if len(r.equity_curve) >= 60:
                with st.expander("🔬 Walk-Forward 样本内外对比", expanded=False):
                    split = int(len(r.equity_curve)*0.7)
                    if "overfit_diagnosis" not in st.session_state:
                        from quant.anti_overfit import overfit_diagnosis as _od
                        st.session_state["overfit_diagnosis"] = _od
                    def _m(e):
                        rd=e.pct_change().dropna(); t=e.iloc[-1]/e.iloc[0]-1
                        a=(1+t)**(252/len(e))-1; s=rd.mean()/(rd.std()+1e-9)*252**0.5
                        d=((e-e.cummax())/e.cummax()).min()
                        return {"annual_return":a,"sharpe_ratio":s,"max_drawdown":d}
                    is_m=_m(r.equity_curve.iloc[:split])
                    os_m=_m(r.equity_curve.iloc[split:])
                    diag=st.session_state["overfit_diagnosis"](is_m,os_m,"当前策略")
                    rc={"低":"#22c55e","中":"#f59e0b","高⚠️":"#ef4444"}.get(diag["overfit_risk"],"#9ca3af")
                    st.markdown(
                        f'<div style="background:rgba(255,255,255,0.04);border-left:2px solid {rc};'
                        f'border-radius:4px;padding:10px 14px;font-size:12px;color:{rc}">'
                        f'过拟合风险：{diag["overfit_risk"]} &nbsp; {diag["verdict"]}<br>'
                        f'<span style="color:rgba(255,255,255,0.4)">{diag["recommendation"]}</span>'
                        f'</div>', unsafe_allow_html=True,
                    )

    # ════════════════════════════════════════
    #  Tab4: 参数优化（VnPy 网格搜索）
    # ════════════════════════════════════════
    with tab4:
        st.markdown(_card(
            '<div style="font-size:12px;color:rgba(255,255,255,0.45);line-height:1.8">'
            '<b style="color:#3b82f6">VnPy 参数网格优化</b>：遍历参数组合，找到最优 Top-N 和调仓频率</div>',
            accent="#3b82f6"
        ), unsafe_allow_html=True)
        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

        oc1,oc2 = st.columns(2)
        with oc1:
            opt_codes = st.text_input("优化标的",value="159611,513160,515880,588200,512480,588000",key="opt_codes")
            opt_days  = st.select_slider("历史天数",[180,252,365],value=252,key="opt_days")
            opt_metric= st.selectbox("优化目标",["sharpe_ratio","annual_return","calmar_ratio"],key="opt_metric")
        with oc2:
            st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.35);padding:4px 0 6px">'
                        '参数网格（固定范围，自动遍历）</div>', unsafe_allow_html=True)
            st.markdown(_card(
                '<div style="font-size:12px;color:rgba(255,255,255,0.5);line-height:1.9">'
                'Top-N：[3, 5, 7, 10]<br>调仓频率：[W, M]<br>'
                f'组合数：8 种</div>'
            ), unsafe_allow_html=True)

        if st.button("🔍  开始参数优化",key="run_opt",type="primary"):
            codes = [c.strip() for c in opt_codes.split(",") if c.strip()]
            with st.spinner("网格搜索中（8 种参数组合）..."):
                try:
                    import quant.patch_requests  # noqa
                    from quant.data import get_batch_ohlcv
                    from quant.vnpy_bridge import optimize_strategy_params
                    price_data = get_batch_ohlcv(codes, days=opt_days)
                    if not price_data:
                        st.error("无法获取数据")
                    else:
                        param_grid = {"top_n":[3,5,7,10],"rebalance":["W","M"]}
                        result_df  = optimize_strategy_params(
                            price_data, param_grid,
                            metric=opt_metric, top_k=8
                        )
                        st.session_state["opt_result"] = result_df
                except Exception as e:
                    st.error(f"参数优化失败：{e}")

        if "opt_result" in st.session_state:
            df = st.session_state["opt_result"]
            st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
            st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.3);'
                        'text-transform:uppercase;letter-spacing:0;margin-bottom:8px">'
                        '参数优化结果（按目标指标排序）</div>', unsafe_allow_html=True)

            if "error" not in df.columns:
                # 表头
                cols_show = ["top_n","rebalance","sharpe_ratio","annual_return","max_drawdown","calmar_ratio"]
                cols_show = [c for c in cols_show if c in df.columns]
                st.markdown(
                    '<div style="display:grid;grid-template-columns:40px 60px 80px 90px 90px 90px 90px;'
                    'gap:8px;padding:6px 12px;font-size:10px;color:rgba(255,255,255,0.3);'
                    'text-transform:uppercase;border-bottom:1px solid rgba(255,255,255,0.06)">'
                    '<div>#</div><div>Top-N</div><div>频率</div>'
                    '<div>夏普</div><div>年化</div><div>回撤</div><div>卡玛</div></div>',
                    unsafe_allow_html=True,
                )
                for i,row in df.iterrows():
                    rank = list(df.index).index(i)+1
                    medal={1:"🥇",2:"🥈",3:"🥉"}.get(rank,"  ")
                    sr   = row.get("sharpe_ratio",0)
                    ar   = row.get("annual_return",0)
                    dd   = row.get("max_drawdown",0)
                    cr   = row.get("calmar_ratio",0)
                    sc   = "#22c55e" if sr>1 else "#f59e0b" if sr>0.5 else "#ef4444"
                    st.markdown(
                        f'<div style="display:grid;grid-template-columns:40px 60px 80px 90px 90px 90px 90px;'
                        f'gap:8px;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.03);'
                        f'align-items:center">'
                        f'<div style="font-size:13px">{medal}</div>'
                        f'<div style="font-size:13px;font-weight:700;color:#fff">{int(row.get("top_n",5))}</div>'
                        f'<div style="font-size:12px;color:rgba(255,255,255,0.5)">{row.get("rebalance","")}</div>'
                        f'<div style="font-size:13px;font-weight:600;color:{sc}">{sr:.2f}</div>'
                        f'<div style="font-size:13px;color:{"#22c55e" if ar>0 else "#ef4444"}">{ar:.1%}</div>'
                        f'<div style="font-size:13px;color:#ef4444">{dd:.1%}</div>'
                        f'<div style="font-size:13px;color:{"#22c55e" if cr>1 else "#f59e0b"}">{cr:.2f}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                # 最优推荐
                best = df.iloc[0]
                st.markdown(
                    f'<div style="background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.2);'
                    f'border-radius:6px;padding:10px 14px;margin-top:10px;font-size:12px;'
                    f'color:rgba(34,197,94,0.9)">'
                    f'✓ 最优参数：Top-{int(best.get("top_n",5))} 等权 · '
                    f'{best.get("rebalance","")} 调仓 · '
                    f'夏普={best.get("sharpe_ratio",0):.2f} · '
                    f'年化={best.get("annual_return",0):.1%}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ════════════════════════════════════════
    #  Tab5: 过拟合诊断
    # ════════════════════════════════════════
    with tab5:
        st.markdown('<div style="font-size:11px;font-weight:600;letter-spacing:0;'
                    'text-transform:uppercase;color:rgba(255,255,255,0.3);padding:0 0 10px">'
                    '过拟合风险诊断</div>', unsafe_allow_html=True)
        st.markdown(_card(
            '<div style="font-size:12px;color:rgba(255,255,255,0.45);line-height:1.9">'
            '四项检验：'
            '<b style="color:#3b82f6">①</b> 样本外 Sharpe 衰减 &lt;50%&nbsp;'
            '<b style="color:#3b82f6">②</b> 样本外回撤 &lt;样本内2倍&nbsp;'
            '<b style="color:#3b82f6">③</b> 样本外年化收益 &gt;0&nbsp;'
            '<b style="color:#3b82f6">④</b> 参数扰动后 Sharpe 变化 &lt;40%'
            '</div>'
        ), unsafe_allow_html=True)
        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

        if "bt_result" not in st.session_state:
            st.info("请先在「VnPy 回测」Tab 运行一次回测")
        else:
            r  = st.session_state["bt_result"]
            eq = r.equity_curve
            if len(eq) < 60:
                st.warning("净值数据不足 60 天，无法诊断")
            else:
                split = int(len(eq)*0.7)
                def _m(e):
                    rd=e.pct_change().dropna(); t=e.iloc[-1]/e.iloc[0]-1
                    a=(1+t)**(252/len(e))-1; s=rd.mean()/(rd.std()+1e-9)*252**0.5
                    d=((e-e.cummax())/e.cummax()).min()
                    return {"annual_return":a,"sharpe_ratio":s,"max_drawdown":d}
                is_m=_m(eq.iloc[:split]); os_m=_m(eq.iloc[split:])
                from quant.anti_overfit import overfit_diagnosis
                diag=overfit_diagnosis(is_m,os_m,"当前策略")
                rc={"低":"#22c55e","中":"#f59e0b","高⚠️":"#ef4444"}.get(diag["overfit_risk"],"#9ca3af")

                st.markdown(
                    f'<div style="background:rgba(255,255,255,0.03);border-left:2px solid {rc};'
                    f'border-radius:6px;padding:14px 16px;margin-bottom:14px">'
                    f'<div style="font-size:14px;font-weight:700;color:{rc};margin-bottom:6px">'
                    f'过拟合风险：{diag["overfit_risk"]}  {diag["verdict"]}</div>'
                    f'<div style="font-size:12px;color:rgba(255,255,255,0.4)">{diag["recommendation"]}</div>'
                    f'</div>', unsafe_allow_html=True,
                )

                d1,d2=st.columns(2)
                for col,(lb,m) in zip([d1,d2],[("📚 样本内（前70%）",is_m),("🧪 样本外（后30%）",os_m)]):
                    with col:
                        rows=""
                        for ml,key,gfn in [("年化收益","annual_return",lambda v:v>0),
                                            ("夏普比率","sharpe_ratio",lambda v:v>1),
                                            ("最大回撤","max_drawdown",lambda v:v>-0.2)]:
                            v=m[key]; mc="#22c55e" if gfn(v) else "#ef4444"
                            vs=f"{v:.1%}" if key!="sharpe_ratio" else f"{v:.2f}"
                            rows+=(f'<div style="display:flex;justify-content:space-between;'
                                   f'padding:7px 0;border-bottom:1px solid rgba(255,255,255,0.04)">'
                                   f'<span style="color:rgba(255,255,255,0.4);font-size:12px">{ml}</span>'
                                   f'<span style="color:{mc};font-weight:600;font-size:13px;'
                                   f'font-variant-numeric:tabular-nums">{vs}</span></div>')
                        st.markdown(
                            f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
                            f'border-radius:6px;padding:14px 16px"><div style="font-size:11px;'
                            f'color:rgba(255,255,255,0.3);text-transform:uppercase;letter-spacing:0;'
                            f'margin-bottom:10px">{lb}</div>{rows}</div>',
                            unsafe_allow_html=True,
                        )

                decay=diag["sharpe_decay"]; ddr=diag["dd_ratio"]
                da1,da2=st.columns(2)
                with da1:
                    dc="#ef4444" if decay>0.5 else "#22c55e"
                    st.markdown(_metric("Sharpe 衰减",f"{decay:.0%}",dc,
                                        "⚠️ 过高" if decay>0.5 else "✓ 合格"), unsafe_allow_html=True)
                with da2:
                    dc2="#ef4444" if ddr>2 else "#22c55e"
                    st.markdown(_metric("回撤恶化",f"{ddr:.1f}x",dc2,
                                        "⚠️ >2倍" if ddr>2 else "✓ 合格"), unsafe_allow_html=True)

                for issue in diag.get("issues",[]):
                    st.markdown(
                        f'<div style="background:rgba(239,68,68,0.05);border-left:2px solid #ef4444;'
                        f'border-radius:4px;padding:8px 12px;margin:4px 0;font-size:12px;color:#ef4444">'
                        f'{issue}</div>', unsafe_allow_html=True,
                    )

    # ════════════════════════════════════════
    #  Tab6: 置信度训练
    # ════════════════════════════════════════
    with tab6:
        try:
            from confidence_page import render_confidence_page
            render_confidence_page(st)
        except Exception as _e:
            import traceback
            st.error(f"置信度训练加载失败：{_e}")
            st.code(traceback.format_exc())


# ══════════════════════════════════════════
#  ETF50 结果展示（与运行状态分离）
# ══════════════════════════════════════════
def _render_etf50_result(st, data: dict):
    ts      = data.get("datetime","")[:16]
    total   = data.get("total",0)
    success = data.get("success",0)
    bull    = data.get("bullish",0)
    bear    = data.get("bearish",0)
    neut    = data.get("neutral",0)

    st.markdown(f'<div style="height:14px"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:11px;color:rgba(255,255,255,0.25);margin-bottom:10px">'
        f'上次量化扫描：{ts} · {success}/{total} 只有效</div>',
        unsafe_allow_html=True,
    )

    # 统计
    s1,s2,s3,s4 = st.columns(4)
    for col,(lb,v,c) in zip([s1,s2,s3,s4],[
        ("📈 看多",bull,"#22c55e"),("→ 中性",neut,"#f59e0b"),
        ("📉 看空",bear,"#ef4444"),("✅ 有效",success,"#3b82f6"),
    ]):
        with col: st.markdown(_metric(lb,str(v),c), unsafe_allow_html=True)

    # 组合回测
    bt = data.get("backtest",{})
    if bt and bt.get("sharpe_ratio") is not None:
        top_n = data.get("top_n",5)
        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
        mc1,mc2,mc3,mc4 = st.columns(4)
        for col,(lb,v,c) in zip([mc1,mc2,mc3,mc4],[
            ("组合总收益",f"{bt.get('total_return',0):.1%}","#22c55e" if bt.get('total_return',0)>0 else "#ef4444"),
            ("年化收益",  f"{bt.get('annual_return',0):.1%}","#22c55e" if bt.get('annual_return',0)>0 else "#ef4444"),
            ("夏普比率",  f"{bt.get('sharpe_ratio',0):.2f}","#22c55e" if bt.get('sharpe_ratio',0)>1 else "#f59e0b"),
            ("最大回撤",  f"{bt.get('max_drawdown',0):.1%}","#ef4444"),
        ]):
            with col: st.markdown(_metric(lb,v,c,f"Top-{top_n}等权组合"), unsafe_allow_html=True)

        eq_list = bt.get("equity_curve",[])
        if eq_list and len(eq_list)>5:
            import pandas as pd

            eq = pd.Series(eq_list)
            st.line_chart(pd.DataFrame({"净值":eq/eq.iloc[0]}),color=["#3b82f6"],height=150)

    # 前三名
    top3 = [r for r in data.get("top3",[]) if r.get("has_data", True)]
    if top3:
        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.3);'
                    'text-transform:uppercase;letter-spacing:0;margin-bottom:8px">'
                    '🏆 量化前三名</div>', unsafe_allow_html=True)
        t1,t2,t3 = st.columns(3)
        for col,r,medal,acc in zip([t1,t2,t3],top3,["🥇","🥈","🥉"],["#22c55e","#3b82f6","#f59e0b"]):
            sc = r.get("quant_score",0)
            reasons = " · ".join(r.get("reasons",[])[:2])
            with col:
                st.markdown(
                    f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
                    f'border-top:1px solid {acc};border-radius:6px;padding:14px">'
                    f'<div style="font-size:15px;margin-bottom:6px">{medal}</div>'
                    f'<div style="font-size:11px;color:rgba(255,255,255,0.3)">{r.get("code","")}</div>'
                    f'<div style="font-size:14px;font-weight:600;color:#fff;margin:3px 0">{r.get("name","")}</div>'
                    f'<div style="font-size:24px;font-weight:700;color:{acc};'
                    f'font-variant-numeric:tabular-nums;margin:6px 0">{sc:.0f}<span style="font-size:12px;'
                    f'color:rgba(255,255,255,0.3)"> 分</span></div>'
                    f'{_bar(sc)}'
                    f'<div style="font-size:11px;color:rgba(255,255,255,0.3);margin-top:6px">{reasons}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # 完整排行
    results = [r for r in data.get("results",[]) if r.get("has_data", True)]
    if not results:
        return

    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
    fc1,fc2 = st.columns([1,2])
    with fc1:
        sig_f = st.selectbox("",["全部","看多","中性","看空"],
                             key="etfq_sig",label_visibility="collapsed")
    with fc2:
        cats  = ["全部类别"]+sorted(set(r.get("category","") for r in results if r.get("category")))
        cat_f = st.selectbox("",cats,key="etfq_cat",label_visibility="collapsed")

    sm = {"全部":None,"看多":"bullish","中性":"neutral","看空":"bearish"}
    filtered = [r for r in results if
                (not sm[sig_f] or r.get("signal")==sm[sig_f]) and
                (cat_f=="全部类别" or r.get("category","")==cat_f)]

    # 表头
    st.markdown(
        '<div style="display:grid;grid-template-columns:44px 70px 1fr 110px 64px 80px 110px 80px;'
        'gap:6px;padding:6px 12px;font-size:10px;color:rgba(255,255,255,0.25);'
        'text-transform:uppercase;letter-spacing:0;'
        'border-bottom:1px solid rgba(255,255,255,0.06);margin-top:8px">'
        '<div>#</div><div>代码</div><div>名称</div><div>类别</div>'
        '<div style="text-align:right">量化分</div><div style="text-align:center">信号</div>'
        '<div>关键因子</div><div>进度</div></div>',
        unsafe_allow_html=True,
    )

    for i,r in enumerate(filtered,1):
        sc   = r.get("quant_score",0)
        sig  = r.get("signal","neutral")
        sc_c = _sc(sc)
        reasons = " · ".join(r.get("reasons",[])[:2])
        medal = {1:"🥇",2:"🥈",3:"🥉"}.get(i,"")
        bg = "rgba(255,255,255,0.015)" if i%2==0 else "transparent"

        st.markdown(
            f'<div style="display:grid;grid-template-columns:44px 70px 1fr 110px 64px 80px 110px 80px;'
            f'gap:6px;padding:8px 12px;background:{bg};border-radius:4px;align-items:center;'
            f'border-bottom:1px solid rgba(255,255,255,0.025)">'
            f'<div style="font-size:12px;color:rgba(255,255,255,0.2)">{medal}{i}</div>'
            f'<div style="font-size:12px;color:rgba(255,255,255,0.45);font-family:monospace">{r.get("code","")}</div>'
            f'<div style="font-size:13px;font-weight:500;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{r.get("name","")}</div>'
            f'<div style="font-size:10px;color:rgba(255,255,255,0.25);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{r.get("category","")}</div>'
            f'<div style="text-align:right;font-size:14px;font-weight:700;color:{sc_c};font-variant-numeric:tabular-nums">{sc:.0f}</div>'
            f'<div style="text-align:center">{_sig(sig)}</div>'
            f'<div style="font-size:11px;color:rgba(255,255,255,0.3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{reasons}</div>'
            f'<div>{_bar(sc)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

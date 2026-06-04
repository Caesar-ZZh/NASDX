"""
NASDX V2 — 量化策略页面
"""
from __future__ import annotations
import sys, json, glob, subprocess, threading
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════
#  工具
# ══════════════════════════════════════════
def _sc(v):
    return "#22c55e" if v >= 60 else "#ef4444" if v <= 40 else "#f59e0b"

def _bar(v, h=4):
    c = _sc(v)
    return (f'<div style="background:rgba(255,255,255,0.06);border-radius:2px;height:{h}px;overflow:hidden">'
            f'<div style="width:{min(v,100):.0f}%;height:100%;background:{c};border-radius:2px"></div></div>')

def _sig_html(sig):
    cfg = {"bullish":("#22c55e","rgba(34,197,94,0.12)","↑ 看多"),
           "bearish":("#ef4444","rgba(239,68,68,0.12)","↓ 看空"),
           "neutral":("#f59e0b","rgba(245,158,11,0.10)","→ 中性")}
    c, bg, lb = cfg.get(sig, ("#9ca3af","rgba(156,163,175,0.1)","— 无"))
    return (f'<span style="color:{c};background:{bg};border:1px solid {c}40;'
            f'border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600">{lb}</span>')

def _card(content, accent=None):
    top = f"background:linear-gradient(90deg,transparent,{accent},transparent)" if accent else "none"
    return (f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);border-radius:8px;'
            f'padding:16px;position:relative;overflow:hidden">'
            f'<div style="position:absolute;top:0;left:0;right:0;height:1px;{top}"></div>'
            f'{content}</div>')


def _run_etf50_bg(days, top_n, freq, log_path):
    """后台线程运行 ETF50 量化"""
    import sys, json
    sys.path.insert(0, str(ROOT))
    with open(log_path, "w", encoding="utf-8", buffering=1) as log:
        try:
            import quant.patch_requests  # noqa
            from quant.etf50_quant import run_etf50_quant

            class Tee:
                def write(self, msg):
                    log.write(msg); log.flush()
                def flush(self): log.flush()

            import builtins
            _orig_print = builtins.print
            def _tee_print(*args, **kw):
                import io
                buf = io.StringIO()
                _orig_print(*args, file=buf, **kw)
                log.write(buf.getvalue()); log.flush()
                _orig_print(*args, **kw)
            builtins.print = _tee_print

            result = run_etf50_quant(days=days, top_n=top_n,
                                     rebalance_freq=freq, verbose=True)
            builtins.print = _orig_print
            log.write(f"\n__DONE__:{result.get('_saved_to','')}\n")
        except Exception as e:
            import traceback
            log.write(f"\n__ERROR__:{e}\n{traceback.format_exc()}\n")


# ══════════════════════════════════════════
#  主页面
# ══════════════════════════════════════════
def render_quant_page(st):
    st.markdown(
        '<div style="padding:20px 0 16px"><div style="font-size:22px;font-weight:700;color:#fff">量化策略引擎</div>'
        '<div style="font-size:12px;color:rgba(255,255,255,0.4);margin-top:3px">'
        'Alpha158 因子 · Walk-Forward 回测 · 50只ETF全量分析 · 抗过拟合验证</div></div>',
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3, tab4 = st.tabs(["🚀 ETF50 量化扫描", "📐 因子分析", "📈 策略回测", "🛡️ 过拟合诊断"])

    # ════════════════════════════════════════
    #  Tab1: ETF50 全量量化
    # ════════════════════════════════════════
    with tab1:
        # 说明
        st.markdown(_card(
            '<div style="font-size:12px;color:rgba(255,255,255,0.5);line-height:1.9">'
            '对 <b style="color:#fff">50 只主流 ETF</b> 执行完整量化分析：<br>'
            '① 获取历史 OHLCV 数据 &nbsp;→&nbsp; '
            '② 计算 <b style="color:#3b82f6">Alpha158</b> 80个因子 &nbsp;→&nbsp; '
            '③ 多因子合成评分 &nbsp;→&nbsp; '
            '④ Top-N 组合 Walk-Forward 回测 &nbsp;→&nbsp; '
            '⑤ 量化排行榜 + 操作建议'
            '</div>', accent="#3b82f6"
        ), unsafe_allow_html=True)

        st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

        # 参数
        p1, p2, p3 = st.columns(3)
        with p1:
            days = st.select_slider("历史数据天数", [90, 180, 252, 365], value=252,
                                    help="天数越长因子越稳定，但数据获取时间越长")
        with p2:
            top_n = st.slider("组合只数 Top-N", 3, 10, 5,
                               help="选取因子评分最高的 N 只 ETF 构建等权组合进行回测")
        with p3:
            freq  = st.selectbox("调仓频率", ["W 每周","M 每月","D 每日"], index=0).split()[0]

        est_min = max(3, len(json.load(open(ROOT/"etf50_pool.json",encoding="utf-8"))["etfs"]) * days // 5000)
        st.caption(f"预计耗时约 {est_min}-{est_min*2} 分钟（受网络速度影响）")

        col_btn, col_status = st.columns([1, 3])
        with col_btn:
            run_btn = st.button("▶  开始全量分析", key="run_etf50_quant",
                                type="primary", use_container_width=True,
                                disabled=st.session_state.get("etf50q_running", False))

        # 触发后台任务
        if run_btn:
            log_path = ROOT / "etf50_quant_log.txt"
            log_path.write_text("", encoding="utf-8")
            t = threading.Thread(
                target=_run_etf50_bg,
                args=(days, top_n, freq, str(log_path)),
                daemon=True
            )
            t.start()
            st.session_state["etf50q_running"] = True
            st.session_state["etf50q_thread"]  = t
            st.session_state["etf50q_log"]     = str(log_path)
            st.rerun()

        # 运行中状态
        if st.session_state.get("etf50q_running"):
            log_path = Path(st.session_state.get("etf50q_log",""))
            log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            lines = [l for l in log_text.splitlines() if l.strip()]

            # 进度
            done_lines = sum(1 for l in lines if any(x in l for x in ["✅","❌","⚠️","📈","📉","➡️"]))
            total_pool = 50
            pct = min(done_lines / total_pool, 0.95)
            st.progress(pct, text=f"分析中... {done_lines}/{total_pool}")

            with st.expander("📟 实时日志", expanded=True):
                recent = "\n".join(lines[-20:]) if lines else "启动中..."
                st.code(recent, language=None)

            # 检查完成
            thread = st.session_state.get("etf50q_thread")
            thread_done = thread is None or not thread.is_alive()
            if "__DONE__" in log_text or (thread_done and done_lines > 5):
                st.session_state["etf50q_running"] = False
                st.session_state.pop("etf50q_thread", None)
                # 找到保存的文件路径
                for line in lines:
                    if "__DONE__:" in line:
                        saved = line.split("__DONE__:")[-1].strip()
                        st.session_state["etf50q_result"] = saved
                        break
                st.rerun()
            elif "__ERROR__" in log_text and thread_done:
                st.session_state["etf50q_running"] = False
                err_lines = [l for l in lines if "__ERROR__" in l or "Traceback" in l]
                st.error("分析失败：" + "\n".join(err_lines[:5]))
            else:
                import time; time.sleep(3); st.rerun()

        # 展示结果
        from quant.etf50_quant import load_latest_quant
        data = load_latest_quant()

        if data:
            _show_etf50_result(st, data)

    # ════════════════════════════════════════
    #  Tab2: 单只因子分析
    # ════════════════════════════════════════
    with tab2:
        st.markdown('<div style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:rgba(255,255,255,0.3);padding:0 0 10px">Alpha158 单只因子诊断</div>', unsafe_allow_html=True)

        c1, c2 = st.columns([2,1])
        with c1:
            fa_code = st.text_input("ETF / 股票代码", value="159611", label_visibility="visible", key="fa_code")
        with c2:
            fa_days = st.select_slider("天数", [90,180,252], value=180, key="fa_days")

        if st.button("计算因子", key="calc_factor", type="primary"):
            with st.spinner("获取数据并计算因子..."):
                try:
                    import quant.patch_requests  # noqa
                    from quant.data import get_ohlcv
                    from quant.factors import compute_alpha158
                    df = get_ohlcv(fa_code, days=fa_days)
                    if df.empty:
                        st.error("无数据，请确认代码正确且网络畅通")
                    else:
                        factors = compute_alpha158(df)
                        latest = factors.iloc[-1].dropna()
                        st.success(f"计算完成：{len(factors.columns)} 个因子 · {len(df)} 天数据")

                        # 分组展示
                        GROUPS = [
                            ("动量", [c for c in latest.index if "ROC" in c]),
                            ("均线偏离", [c for c in latest.index if "BIAS" in c or "MA" in c]),
                            ("震荡", [c for c in latest.index if "RSI" in c or "MACD" in c or "BOLL" in c]),
                            ("量价", [c for c in latest.index if "VOL" in c or "VPT" in c or "CORR" in c]),
                            ("波动", [c for c in latest.index if "STD" in c or "ATR" in c]),
                            ("形态", [c for c in latest.index if "SHADOW" in c or "BODY" in c or "MOM" in c]),
                        ]
                        for gname, cols in GROUPS:
                            if not cols: continue
                            vals = latest[cols].sort_values(ascending=False)
                            st.markdown(f'<div style="font-size:11px;color:rgba(255,255,255,0.35);font-weight:600;text-transform:uppercase;letter-spacing:.05em;padding:10px 0 6px">{gname}</div>', unsafe_allow_html=True)
                            gcols = st.columns(min(len(vals), 6))
                            for col, (fn, fv) in zip(gcols * 10, vals.items()):
                                fc = "#22c55e" if fv > 0.5 else "#ef4444" if fv < -0.5 else "rgba(255,255,255,0.5)"
                                with col:
                                    st.markdown(
                                        f'<div style="background:#161616;border:1px solid rgba(255,255,255,0.06);border-radius:6px;padding:8px;text-align:center">'
                                        f'<div style="font-size:10px;color:rgba(255,255,255,0.3);margin-bottom:4px">{fn}</div>'
                                        f'<div style="font-size:15px;font-weight:700;color:{fc}">{fv:.2f}</div>'
                                        f'</div>',
                                        unsafe_allow_html=True,
                                    )
                except Exception as e:
                    st.error(f"因子计算失败：{e}")

    # ════════════════════════════════════════
    #  Tab3: 策略回测
    # ════════════════════════════════════════
    with tab3:
        st.markdown('<div style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:rgba(255,255,255,0.3);padding:0 0 10px">Walk-Forward 策略回测</div>', unsafe_allow_html=True)

        st.markdown(_card(
            '<div style="font-size:12px;color:rgba(255,255,255,0.45);line-height:1.8">'
            '<b style="color:#3b82f6">Walk-Forward</b>：滚动训练窗口，测试窗口严格在训练之后——杜绝未来数据泄漏。'
            '</div>'
        ), unsafe_allow_html=True)

        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

        bc1, bc2 = st.columns(2)
        with bc1:
            bt_codes_input = st.text_input("回测标的（逗号分隔）", value="159611,513160,515880,588200,512480", key="bt_codes")
            bt_strategy    = st.selectbox("策略", ["factor_rank","momentum","mean_reversion"], key="bt_strat",
                                          format_func={"factor_rank":"多因子排名","momentum":"动量","mean_reversion":"均值回归"}.get)
            bt_capital     = st.number_input("初始资金（元）", value=100000, step=10000, key="bt_cap")
        with bc2:
            bt_days  = st.select_slider("历史天数", [180, 365, 500, 730], value=365, key="bt_days")
            bt_rebal = st.selectbox("调仓频率", ["W","M","D"], key="bt_rebal",
                                    format_func={"W":"每周","M":"每月","D":"每日"}.get)
            train_w = st.slider("训练窗口（天）", 60, 252, 126, key="bt_train")

        if st.button("▶  开始回测", key="run_bt", type="primary"):
            bt_codes = [c.strip() for c in bt_codes_input.split(",") if c.strip()]
            with st.spinner(f"回测 {len(bt_codes)} 只标的 · {bt_days} 天..."):
                try:
                    import quant.patch_requests  # noqa
                    from quant.data import get_batch_ohlcv
                    from quant.backtest import (Backtester, strategy_factor_rank,
                                                strategy_momentum, strategy_mean_reversion)
                    price_data = get_batch_ohlcv(bt_codes, days=bt_days)
                    if not price_data:
                        st.error("无法获取数据，请检查网络和代码")
                    else:
                        fn_map = {"factor_rank":strategy_factor_rank,
                                  "momentum":strategy_momentum,
                                  "mean_reversion":strategy_mean_reversion}
                        bt = Backtester(initial_capital=bt_capital)
                        r  = bt.run(price_data, fn_map[bt_strategy], rebalance_freq=bt_rebal)
                        st.session_state["bt_result"] = r
                except Exception as e:
                    import traceback
                    st.error(f"回测失败：{e}\n{traceback.format_exc()[-500:]}")

        if "bt_result" in st.session_state:
            r = st.session_state["bt_result"]
            st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

            m1,m2,m3,m4,m5,m6 = st.columns(6)
            metrics = [
                ("总收益",   f"{r.total_return:.1%}",  "#22c55e" if r.total_return>0 else "#ef4444"),
                ("年化收益", f"{r.annual_return:.1%}", "#22c55e" if r.annual_return>0 else "#ef4444"),
                ("最大回撤", f"{r.max_drawdown:.1%}",  "#ef4444"),
                ("夏普比率", f"{r.sharpe_ratio:.2f}",  "#22c55e" if r.sharpe_ratio>1 else "#f59e0b"),
                ("胜率",     f"{r.win_rate:.1%}",      "#22c55e" if r.win_rate>0.5 else "#f59e0b"),
                ("交易笔数", str(r.total_trades),      "#3b82f6"),
            ]
            for col,(lb,v,c) in zip([m1,m2,m3,m4,m5,m6], metrics):
                with col:
                    st.markdown(
                        f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);border-radius:6px;padding:12px;text-align:center">'
                        f'<div style="font-size:18px;font-weight:700;color:{c};font-variant-numeric:tabular-nums">{v}</div>'
                        f'<div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;text-transform:uppercase;letter-spacing:.05em">{lb}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            if not r.equity_curve.empty:
                eq = r.equity_curve
                st.line_chart(pd.DataFrame({"净值曲线": eq / eq.iloc[0]}),
                              color=["#3b82f6"], height=220)

            if r.sharpe_ratio < 0.5:
                st.markdown(_card('<div style="font-size:12px;color:#ef4444">⚠️ 夏普比率低于 0.5，策略实盘表现存疑</div>', "#ef4444"), unsafe_allow_html=True)

    # ════════════════════════════════════════
    #  Tab4: 过拟合诊断
    # ════════════════════════════════════════
    with tab4:
        st.markdown('<div style="font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:rgba(255,255,255,0.3);padding:0 0 10px">过拟合风险诊断</div>', unsafe_allow_html=True)
        st.markdown(_card(
            '<div style="font-size:12px;color:rgba(255,255,255,0.45);line-height:1.9">'
            '四项检验：'
            '<b style="color:#3b82f6">①</b> 样本外 Sharpe 衰减 &lt;50%&nbsp;'
            '<b style="color:#3b82f6">②</b> 样本外回撤 &lt;样本内2倍&nbsp;'
            '<b style="color:#3b82f6">③</b> 样本外年化收益 &gt;0%&nbsp;'
            '<b style="color:#3b82f6">④</b> 参数扰动后 Sharpe 变化 &lt;40%'
            '</div>'
        ), unsafe_allow_html=True)

        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

        if "bt_result" not in st.session_state:
            st.info("请先在「策略回测」Tab 运行一次回测")
        else:
            r  = st.session_state["bt_result"]
            eq = r.equity_curve
            if len(eq) < 60:
                st.warning("数据不足（需至少 60 天净值数据）")
            else:
                split = int(len(eq) * 0.7)
                def _m(e):
                    rd = e.pct_change().dropna()
                    t  = e.iloc[-1]/e.iloc[0] - 1
                    a  = (1+t)**(252/len(e)) - 1
                    s  = rd.mean()/(rd.std()+1e-9)*252**0.5
                    d  = ((e - e.cummax())/e.cummax()).min()
                    return {"annual_return":a,"sharpe_ratio":s,"max_drawdown":d}

                is_m = _m(eq.iloc[:split])
                os_m = _m(eq.iloc[split:])

                from quant.anti_overfit import overfit_diagnosis
                diag = overfit_diagnosis(is_m, os_m, "当前策略")
                rc = {"低":"#22c55e","中":"#f59e0b","高⚠️":"#ef4444"}.get(diag["overfit_risk"],"#9ca3af")

                # 风险总结
                st.markdown(
                    f'<div style="background:#111;border:1px solid {rc}40;border-left:2px solid {rc};'
                    f'border-radius:6px;padding:14px 16px;margin-bottom:14px">'
                    f'<div style="font-size:14px;font-weight:700;color:{rc};margin-bottom:6px">'
                    f'过拟合风险：{diag["overfit_risk"]}  {diag["verdict"]}</div>'
                    f'<div style="font-size:12px;color:rgba(255,255,255,0.45)">{diag["recommendation"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # 对比表
                d1, d2 = st.columns(2)
                for col, (lb, m) in zip([d1,d2],[("📚 样本内（前70%）",is_m),("🧪 样本外（后30%）",os_m)]):
                    with col:
                        rows_html = ""
                        for metric_lb, key, good_fn in [
                            ("年化收益","annual_return",lambda v:v>0),
                            ("夏普比率","sharpe_ratio", lambda v:v>1),
                            ("最大回撤","max_drawdown", lambda v:v>-0.2),
                        ]:
                            v   = m[key]
                            mvc = "#22c55e" if good_fn(v) else "#ef4444"
                            vs  = f"{v:.1%}" if key!="sharpe_ratio" else f"{v:.2f}"
                            rows_html += (
                                f'<div style="display:flex;justify-content:space-between;'
                                f'padding:7px 0;border-bottom:1px solid rgba(255,255,255,0.04)">'
                                f'<span style="color:rgba(255,255,255,0.4);font-size:12px">{metric_lb}</span>'
                                f'<span style="color:{mvc};font-weight:600;font-size:13px;font-variant-numeric:tabular-nums">{vs}</span>'
                                f'</div>'
                            )
                        st.markdown(
                            f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
                            f'border-radius:6px;padding:14px 16px">'
                            f'<div style="font-size:11px;color:rgba(255,255,255,0.3);'
                            f'text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px">{lb}</div>'
                            f'{rows_html}</div>',
                            unsafe_allow_html=True,
                        )

                decay = diag["sharpe_decay"]
                dd_r  = diag["dd_ratio"]
                da1, da2 = st.columns(2)
                with da1:
                    dc = "#ef4444" if decay > 0.5 else "#22c55e"
                    st.markdown(
                        f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);border-radius:6px;padding:12px;text-align:center;margin-top:10px">'
                        f'<div style="font-size:10px;color:rgba(255,255,255,0.3);text-transform:uppercase;letter-spacing:.05em">Sharpe 衰减</div>'
                        f'<div style="font-size:22px;font-weight:700;color:{dc};margin:6px 0;font-variant-numeric:tabular-nums">{decay:.0%}</div>'
                        f'<div style="font-size:11px;color:{dc}">{"⚠️ 过高" if decay>0.5 else "✓ 合格"}</div></div>',
                        unsafe_allow_html=True,
                    )
                with da2:
                    dc2 = "#ef4444" if dd_r > 2 else "#22c55e"
                    st.markdown(
                        f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);border-radius:6px;padding:12px;text-align:center;margin-top:10px">'
                        f'<div style="font-size:10px;color:rgba(255,255,255,0.3);text-transform:uppercase;letter-spacing:.05em">回撤恶化</div>'
                        f'<div style="font-size:22px;font-weight:700;color:{dc2};margin:6px 0;font-variant-numeric:tabular-nums">{dd_r:.1f}x</div>'
                        f'<div style="font-size:11px;color:{dc2}">{"⚠️ 超2倍" if dd_r>2 else "✓ 合格"}</div></div>',
                        unsafe_allow_html=True,
                    )

                if diag.get("issues"):
                    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
                    for issue in diag["issues"]:
                        st.markdown(
                            f'<div style="background:rgba(239,68,68,0.06);border-left:2px solid #ef4444;'
                            f'border-radius:4px;padding:8px 12px;margin-bottom:6px;font-size:12px;color:#ef4444">{issue}</div>',
                            unsafe_allow_html=True,
                        )


# ══════════════════════════════════════════
#  ETF50 结果展示
# ══════════════════════════════════════════
def _show_etf50_result(st, data: dict):
    ts = data.get("datetime","")[:16]
    total = data.get("total", 0)
    success = data.get("success", 0)
    bull = data.get("bullish", 0)
    bear = data.get("bearish", 0)
    neut = data.get("neutral", 0)

    st.markdown(f'<div style="font-size:11px;color:rgba(255,255,255,0.3);margin:14px 0 10px">上次扫描：{ts} · {success}/{total} 只有效</div>', unsafe_allow_html=True)

    # 统计行
    s1,s2,s3,s4 = st.columns(4)
    for col,(lb,v,c) in zip([s1,s2,s3,s4],[
        ("📈 看多",bull,"#22c55e"),("→ 中性",neut,"#f59e0b"),
        ("📉 看空",bear,"#ef4444"),("✅ 有效",success,"#3b82f6"),
    ]):
        with col:
            st.markdown(
                f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);border-radius:6px;padding:10px;text-align:center">'
                f'<div style="font-size:20px;font-weight:700;color:{c};font-variant-numeric:tabular-nums">{v}</div>'
                f'<div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:2px">{lb}</div></div>',
                unsafe_allow_html=True,
            )

    # 组合回测结果
    bt = data.get("backtest", {})
    if bt and bt.get("sharpe_ratio") is not None:
        st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
        top_n = data.get("top_n", 5)
        st.markdown(
            f'<div style="background:#111;border:1px solid rgba(59,130,246,0.2);border-left:2px solid #3b82f6;'
            f'border-radius:6px;padding:12px 16px;margin-bottom:4px">'
            f'<div style="font-size:11px;color:#3b82f6;font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Top-{top_n} 等权组合回测结果</div>'
            f'<div style="display:flex;gap:24px;flex-wrap:wrap">'
            + "".join([
                f'<div><div style="font-size:18px;font-weight:700;color:{c};font-variant-numeric:tabular-nums">{v}</div>'
                f'<div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:2px">{lb}</div></div>'
                for lb,v,c in [
                    ("总收益",f"{bt.get('total_return',0):.1%}","#22c55e" if bt.get('total_return',0)>0 else "#ef4444"),
                    ("年化收益",f"{bt.get('annual_return',0):.1%}","#22c55e" if bt.get('annual_return',0)>0 else "#ef4444"),
                    ("夏普比率",f"{bt.get('sharpe_ratio',0):.2f}","#22c55e" if bt.get('sharpe_ratio',0)>1 else "#f59e0b"),
                    ("最大回撤",f"{bt.get('max_drawdown',0):.1%}","#ef4444"),
                    ("交易笔数",str(bt.get('total_trades',0)),"#9ca3af"),
                ]
            ]) + '</div></div>',
            unsafe_allow_html=True,
        )

        # 净值曲线
        eq_list = bt.get("equity_curve", [])
        if eq_list and len(eq_list) > 5:
            eq = pd.Series(eq_list)
            st.line_chart(pd.DataFrame({"净值": eq / eq.iloc[0]}), color=["#3b82f6"], height=160)

    # 前三名
    top3 = data.get("top3", [])
    if top3:
        st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">🏆 量化前三名</div>', unsafe_allow_html=True)
        t1,t2,t3 = st.columns(3)
        medals = ["🥇","🥈","🥉"]
        accs   = ["#22c55e","#3b82f6","#f59e0b"]
        for col, r, medal, acc in zip([t1,t2,t3], top3, medals, accs):
            sc = r.get("quant_score", 0)
            reasons = r.get("reasons",[])[:2]
            rhtml = " · ".join(reasons) if reasons else ""
            with col:
                st.markdown(
                    f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);border-top:1px solid {acc};border-radius:6px;padding:14px">'
                    f'<div style="font-size:16px;margin-bottom:6px">{medal}</div>'
                    f'<div style="font-size:11px;color:rgba(255,255,255,0.35)">{r.get("code","")}</div>'
                    f'<div style="font-size:14px;font-weight:600;color:#fff;margin:3px 0">{r.get("name","")}</div>'
                    f'<div style="font-size:22px;font-weight:700;color:{acc};font-variant-numeric:tabular-nums;margin:6px 0">{sc:.0f}<span style="font-size:12px;color:rgba(255,255,255,0.3)"> 分</span></div>'
                    f'{_bar(sc)}'
                    f'<div style="font-size:11px;color:rgba(255,255,255,0.35);margin-top:6px">{rhtml}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # 完整排行榜
    results = [r for r in data.get("results",[]) if r.get("has_data")]
    if not results:
        return

    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

    # 筛选
    fc1, fc2 = st.columns([1,2])
    with fc1:
        sig_f = st.selectbox("", ["全部","看多","中性","看空"], key="etfq_sig", label_visibility="collapsed")
    with fc2:
        cats = ["全部类别"] + sorted(set(r.get("category","") for r in results if r.get("category")))
        cat_f = st.selectbox("", cats, key="etfq_cat", label_visibility="collapsed")

    sm = {"全部":None,"看多":"bullish","中性":"neutral","看空":"bearish"}
    filtered = [r for r in results if
                (not sm[sig_f] or r.get("signal")==sm[sig_f]) and
                (cat_f=="全部类别" or r.get("category","")==cat_f)]

    # 表头
    st.markdown(
        '<div style="display:grid;grid-template-columns:40px 70px 1fr 100px 60px 80px 100px 80px;'
        'gap:8px;padding:6px 12px;font-size:10px;color:rgba(255,255,255,0.3);'
        'text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid rgba(255,255,255,0.06);margin-top:6px">'
        '<div>#</div><div>代码</div><div>名称</div><div>类别</div>'
        '<div style="text-align:right">量化分</div><div style="text-align:center">信号</div>'
        '<div>关键因子</div><div style="text-align:right">进度</div></div>',
        unsafe_allow_html=True,
    )

    for i, r in enumerate(filtered, 1):
        sc   = r.get("quant_score", 0)
        sig  = r.get("signal","neutral")
        sc_c = _sc(sc)
        reasons = r.get("reasons",[])[:2]
        rstr = " · ".join(reasons)
        medal = {1:"🥇",2:"🥈",3:"🥉"}.get(i,"")
        row_bg = "rgba(255,255,255,0.02)" if i % 2 == 0 else "transparent"
        sig_html = _sig_html(sig)

        st.markdown(
            f'<div style="display:grid;grid-template-columns:40px 70px 1fr 100px 60px 80px 100px 80px;'
            f'gap:8px;padding:8px 12px;align-items:center;background:{row_bg};'
            f'border-radius:4px;border-bottom:1px solid rgba(255,255,255,0.03)">'
            f'<div style="font-size:12px;color:rgba(255,255,255,0.25)">{medal}{i}</div>'
            f'<div style="font-size:12px;color:rgba(255,255,255,0.5);font-family:monospace">{r.get("code","")}</div>'
            f'<div style="font-size:13px;font-weight:500;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{r.get("name","")}</div>'
            f'<div style="font-size:10px;color:rgba(255,255,255,0.3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{r.get("category","")}</div>'
            f'<div style="text-align:right;font-size:14px;font-weight:700;color:{sc_c};font-variant-numeric:tabular-nums">{sc:.0f}</div>'
            f'<div style="text-align:center">{sig_html}</div>'
            f'<div style="font-size:11px;color:rgba(255,255,255,0.35);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{rstr}</div>'
            f'<div>{_bar(sc)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

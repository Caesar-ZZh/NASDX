"""
NASDX V2 — 置信度训练页面
"""
from __future__ import annotations
# ── scripts/ 运行引导：把仓库根加入 sys.path（移动自根目录）──
import sys
from pathlib import Path
_ROOT_DIR = str(Path(__file__).resolve().parents[1])
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)
import sys, threading, time, json
from pathlib import Path
# pandas/numpy 延迟导入，避免 import confidence_page 时阻塞 500ms

from nasdx.paths import get_reports_dir

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _bar(v, h=5, color=None):
    c = color or ("#22c55e" if v >= 0.55 else "#ef4444" if v <= 0.45 else "#f59e0b")
    return (f'<div style="background:rgba(255,255,255,0.06);border-radius:2px;height:{h}px;overflow:hidden">'
            f'<div style="width:{min(v*100,100):.0f}%;height:100%;background:{c};border-radius:2px"></div></div>')


def _card(body, accent=None):
    top = f"background:linear-gradient(90deg,transparent,{accent},transparent)" if accent else "none"
    return (f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
            f'border-radius:8px;padding:14px 16px;position:relative;overflow:hidden">'
            f'<div style="position:absolute;top:0;left:0;right:0;height:1px;{top}"></div>'
            f'{body}</div>')


def _metric(label, value, color="#fff", sub=""):
    sub_html = f'<div style="font-size:11px;color:rgba(255,255,255,0.3);margin-top:2px">{sub}</div>' if sub else ""
    return (f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
            f'border-radius:6px;padding:12px;text-align:center">'
            f'<div style="font-size:18px;font-weight:700;color:{color};font-variant-numeric:tabular-nums">{value}</div>'
            f'<div style="font-size:10px;color:rgba(255,255,255,0.3);text-transform:uppercase;'
            f'letter-spacing:.05em;margin-top:3px">{label}</div>'
            f'{sub_html}</div>')


_train_log = []
_train_done = False
_train_result = None


def _run_training(codes, days, fwd_days, log_path):
    global _train_log, _train_done, _train_result
    _train_log = []
    _train_done = False

    import builtins, io
    sys.path.insert(0, str(ROOT))

    with open(log_path, "w", encoding="utf-8", buffering=1) as log:
        try:
            import quant.patch_requests  # noqa
            from quant.confidence_trainer import ConfidenceTrainer

            orig_print = builtins.print
            def tee(*a, **k):
                buf = io.StringIO()
                orig_print(*a, file=buf, **k)
                msg = buf.getvalue()
                log.write(msg); log.flush()
            builtins.print = tee

            trainer = ConfidenceTrainer(forward_days=fwd_days)
            result  = trainer.train(codes=codes or None, days=days, verbose=True)
            builtins.print = orig_print

            log.write(f"\n__DONE__\n")
            _train_result = result
        except Exception as e:
            import traceback
            log.write(f"\n__ERROR__:{e}\n{traceback.format_exc()}\n")
        finally:
            _train_done = True


def render_confidence_page(st):
    """置信度训练主页面"""

    st.markdown(
        '<div style="padding:20px 0 14px">'
        '<div style="font-size:22px;font-weight:700;color:#fff">数据置信度训练</div>'
        '<div style="font-size:12px;color:rgba(255,255,255,0.35);margin-top:3px">'
        '用历史真实数据校准各信号源可靠性 → 动态调整 SignalVoter 权重 → 提升预测准确度'
        '</div></div>',
        unsafe_allow_html=True,
    )

    # ── 原理说明 ──────────────────────────────────────────
    st.markdown(_card(
        '<div style="font-size:12px;color:rgba(255,255,255,0.45);line-height:2">'
        '<b style="color:#a855f7">训练逻辑</b>（整合三大框架）：<br>'
        '① <b style="color:#3b82f6">QLib</b> Walk-Forward：滚动验证各因子 IC/ICIR，筛选稳定因子<br>'
        '② <b style="color:#22c55e">VnPy</b> 命中率统计：对比历史信号方向与实际价格变化<br>'
        '③ <b style="color:#f59e0b">FinRL</b> 权重更新：根据 Hit Rate + IC 动态调整 SignalVoter 5个权重<br>'
        '④ 结果固化到 <code>models/signal_confidence.json</code>，后续所有分析自动使用校准权重'
        '</div>', accent="#a855f7"
    ), unsafe_allow_html=True)

    st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)

    # ── 当前权重展示 ──────────────────────────────────────
    from quant.confidence_trainer import load_calibrated_weights, load_confidence_report, DEFAULT_WEIGHTS
    current_weights  = load_calibrated_weights()
    conf_report      = load_confidence_report()
    is_calibrated    = conf_report is not None

    st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.25);text-transform:uppercase;'
                'letter-spacing:.06em;margin-bottom:8px">'
                f'{"✅ 已校准权重" if is_calibrated else "⚪ 默认权重（未训练）"}'
                f'{" · " + conf_report.get("trained_at","")[:16] if is_calibrated else ""}'
                '</div>', unsafe_allow_html=True)

    SRC_LABELS = {
        "technical": ("📐 技术面", "ETF50 评分"),
        "factor":    ("🧮 因子",   "Alpha158"),
        "trend":     ("📈 趋势",   "MA/MACD"),
        "volume":    ("💰 量价",   "量比/资金"),
        "ai":        ("🤖 AI",     "DeepSeek"),
    }

    wc = st.columns(5)
    for col, (src, (icon, label)) in zip(wc, SRC_LABELS.items()):
        w    = current_weights.get(src, DEFAULT_WEIGHTS.get(src, 0.2))
        dw   = DEFAULT_WEIGHTS.get(src, 0.2)
        diff = w - dw
        diff_c = "#22c55e" if diff > 0.01 else "#ef4444" if diff < -0.01 else "rgba(255,255,255,0.3)"
        diff_s = f"{diff:+.3f}" if abs(diff) > 0.005 else "持平"
        with col:
            st.markdown(
                f'<div style="background:#111;border:1px solid rgba(255,255,255,0.06);'
                f'border-radius:6px;padding:12px;text-align:center">'
                f'<div style="font-size:16px;margin-bottom:6px">{icon}</div>'
                f'<div style="font-size:11px;color:rgba(255,255,255,0.3);margin-bottom:4px">{label}</div>'
                f'<div style="font-size:22px;font-weight:700;color:#fff;font-variant-numeric:tabular-nums">'
                f'{w:.1%}</div>'
                f'{_bar(w)}'
                f'<div style="font-size:11px;color:{diff_c};margin-top:4px">{diff_s}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── 置信度详情（已训练） ──────────────────────────────
    if conf_report:
        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
        conf_lv = conf_report.get("confidence_level", "low")
        conf_color = {"high": "#22c55e", "medium": "#f59e0b", "low": "#ef4444"}.get(conf_lv, "#9ca3af")
        conf_icon  = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf_lv, "⚪")
        wf = conf_report.get("walk_forward", {})

        dm = st.columns(4)
        for col, (lb, v, c) in zip(dm, [
            ("整体置信度",  f'{conf_icon} {conf_lv.upper()}',       conf_color),
            ("技术信号命中率", f'{conf_report.get("tech_hit_rate",0.5):.1%}',
             "#22c55e" if conf_report.get("tech_hit_rate",0.5)>0.55 else "#f59e0b"),
            ("稳定因子数量", str(wf.get("n_stable_factors", 0)),    "#3b82f6"),
            ("历史报告数量", str(conf_report.get("n_reports", 0)),  "#9ca3af"),
        ]):
            with col:
                st.markdown(_metric(lb, v, c), unsafe_allow_html=True)

        # 因子 IC 排行
        factor_ic = conf_report.get("factor_ic", {})
        if factor_ic:
            st.markdown('<div style="height:12px"></div>', unsafe_allow_html=True)
            st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.25);text-transform:uppercase;'
                        'letter-spacing:.06em;margin-bottom:8px">Alpha158 因子 IC 排行（Top 15）</div>',
                        unsafe_allow_html=True)

            sorted_f = sorted(factor_ic.items(),
                               key=lambda x: -abs(x[1].get("ic_mean", 0)))[:15]

            hdr = st.columns([2, 1, 1, 1, 1])
            for col, h in zip(hdr, ["因子名", "IC均值", "ICIR", "稳定性", "强度"]):
                col.markdown(f'<div style="font-size:10px;color:rgba(255,255,255,0.2);'
                             f'text-transform:uppercase;letter-spacing:.05em;padding:4px 0">{h}</div>',
                             unsafe_allow_html=True)

            for fname, info in sorted_f:
                ic   = info.get("ic_mean", 0)
                icir = info.get("icir", 0)
                stable = info.get("stable", False)
                ic_c = "#22c55e" if ic > 0.03 else "#ef4444" if ic < -0.03 else "#f59e0b"
                bar_w = min(abs(ic) * 1000, 100)

                row = st.columns([2, 1, 1, 1, 1])
                row[0].markdown(f'<div style="font-size:12px;color:#fff;padding:6px 0">{fname}</div>',
                                unsafe_allow_html=True)
                row[1].markdown(f'<div style="font-size:12px;color:{ic_c};font-weight:600;'
                                f'font-variant-numeric:tabular-nums;padding:6px 0">{ic:+.4f}</div>',
                                unsafe_allow_html=True)
                row[2].markdown(f'<div style="font-size:12px;color:rgba(255,255,255,0.5);'
                                f'font-variant-numeric:tabular-nums;padding:6px 0">{icir:+.3f}</div>',
                                unsafe_allow_html=True)
                row[3].markdown(f'<div style="padding:6px 0;font-size:12px;'
                                f'color:{"#22c55e" if stable else "rgba(255,255,255,0.25)"}">'
                                f'{"✓ 稳定" if stable else "· 弱"}</div>',
                                unsafe_allow_html=True)
                row[4].markdown(
                    f'<div style="padding:6px 0">'
                    f'<div style="background:rgba(255,255,255,0.06);border-radius:2px;height:4px;overflow:hidden">'
                    f'<div style="width:{bar_w:.0f}%;height:100%;background:{ic_c};border-radius:2px"></div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

    st.markdown('<hr style="border:none;border-top:0.5px solid rgba(255,255,255,0.06);margin:20px 0">',
                unsafe_allow_html=True)

    # ── 训练参数 + 运行 ──────────────────────────────────
    st.markdown('<div style="font-size:11px;color:rgba(255,255,255,0.25);text-transform:uppercase;'
                'letter-spacing:.06em;margin-bottom:10px">训练参数</div>', unsafe_allow_html=True)

    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        train_days = st.select_slider("历史天数", [180, 252, 365, 500], value=365,
                                       help="越长越准确，但耗时更久")
    with tc2:
        fwd_days = st.select_slider("预测窗口（日）", [3, 5, 10, 20], value=5,
                                     help="验证信号后N日的实际表现")
    with tc3:
        train_codes_input = st.text_input(
            "指定标的（选填，留空用全部）",
            placeholder="159611,513160,515880",
            key="conf_codes",
        )

    reports_dir = get_reports_dir()
    n_reports = len(list(reports_dir.glob("etf50_*.json")))
    n_quant   = len(list(reports_dir.glob("etf50_quant_*.json")))
    st.caption(f"可用数据：{n_reports} 份 ETF50 扫描报告 + {n_quant} 份量化报告 · "
               f"预计耗时 3-8 分钟")

    is_running = st.session_state.get("conf_running", False)

    if not is_running:
        if st.button("🧬  开始置信度训练", key="run_confidence",
                     type="primary", use_container_width=False):
            codes = ([c.strip().zfill(6) for c in train_codes_input.split(",")
                      if c.strip()] if train_codes_input.strip() else None)
            lp = ROOT / "confidence_train_log.txt"
            lp.write_text("", encoding="utf-8")
            t = threading.Thread(
                target=_run_training,
                args=(codes, train_days, fwd_days, str(lp)),
                daemon=True,
            )
            t.start()
            st.session_state.update({
                "conf_running": True,
                "conf_thread":  t,
                "conf_log":     str(lp),
            })
            st.rerun()
    else:
        # 训练中
        lp = Path(st.session_state.get("conf_log", ""))
        log_text = lp.read_text(encoding="utf-8") if lp.exists() else ""
        lines = [l for l in log_text.splitlines() if l.strip()]

        # 简单进度：检测 Step 关键词
        done_steps = sum(1 for l in lines if "Step" in l)
        st.progress(min(done_steps / 5, 0.95), text=f"训练中... Step {done_steps}/5")

        with st.expander("📟 训练日志", expanded=True):
            st.code("\n".join(lines[-20:]) if lines else "启动中...", language=None)

        thread = st.session_state.get("conf_thread")
        finished = thread is None or not thread.is_alive()

        if "__DONE__" in log_text or (finished and done_steps >= 4):
            st.session_state["conf_running"] = False
            st.session_state.pop("conf_thread", None)
            st.rerun()
        elif "__ERROR__" in log_text and finished:
            st.session_state["conf_running"] = False
            err = [l for l in lines if "ERROR" in l or "Traceback" in l]
            st.error("训练失败：" + "\n".join(err[:4]))
        else:
            time.sleep(3)
            st.rerun()

    # ── 权重对比说明 ──────────────────────────────────────
    if not is_running:
        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
        st.markdown(_card(
            '<div style="font-size:12px;color:rgba(255,255,255,0.4);line-height:2">'
            '<b style="color:#fff">权重调整逻辑说明：</b><br>'
            '· Hit Rate &gt; 55% → 该信号源权重上调，最高 50%<br>'
            '· Hit Rate &lt; 50% → 该信号源权重下调，最低 5%<br>'
            '· 因子 IC 稳定（ICIR &gt; 0.3）→ 因子权重加成<br>'
            '· 所有权重归一化保证总和 = 100%<br>'
            '· AI 信号（DeepSeek）当前固定 15%，需积累更多历史后自动校准'
            '</div>'
        ), unsafe_allow_html=True)

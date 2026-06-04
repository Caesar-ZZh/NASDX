"""同花顺页面代码片段 — 由 app.py 动态加载"""


def render_ths_page(st, ROOT):
    import glob as _g
    from pathlib import Path

    st.markdown(
        '<div style="padding:24px 0 20px">'
        '<div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">同花顺接入</div>'
        '<div style="font-size:13px;color:#6b6b6b;margin-top:4px">持仓同步 · 实时行情 · 自动交易</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    for k, v in {
        "ths_connected": False, "ths_position": [], "ths_balance": {},
        "ths_prices": {}, "ths_trader": None, "ths_capital": 26000.0,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # ── 连接 ────────────────────────────────────────────
    st.markdown('<div class="n-section-title">连接状态</div>', unsafe_allow_html=True)
    col_conn, col_info = st.columns([1, 3])
    with col_conn:
        if st.button("🔌 连接同花顺", use_container_width=True, key="ths_connect"):
            try:
                from ths_bridge import THSTrader
                trader = THSTrader()
                ok = trader.connect()
                if ok:
                    st.session_state.ths_connected = True
                    st.session_state.ths_trader = trader
                    st.session_state.ths_balance = trader.get_balance()
                    st.session_state.ths_position = trader.get_position()
                    st.toast("同花顺连接成功！", icon="✅")
                else:
                    st.session_state.ths_connected = False
            except ImportError:
                st.error("请先安装：pip install easytrader")
            except Exception as e:
                st.error(f"连接失败：{e}")
            st.rerun()

    with col_info:
        if st.session_state.ths_connected:
            st.markdown(
                '<div style="display:flex;align-items:center;gap:8px;padding-top:8px">'
                '<span style="color:#4bae8a;font-size:20px">●</span>'
                '<span style="color:#4bae8a;font-weight:600;font-size:14px">已连接同花顺</span></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="n-card" style="padding:12px 16px;border-left:3px solid #d4a843">'
                '<div style="font-size:12px;color:#d4a843;font-weight:600;margin-bottom:4px">⚠️ 使用前请确认</div>'
                '<div style="font-size:12px;color:#6b6b6b;line-height:1.7">'
                "1. 同花顺客户端已在电脑上登录运行<br>"
                "2. 已安装 easytrader 和 pytdx<br>"
                "3. 点击「连接同花顺」建立连接"
                "</div></div>",
                unsafe_allow_html=True,
            )

    st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

    # ── 实时行情 ──────────────────────────────────────────
    st.markdown('<div class="n-section-title">实时行情（通达信协议 · 免费）</div>', unsafe_allow_html=True)
    watch_input = st.text_input(
        "股票代码（逗号分隔）",
        value="512480,159611,513160,513130,515880",
        label_visibility="visible",
    )
    if st.button("↻ 刷新行情", key="refresh_price"):
        try:
            from ths_bridge import get_realtime_batch
            codes = [c.strip() for c in watch_input.split(",") if c.strip()]
            st.session_state.ths_prices = get_realtime_batch(codes)
            st.toast(f"已更新 {len(st.session_state.ths_prices)} 只", icon="📡")
        except Exception as e:
            st.error(f"行情获取失败（需安装 pytdx）：{e}")
        st.rerun()

    prices = st.session_state.ths_prices
    if prices:
        hdr = st.columns([2, 1, 1, 1, 1, 1])
        for col, h in zip(hdr, ["代码", "现价", "涨跌%", "最高", "最低", "成交额(亿)"]):
            col.markdown(
                f'<div style="font-size:11px;color:#5b5b5b;font-weight:600;padding:4px 0">{h}</div>',
                unsafe_allow_html=True,
            )
        for code, info in prices.items():
            chg = info.get("change_pct", 0)
            cc = "#4bae8a" if chg > 0 else "#e16b6b" if chg < 0 else "#6b6b6b"
            row = st.columns([2, 1, 1, 1, 1, 1])
            row[0].markdown(f'<div style="font-size:13px;font-weight:600;color:#fff;padding:6px 0">{code}</div>', unsafe_allow_html=True)
            row[1].markdown(f'<div style="font-size:13px;color:#fff;padding:6px 0">{info["price"]:.3f}</div>', unsafe_allow_html=True)
            row[2].markdown(f'<div style="font-size:13px;font-weight:600;color:{cc};padding:6px 0">{chg:+.2f}%</div>', unsafe_allow_html=True)
            row[3].markdown(f'<div style="font-size:12px;color:#6b6b6b;padding:6px 0">{info["high"]:.3f}</div>', unsafe_allow_html=True)
            row[4].markdown(f'<div style="font-size:12px;color:#6b6b6b;padding:6px 0">{info["low"]:.3f}</div>', unsafe_allow_html=True)
            row[5].markdown(f'<div style="font-size:12px;color:#6b6b6b;padding:6px 0">{info["amount"]/1e8:.2f}</div>', unsafe_allow_html=True)

    st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

    # ── 持仓 ──────────────────────────────────────────────
    st.markdown('<div class="n-section-title">当前持仓</div>', unsafe_allow_html=True)
    if st.session_state.ths_connected:
        if st.button("↻ 刷新持仓", key="refresh_pos"):
            t = st.session_state.ths_trader
            st.session_state.ths_position = t.get_position()
            st.session_state.ths_balance = t.get_balance()
            st.rerun()

        bal = st.session_state.ths_balance
        if bal:
            bc = st.columns(3, gap="small")
            for col, (k, lb) in zip(bc, [("总资产", "总资产"), ("可用金额", "可用资金"), ("市值", "持仓市值")]):
                val = bal.get(k, bal.get(lb, 0))
                with col:
                    st.markdown(
                        f'<div class="n-card" style="text-align:center;padding:12px">'
                        f'<div style="font-size:18px;font-weight:700;color:#fff">¥{float(val):,.0f}</div>'
                        f'<div style="font-size:11px;color:#5b5b5b;margin-top:2px">{lb}</div></div>',
                        unsafe_allow_html=True,
                    )

        pos = st.session_state.ths_position
        if pos:
            for p in pos:
                code = p.get("证券代码", ""); name = p.get("证券名称", "")
                qty = p.get("持仓量", 0); avail = p.get("可用量", 0)
                cost = p.get("成本价", 0); mkt = p.get("市价", 0)
                pnl = (float(mkt) - float(cost)) * int(qty) if mkt and cost and qty else 0
                pc = "#4bae8a" if pnl > 0 else "#e16b6b" if pnl < 0 else "#6b6b6b"
                st.markdown(
                    f'<div class="n-card" style="margin-bottom:8px;padding:14px 18px">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center">'
                    f'<div><span style="font-size:13px;font-weight:600;color:#fff">{code} {name}</span>'
                    f'<span style="font-size:11px;color:#5b5b5b;margin-left:12px">持仓{qty}股 · 可用{avail}股</span></div>'
                    f'<div style="text-align:right">'
                    f'<div style="font-size:14px;font-weight:700;color:{pc}">¥{pnl:+,.0f}</div>'
                    f'<div style="font-size:11px;color:#5b5b5b">成本{cost} · 现价{mkt}</div>'
                    f"</div></div></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div style="color:#4a4a4a;padding:16px 0;font-size:13px">暂无持仓</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#4a4a4a;padding:12px 0;font-size:13px">请先连接同花顺客户端</div>', unsafe_allow_html=True)

    st.markdown('<hr class="n-divider">', unsafe_allow_html=True)

    # ── 自动交易 ──────────────────────────────────────────
    st.markdown('<div class="n-section-title">智能交易（基于 ETF50 扫描）</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="n-card" style="border-left:3px solid #e16b6b;margin-bottom:16px;padding:12px 16px">'
        '<div style="font-size:12px;color:#e16b6b;font-weight:600;margin-bottom:2px">⚠️ 风险提示</div>'
        '<div style="font-size:12px;color:#6b6b6b">仅供学习研究，不构成投资建议。建议先用演练模式确认逻辑。</div></div>',
        unsafe_allow_html=True,
    )

    tc1, tc2 = st.columns(2, gap="medium")
    with tc1:
        capital = st.number_input("可用资金（元）", value=float(st.session_state.ths_capital), min_value=1000.0, step=1000.0)
        st.session_state.ths_capital = capital
        min_score = st.slider("最低买入评分", 60, 95, 80)
        max_prem = st.slider("最高溢价率（%）", 0.0, 5.0, 1.0, step=0.5)
    with tc2:
        st.markdown(
            f'<div class="n-card" style="padding:14px">'
            f'<div class="n-label" style="margin-bottom:8px">交易规则</div>'
            f'<div style="font-size:12px;color:#9b9b9b;line-height:2">'
            f"评分 ≥ {min_score} 且溢价 &lt; {max_prem:.1f}% → 买入<br>"
            f"持仓股评分 ≤ 40 → 卖出<br>"
            f"单只仓位 ≤ 总资金 40%，每日最多 3 只"
            f"</div></div>",
            unsafe_allow_html=True,
        )

    etf_files = sorted(_g.glob(str(ROOT / "reports/etf50_*.json")))
    if etf_files:
        latest_etf = etf_files[-1]
        st.caption(f"基于：{Path(latest_etf).name}")
        dr_col, live_col = st.columns(2, gap="small")
        with dr_col:
            if st.button("🧪 演练模式（不下单）", use_container_width=True, key="dry_run"):
                try:
                    from ths_bridge import NasdxAutoTrader
                    import io, contextlib
                    auto = NasdxAutoTrader(total_capital=capital)
                    auto.connected = False
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        auto.run_once(latest_etf, dry_run=True)
                    st.code(buf.getvalue(), language=None)
                except Exception as e:
                    st.error(f"演练失败：{e}")
        with live_col:
            if st.session_state.ths_connected:
                if st.button("⚡ 实盘执行", use_container_width=True, key="live_trade"):
                    confirm = st.checkbox("我已阅读风险提示，确认实盘操作", key="confirm_live")
                    if confirm:
                        try:
                            from ths_bridge import NasdxAutoTrader
                            import io, contextlib
                            auto = NasdxAutoTrader(total_capital=capital)
                            auto.trader = st.session_state.ths_trader
                            auto.connected = True
                            buf = io.StringIO()
                            with contextlib.redirect_stdout(buf):
                                auto.run_once(latest_etf, dry_run=False)
                            st.code(buf.getvalue(), language=None)
                            st.success("执行完成，请在同花顺查看委托")
                        except Exception as e:
                            st.error(f"执行失败：{e}")
            else:
                st.markdown('<div style="font-size:12px;color:#4a4a4a;padding-top:8px">实盘需先连接同花顺</div>', unsafe_allow_html=True)
    else:
        st.info("请先运行 ETF50 扫描")

    st.markdown('<hr class="n-divider" style="margin:24px 0 8px">', unsafe_allow_html=True)
    st.markdown('<div style="font-size:11px;color:#3a3a3a;text-align:center">easytrader · pytdx · 同花顺客户端</div>', unsafe_allow_html=True)

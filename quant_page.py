"""
NASDX V2 量化引擎页面 — 由 app.py 路由调用
整合 QLib 因子 + FinRL 强化学习 + VnPy 回测
"""
import sys, json, time
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def render_quant_page(st, ROOT):
    """渲染量化引擎完整页面"""

    st.markdown("""
    <div style="padding:24px 0 20px">
      <div style="font-size:26px;font-weight:700;color:#fff;letter-spacing:-0.02em">量化引擎</div>
      <div style="font-size:13px;color:#6b6b6b;margin-top:4px">
        QLib 因子挖掘 · FinRL 强化学习 · 回测验证 · 投资组合优化
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Tabs ─────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "📐 因子分析", "🔁 策略回测", "🤖 强化学习", "📦 投资组合"
    ])

    # ══════════════════════════════════════════════════
    #  Tab1：因子分析（QLib Alpha158）
    # ══════════════════════════════════════════════════
    with tab1:
        st.markdown('<div class="n-section-title">Alpha 因子分析（参考 QLib Alpha158）</div>',
                    unsafe_allow_html=True)
        st.markdown("""
        <div class="n-card" style="padding:12px 16px;margin-bottom:16px">
          <div style="font-size:12px;color:#9b9b9b;line-height:1.8">
            计算 80+ 个量价因子（动量/反转/波动/量比/MACD/RSI/布林带），
            并进行横截面排名，找出因子最强的 ETF/股票。
          </div>
        </div>""", unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        with c1:
            codes_input = st.text_area(
                "分析标的（每行一个代码）",
                value="512480\n159611\n513160\n513130\n515880\n159995\n588200\n512660",
                height=160,
            )
        with c2:
            factor_days = st.slider("历史数据天数", 60, 365, 120)
            top_factor = st.selectbox("重点因子", [
                "ROC20","ROC5","RSI14","MACD","VOLU5",
                "BIAS20","BOLL_POS20","ATR14","MOM5_REVERSAL"
            ])
        with c3:
            st.markdown("""
            <div class="n-card" style="padding:12px;font-size:12px;color:#6b6b6b;line-height:1.8">
              <b style="color:#fff">因子说明</b><br>
              ROC20: 20日动量<br>
              RSI14: 超买超卖<br>
              MACD: 趋势动能<br>
              VOLU5: 量比放量<br>
              BIAS20: 均线偏离<br>
              ATR14: 波动率
            </div>""", unsafe_allow_html=True)

        if st.button("🔍 计算因子", use_container_width=False, key="calc_factor"):
            codes = [c.strip() for c in codes_input.split("\n") if c.strip()]
            if not codes:
                st.warning("请输入股票代码")
            else:
                with st.spinner(f"抓取 {len(codes)} 只数据并计算因子..."):
                    try:
                        from quant.data import get_batch_ohlcv
                        from quant.factors import compute_alpha158, multi_factor_score, rank_stocks

                        price_data = get_batch_ohlcv(codes, days=factor_days)
                        if not price_data:
                            st.error("未能获取数据，请检查代码或网络")
                        else:
                            factor_data = {}
                            for code, df in price_data.items():
                                if len(df) >= 60:
                                    factor_data[code] = compute_alpha158(df)

                            # 单因子排名
                            ranking = rank_stocks(factor_data, factor_name=top_factor)
                            # 多因子综合
                            composite = multi_factor_score(factor_data)

                            if not ranking.empty:
                                st.markdown(f'<div class="n-section-title">{top_factor} 因子排名</div>',
                                            unsafe_allow_html=True)
                                for _, row in ranking.iterrows():
                                    code = row["code"]
                                    val  = row[top_factor]
                                    pct  = row["pct_rank"]
                                    rank = int(row["rank"])
                                    bar_w = int((1 - pct) * 100)
                                    color = "#4bae8a" if val > 0 else "#e16b6b"
                                    medal = {1:"🥇",2:"🥈",3:"🥉"}.get(rank,"")
                                    st.markdown(f"""
                                    <div class="n-card" style="margin-bottom:6px;padding:10px 16px">
                                      <div style="display:flex;justify-content:space-between;align-items:center">
                                        <span style="font-weight:600;color:#fff">{medal} {code}</span>
                                        <span style="color:{color};font-weight:700">{val:+.3f}</span>
                                        <span style="font-size:11px;color:#5b5b5b">排名 {rank}/{len(ranking)}</span>
                                      </div>
                                      <div class="bar-wrap" style="margin-top:6px">
                                        <div class="bar-fill-green" style="width:{bar_w}%"></div>
                                      </div>
                                    </div>""", unsafe_allow_html=True)

                            if not composite.empty:
                                st.markdown('<div class="n-section-title">多因子综合评分</div>',
                                            unsafe_allow_html=True)
                                top3 = composite.head(3)
                                tcols = st.columns(3)
                                for col, (_, row) in zip(tcols, top3.iterrows()):
                                    sc = row["factor_score"]
                                    color = "#4bae8a" if sc > 0 else "#e16b6b"
                                    medal = {1:"🥇",2:"🥈",3:"🥉"}.get(int(row["rank"]),"")
                                    with col:
                                        st.markdown(f"""
                                        <div class="n-card" style="text-align:center;padding:16px">
                                          <div style="font-size:18px">{medal}</div>
                                          <div style="font-size:15px;font-weight:700;color:#fff;margin:6px 0">{row['code']}</div>
                                          <div style="font-size:24px;font-weight:700;color:{color}">{sc:+.3f}</div>
                                          <div style="font-size:11px;color:#5b5b5b">综合因子分</div>
                                        </div>""", unsafe_allow_html=True)

                    except Exception as e:
                        st.error(f"因子计算失败：{e}")

    # ══════════════════════════════════════════════════
    #  Tab2：策略回测（参考 VnPy BacktestingEngine）
    # ══════════════════════════════════════════════════
    with tab2:
        st.markdown('<div class="n-section-title">策略回测（参考 VnPy BacktestingEngine）</div>',
                    unsafe_allow_html=True)

        b1, b2 = st.columns([2,1])
        with b1:
            bt_codes = st.text_input(
                "回测标的（逗号分隔）",
                value="512480,159611,513160,513130,515880",
            )
            bt_strategy = st.selectbox("策略", [
                "动量策略 (Momentum)",
                "均值回归策略 (Mean Reversion)",
                "多因子排名策略 (Factor Rank)",
            ])
            bt_capital = st.number_input("初始资金（元）", value=100000, step=10000)
            bt_days    = st.slider("回测天数", 90, 500, 252)
            bt_freq    = st.selectbox("再平衡频率", ["W（每周）","M（每月）","D（每日）"])

        with b2:
            st.markdown("""
            <div class="n-card" style="padding:14px;font-size:12px;color:#6b6b6b;line-height:2">
              <b style="color:#fff">三大策略说明</b><br>
              <b style="color:#d4a843">动量</b>: 买涨最强的Top3<br>
              <b style="color:#5b8af0">均值回归</b>: 买跌最多的Top3<br>
              <b style="color:#4bae8a">多因子</b>: 按Alpha158综合评分<br><br>
              <b style="color:#fff">手续费</b>: 万3 + 千1印花税<br>
              <b style="color:#fff">滑点</b>: 0.1%
            </div>""", unsafe_allow_html=True)

        if st.button("▶ 开始回测", use_container_width=False, key="run_backtest"):
            codes = [c.strip() for c in bt_codes.split(",") if c.strip()]
            freq_map = {"W（每周）":"W","M（每月）":"M","D（每日）":"D"}
            freq = freq_map.get(bt_freq, "W")

            with st.spinner("抓取数据并运行回测..."):
                try:
                    from quant.data import get_batch_ohlcv
                    from quant.backtest import Backtester, strategy_momentum, strategy_mean_reversion, strategy_factor_rank

                    price_data = get_batch_ohlcv(codes, days=bt_days + 60)
                    if not price_data:
                        st.error("无法获取数据")
                    else:
                        strat_map = {
                            "动量策略 (Momentum)":        strategy_momentum,
                            "均值回归策略 (Mean Reversion)": strategy_mean_reversion,
                            "多因子排名策略 (Factor Rank)":  strategy_factor_rank,
                        }
                        signal_fn = strat_map[bt_strategy]
                        bt = Backtester(initial_capital=bt_capital)
                        result = bt.run(price_data, signal_fn, rebalance_freq=freq)

                        # 显示结果
                        st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
                        st.markdown('<div class="n-section-title">回测结果</div>', unsafe_allow_html=True)

                        m1,m2,m3,m4 = st.columns(4)
                        metrics = [
                            ("总收益", f"{result.total_return:.2%}", "#4bae8a" if result.total_return>0 else "#e16b6b"),
                            ("年化收益", f"{result.annual_return:.2%}", "#4bae8a" if result.annual_return>0 else "#e16b6b"),
                            ("最大回撤", f"{result.max_drawdown:.2%}", "#e16b6b"),
                            ("夏普比率", f"{result.sharpe_ratio:.3f}", "#4bae8a" if result.sharpe_ratio>1 else "#d4a843"),
                        ]
                        for col, (label, val, color) in zip([m1,m2,m3,m4], metrics):
                            with col:
                                st.markdown(f"""
                                <div class="n-card" style="text-align:center;padding:14px">
                                  <div style="font-size:20px;font-weight:700;color:{color}">{val}</div>
                                  <div style="font-size:11px;color:#5b5b5b;margin-top:3px">{label}</div>
                                </div>""", unsafe_allow_html=True)

                        m5,m6,m7,m8 = st.columns(4)
                        metrics2 = [
                            ("卡玛比率", f"{result.calmar_ratio:.3f}", "#d4a843"),
                            ("胜率",     f"{result.win_rate:.2%}", "#4bae8a" if result.win_rate>0.5 else "#e16b6b"),
                            ("盈亏比",   f"{result.profit_loss_ratio:.2f}", "#4bae8a" if result.profit_loss_ratio>1 else "#e16b6b"),
                            ("总交易",   f"{result.total_trades}笔", "#9b9b9b"),
                        ]
                        for col, (label, val, color) in zip([m5,m6,m7,m8], metrics2):
                            with col:
                                st.markdown(f"""
                                <div class="n-card" style="text-align:center;padding:14px">
                                  <div style="font-size:20px;font-weight:700;color:{color}">{val}</div>
                                  <div style="font-size:11px;color:#5b5b5b;margin-top:3px">{label}</div>
                                </div>""", unsafe_allow_html=True)

                        # 净值曲线
                        if not result.equity_curve.empty:
                            import plotly.graph_objects as go
                            eq = result.equity_curve
                            fig = go.Figure()
                            fig.add_trace(go.Scatter(
                                x=list(eq.index), y=eq.values,
                                mode="lines", name="策略净值",
                                line=dict(color="#4bae8a", width=2)
                            ))
                            fig.add_hline(y=bt_capital, line_dash="dash",
                                          line_color="#5b5b5b", annotation_text="初始资金")
                            fig.update_layout(
                                template="plotly_dark",
                                paper_bgcolor="#191919",
                                plot_bgcolor="#191919",
                                height=300, margin=dict(l=0,r=0,t=20,b=0),
                                showlegend=True,
                                font=dict(color="#9b9b9b", size=11),
                            )
                            st.plotly_chart(fig, use_container_width=True)

                except Exception as e:
                    st.error(f"回测失败：{e}")
                    import traceback; st.code(traceback.format_exc())

    # ══════════════════════════════════════════════════
    #  Tab3：强化学习（参考 FinRL）
    # ══════════════════════════════════════════════════
    with tab3:
        st.markdown('<div class="n-section-title">强化学习策略（参考 FinRL DRLAgent）</div>',
                    unsafe_allow_html=True)
        st.markdown("""
        <div class="n-card" style="padding:12px 16px;border-left:3px solid #5b8af0;margin-bottom:16px">
          <div style="font-size:12px;color:#9b9b9b;line-height:1.8">
            使用 PPO/A2C 强化学习算法，让 AI 自主学习 ETF 仓位配置策略。
            Agent 通过最大化累计收益来优化动作（每只 ETF 的仓位权重）。<br>
            <b style="color:#d4a843">⚠️ 需要安装：pip install stable-baselines3 gymnasium</b>
          </div>
        </div>""", unsafe_allow_html=True)

        rl1, rl2 = st.columns([2,1])
        with rl1:
            rl_codes = st.text_input("训练标的", value="512480,159611,513160,515880,588200")
            rl_algo  = st.selectbox("算法", ["PPO","A2C","DDPG","TD3","SAC"])
            rl_steps = st.slider("训练步数", 10000, 200000, 50000, step=10000)
            rl_days  = st.slider("训练数据天数", 120, 365, 252)
            rl_capital = st.number_input("模拟资金", value=100000, step=10000, key="rl_cap")
        with rl2:
            st.markdown(f"""
            <div class="n-card" style="padding:14px;font-size:12px;color:#6b6b6b;line-height:2">
              <b style="color:#fff">算法对比</b><br>
              <b style="color:#4bae8a">PPO</b>: 最稳定，推荐首选<br>
              <b style="color:#5b8af0">A2C</b>: 更快，适合小数据<br>
              <b style="color:#d4a843">DDPG</b>: 连续动作精确<br>
              <b style="color:#e16b6b">TD3</b>: DDPG改进版<br>
              <b style="color:#bc8cff">SAC</b>: 最大熵，探索强<br><br>
              训练 {rl_steps:,} 步约需<br>
              <b style="color:#fff">{rl_steps//5000} 分钟</b>
            </div>""", unsafe_allow_html=True)

        col_train, col_bt = st.columns(2)
        with col_train:
            if st.button("🎮 开始训练", use_container_width=True, key="rl_train"):
                codes = [c.strip() for c in rl_codes.split(",") if c.strip()]
                with st.spinner(f"训练 {rl_algo} 策略中（{rl_steps:,} 步）..."):
                    try:
                        from quant.data import get_batch_ohlcv
                        from quant.rl_strategy import ETFTradingEnv, RLTrainer

                        price_data = get_batch_ohlcv(codes, days=rl_days)
                        if not price_data:
                            st.error("无法获取数据")
                        else:
                            env     = ETFTradingEnv(price_data, initial_capital=rl_capital)
                            trainer = RLTrainer(algorithm=rl_algo)
                            trainer.train(env, total_timesteps=rl_steps, verbose=0)
                            model_path = trainer.save(f"etf_{rl_algo.lower()}")
                            st.session_state["rl_trainer"] = trainer
                            st.session_state["rl_env"]     = env
                            st.success(f"✅ {rl_algo} 训练完成！模型已保存：{model_path}")

                    except ImportError:
                        st.error("请先安装：pip install --no-cache-dir stable-baselines3 gymnasium")
                    except Exception as e:
                        st.error(f"训练失败：{e}")

        with col_bt:
            if st.button("📊 回测已训练模型", use_container_width=True, key="rl_bt"):
                if "rl_trainer" not in st.session_state:
                    st.warning("请先训练模型")
                else:
                    trainer = st.session_state["rl_trainer"]
                    env     = st.session_state["rl_env"]
                    with st.spinner("回测中..."):
                        try:
                            res = trainer.backtest(env)
                            r1,r2,r3,r4 = st.columns(4)
                            for col,(k,label) in zip([r1,r2,r3,r4],[
                                ("total_return","总收益"),("annual_return","年化"),
                                ("sharpe_ratio","夏普"),("max_drawdown","最大回撤")
                            ]):
                                v = res[k]
                                fmt = f"{v:.2%}" if "return" in k or "drawdown" in k else f"{v:.3f}"
                                color = "#4bae8a" if v > 0 else "#e16b6b"
                                with col:
                                    st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:18px;font-weight:700;color:{color}">{fmt}</div><div style="font-size:11px;color:#5b5b5b">{label}</div></div>', unsafe_allow_html=True)
                        except Exception as e:
                            st.error(f"回测失败：{e}")

    # ══════════════════════════════════════════════════
    #  Tab4：投资组合优化（参考 QLib Portfolio）
    # ══════════════════════════════════════════════════
    with tab4:
        st.markdown('<div class="n-section-title">投资组合优化（参考 QLib Portfolio Optimizer）</div>',
                    unsafe_allow_html=True)

        p1, p2 = st.columns([2,1])
        with p1:
            port_codes = st.text_input(
                "候选标的（逗号分隔）",
                value="512480,159611,513160,513130,515880,159995,588200,512660",
                key="port_codes",
            )
            port_method = st.selectbox("优化方法", [
                "多因子加权 (Factor)",
                "均值方差 (Mean-Variance)",
                "风险平价 (Risk Parity)",
                "等权 (Equal Weight)",
            ])
            port_top_n   = st.slider("持仓只数", 3, 8, 5)
            port_max_w   = st.slider("单只最大仓位", 0.2, 0.5, 0.4, step=0.05)
            port_capital = st.number_input("总资金（元）", value=26000, step=1000, key="port_cap")

        with p2:
            st.markdown("""
            <div class="n-card" style="padding:14px;font-size:12px;color:#6b6b6b;line-height:2">
              <b style="color:#fff">方法说明</b><br>
              <b style="color:#4bae8a">多因子</b>: 因子分越高权重越大<br>
              <b style="color:#5b8af0">均值方差</b>: 最大化夏普比率<br>
              <b style="color:#d4a843">风险平价</b>: 各资产等风险贡献<br>
              <b style="color:#9b9b9b">等权</b>: 简单均等分配
            </div>""", unsafe_allow_html=True)

        if st.button("⚖️ 计算最优组合", use_container_width=False, key="opt_port"):
            codes = [c.strip() for c in port_codes.split(",") if c.strip()]
            with st.spinner("计算中..."):
                try:
                    from quant.data import get_batch_ohlcv, get_realtime_quotes
                    from quant.factors import compute_alpha158, multi_factor_score
                    from quant.portfolio import build_portfolio, calc_portfolio_metrics

                    price_data = get_batch_ohlcv(codes, days=120)
                    if not price_data:
                        st.error("无法获取数据")
                    else:
                        # 因子评分
                        factor_data = {c: compute_alpha158(df) for c,df in price_data.items() if len(df)>=60}
                        composite   = multi_factor_score(factor_data)

                        # 收益率矩阵
                        returns = pd.DataFrame({
                            c: df["close"].pct_change()
                            for c, df in price_data.items()
                        }).dropna()

                        method_map = {
                            "多因子加权 (Factor)":      "factor",
                            "均值方差 (Mean-Variance)": "mv",
                            "风险平价 (Risk Parity)":   "rp",
                            "等权 (Equal Weight)":      "equal",
                        }
                        weights = build_portfolio(
                            composite, returns,
                            method=method_map[port_method],
                            top_n=port_top_n,
                            max_weight=port_max_w,
                        )

                        if weights.empty:
                            st.error("无法计算权重，数据不足")
                        else:
                            metrics = calc_portfolio_metrics(weights, returns)

                            # 获取实时价格
                            realtime = get_realtime_quotes(list(weights.index))

                            st.markdown('<hr class="n-divider">', unsafe_allow_html=True)
                            st.markdown('<div class="n-section-title">最优投资组合</div>', unsafe_allow_html=True)

                            # 组合指标
                            if metrics:
                                pm1,pm2,pm3,pm4 = st.columns(4)
                                for col,(k,lb) in zip([pm1,pm2,pm3,pm4],[
                                    ("annual_return","年化收益"),("annual_volatility","年化波动"),
                                    ("sharpe_ratio","夏普比率"),("max_drawdown","历史回撤")
                                ]):
                                    v = metrics.get(k,0)
                                    fmt = f"{v:.2%}" if k in ("annual_return","annual_volatility","max_drawdown") else f"{v:.3f}"
                                    color = "#4bae8a" if (k=="annual_return" and v>0) or (k=="sharpe_ratio" and v>1) else "#e16b6b" if k=="max_drawdown" else "#d4a843"
                                    with col:
                                        st.markdown(f'<div class="n-card" style="text-align:center;padding:12px"><div style="font-size:18px;font-weight:700;color:{color}">{fmt}</div><div style="font-size:11px;color:#5b5b5b">{lb}</div></div>', unsafe_allow_html=True)

                            # 权重分配
                            st.markdown('<div class="n-section-title" style="margin-top:16px">仓位分配</div>', unsafe_allow_html=True)
                            total_shares_info = []
                            for code, w in weights.sort_values(ascending=False).items():
                                alloc   = port_capital * w
                                rt      = realtime.get(code, {})
                                price   = rt.get("price", 0)
                                chg     = rt.get("chg",0) or rt.get("change_pct",0)
                                shares  = int(alloc / price / 100) * 100 if price > 0 else 0
                                actual  = shares * price if price > 0 else alloc
                                chg_c   = "#4bae8a" if chg>0 else "#e16b6b" if chg<0 else "#6b6b6b"

                                st.markdown(f"""
                                <div class="n-card" style="margin-bottom:8px;padding:12px 16px">
                                  <div style="display:flex;justify-content:space-between;align-items:center">
                                    <div>
                                      <span style="font-size:14px;font-weight:700;color:#fff">{code}</span>
                                      <span style="font-size:11px;color:#5b5b5b;margin-left:10px">权重 {w:.1%}</span>
                                    </div>
                                    <div style="text-align:right">
                                      <div style="font-size:14px;font-weight:700;color:#fff">¥{actual:,.0f}</div>
                                      <div style="font-size:11px;color:#5b5b5b">{shares}股 × ¥{price:.3f} <span style="color:{chg_c}">{chg:+.2f}%</span></div>
                                    </div>
                                  </div>
                                  <div class="bar-wrap" style="margin-top:8px">
                                    <div class="bar-fill-green" style="width:{w/port_max_w*100:.0f}%"></div>
                                  </div>
                                </div>""", unsafe_allow_html=True)
                                total_shares_info.append({"code":code,"weight":w,"amount":actual,"shares":shares})

                            total_used = sum(x["amount"] for x in total_shares_info)
                            st.markdown(f"""
                            <div class="n-card" style="padding:12px 16px;border-left:3px solid #5b8af0;margin-top:8px">
                              <div style="display:flex;justify-content:space-between;font-size:13px">
                                <span style="color:#9b9b9b">总资金: ¥{port_capital:,}</span>
                                <span style="color:#4bae8a">计划投入: ¥{total_used:,.0f} ({total_used/port_capital:.1%})</span>
                                <span style="color:#d4a843">留存: ¥{port_capital-total_used:,.0f}</span>
                              </div>
                            </div>""", unsafe_allow_html=True)

                except Exception as e:
                    st.error(f"组合优化失败：{e}")
                    import traceback; st.code(traceback.format_exc())

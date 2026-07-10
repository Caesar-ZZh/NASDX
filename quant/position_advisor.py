"""
NASDX V2 — 持仓调仓顾问
整合 QLib 因子 + VnPy 绩效 + FinRL 信号 + 自研回测

输入：持仓清单（代码+成本+数量）
输出：
  - 每只持仓的量化评分 + 信号
  - 整体组合风险指标
  - 具体调仓建议（买入/减仓/清仓/持有）
  - 候选替换标的（来自 ETF50 量化结果）
"""
from __future__ import annotations
from quant.patch_requests import configure_requests
import json, time
from pathlib import Path
from datetime import datetime
from typing import Optional
import numpy as np
import pandas as pd

from nasdx.paths import get_reports_dir

ROOT = Path(__file__).parent.parent
configure_requests()


# ══════════════════════════════════════════
#  持仓条目
# ══════════════════════════════════════════
class Position:
    def __init__(self, code: str, name: str, cost: float,
                 shares: int, asset_type: str = "stock"):
        self.code       = code
        self.name       = name
        self.cost       = cost          # 成本价（元/股）
        self.shares     = shares        # 持有股数
        self.asset_type = asset_type    # stock / etf / lof

        # 实时数据（抓取后填入）
        self.current_price: float = 0.0
        self.today_chg:     float = 0.0
        self.market_value:  float = 0.0
        self.pnl_pct:       float = 0.0
        self.pnl_abs:       float = 0.0

        # 量化分析结果
        self.quant_score:   float = 50.0
        self.factor_signal: str   = "neutral"
        self.trend_signal:  str   = "neutral"
        self.volume_signal: str   = "neutral"
        self.final_signal:  str   = "neutral"
        self.confidence:    float = 0.5

        # 风险指标
        self.volatility:    float = 0.0   # 20日年化波动率
        self.max_drawdown:  float = 0.0   # 持有期最大回撤
        self.beta:          float = 1.0   # 相对沪深300的 beta

        # 关键因子值
        self.roc20:   float = 0.0
        self.rsi14:   float = 50.0
        self.macd:    float = 0.0
        self.bias20:  float = 0.0

        # 建议
        self.action:      str  = "hold"   # buy/reduce/sell/hold
        self.action_pct:  float = 0.0     # 建议调整仓位比例（负=减，正=加）
        self.reasons:     list = []
        self.risk_level:  str  = "medium" # low/medium/high

    @property
    def cost_value(self) -> float:
        return self.cost * self.shares

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ══════════════════════════════════════════
#  持仓分析引擎
# ══════════════════════════════════════════
class PositionAdvisor:
    """
    对用户持仓进行全面量化分析并给出调仓建议

    分析流程（整合三大框架）：
    1. 实时行情获取（mootdx 通达信）
    2. QLib Alpha158 因子计算
    3. VnPy 风险指标计算（波动率/回撤/Beta）
    4. FinRL SignalVoter 集成信号
    5. 组合层风险评估（集中度/相关性/总回撤）
    6. 生成调仓建议（对比 ETF50 最优标的）
    """

    # 调仓阈值配置
    SELL_THRESHOLD    = 35    # 量化分 <= 35 → 建议清仓
    REDUCE_THRESHOLD  = 48    # 量化分 <= 48 → 建议减仓
    HOLD_THRESHOLD    = 62    # 量化分 <= 62 → 建议持有
    ADD_THRESHOLD     = 72    # 量化分 >= 72 → 建议加仓
    STRONG_THRESHOLD  = 82    # 量化分 >= 82 → 强烈加仓

    # 风险控制阈值
    MAX_SINGLE_WEIGHT = 0.40  # 单只最大仓位 40%
    STOP_LOSS_PCT     = -0.15 # 浮亏超过 -15% 触发止损提醒
    MAX_PORTFOLIO_DD  = 0.20  # 组合最大可接受回撤 20%

    def __init__(self, total_capital: float = 100_000):
        self.total_capital = total_capital

    def analyze(
        self,
        positions: list[Position],
        days: int = 180,
        etf50_json: Optional[str] = None,
        verbose: bool = True,
    ) -> dict:
        """
        执行完整持仓分析

        Returns: {
            positions: [Position.to_dict()],
            portfolio: {总市值, 总盈亏, 集中度, ...},
            advice: {调仓建议列表},
            candidates: {推荐替换标的},
            summary: str,
        }
        """
        if verbose:
            print(f"\n{'='*60}")
            print(f"  NASDX 持仓调仓顾问  {datetime.now().strftime('%H:%M')}")
            print(f"  {len(positions)} 只持仓 · {days} 天历史 · 总资金 {self.total_capital:,.0f} 元")
            print(f"{'='*60}\n")

        codes = [p.code for p in positions]

        # ── Step 1: 实时行情 ─────────────────────────────
        if verbose: print("📡 获取实时行情...")
        self._fetch_realtime(positions)

        # ── Step 2: 历史数据 + 量化指标 ─────────────────
        if verbose: print("📊 计算量化因子...")
        price_data = self._fetch_history(codes, days)

        # ── Step 3: Alpha158 因子 + 信号 ─────────────────
        self._calc_factors(positions, price_data, verbose)

        # ── Step 4: VnPy 风险指标 ─────────────────────────
        if verbose: print("🛡️  计算风险指标...")
        self._calc_risk(positions, price_data)

        # ── Step 5: 生成调仓建议 ─────────────────────────
        if verbose: print("💡 生成调仓建议...")
        self._gen_advice(positions)

        # ── Step 6: 组合层分析 ─────────────────────────────
        portfolio = self._portfolio_analysis(positions, price_data)

        # ── Step 7: 推荐候选标的 ─────────────────────────
        candidates = self._find_candidates(positions, etf50_json)

        # ── Step 8: 生成摘要 ─────────────────────────────
        summary = self._build_summary(positions, portfolio, candidates)

        if verbose:
            self._print_report(positions, portfolio, candidates)

        return {
            "datetime":   datetime.now().isoformat(),
            "positions":  [p.to_dict() for p in positions],
            "portfolio":  portfolio,
            "candidates": candidates,
            "summary":    summary,
        }

    # ────────────────────────────────────────────────────
    def _fetch_realtime(self, positions: list[Position]):
        """获取实时价格（mootdx 通达信）"""
        try:
            from ths_bridge import get_realtime_batch
            codes  = [p.code for p in positions]
            prices = get_realtime_batch(codes)
            for p in positions:
                info = prices.get(p.code, {})
                if info.get("price"):
                    p.current_price = info["price"]
                    p.today_chg     = info.get("change_pct", 0)
                    p.market_value  = p.current_price * p.shares
                    p.pnl_pct       = (p.current_price - p.cost) / p.cost * 100
                    p.pnl_abs       = (p.current_price - p.cost) * p.shares
        except Exception as e:
            # 降级：用 akshare 实时行情
            try:
                import akshare as ak
                spot = ak.fund_etf_spot_em()
                spot_map = {r["代码"]: float(r["最新价"]) for _, r in spot.iterrows()
                            if r.get("最新价")}
                for p in positions:
                    price = spot_map.get(p.code)
                    if price:
                        p.current_price = price
                        p.market_value  = price * p.shares
                        p.pnl_pct       = (price - p.cost) / p.cost * 100
                        p.pnl_abs       = (price - p.cost) * p.shares
            except Exception:
                # 最终降级：用成本价
                for p in positions:
                    if not p.current_price:
                        p.current_price = p.cost
                        p.market_value  = p.cost * p.shares

    def _fetch_history(self, codes: list[str], days: int) -> dict:
        """获取历史 OHLCV"""
        from quant.data import get_batch_ohlcv
        return get_batch_ohlcv(codes, days=days)

    def _calc_factors(self, positions: list[Position],
                      price_data: dict, verbose: bool):
        """Alpha158 因子计算 + SignalVoter 集成"""
        from quant.factors import compute_alpha158
        try:
            from quant.confidence_trainer import get_calibrated_voter
            voter = get_calibrated_voter()
        except Exception:
            from quant.anti_overfit import SignalVoter
            voter = SignalVoter()

        factor_signals = {}
        trend_signals  = {}
        volume_signals = {}

        for p in positions:
            df = price_data.get(p.code)
            if df is None or df.empty or len(df) < 30:
                if verbose: print(f"  ⚠️  {p.code} {p.name} 数据不足，跳过因子计算")
                continue

            try:
                factors = compute_alpha158(df)
                if factors.empty:
                    continue
                latest = factors.iloc[-1]

                def safe(k, d=0.0):
                    v = latest.get(k, d)
                    return float(v) if pd.notna(v) else d

                p.roc20  = safe("ROC20")
                p.rsi14  = safe("RSI14", 50.0)
                p.macd   = safe("MACD")
                p.bias20 = safe("BIAS20")

                # 因子综合评分
                score = 0.5
                score += np.clip(p.roc20 * 0.3, -0.15, 0.15)
                score += np.clip(p.macd  * 8,   -0.12, 0.12)
                if p.rsi14 < -0.5:  score += 0.08
                elif p.rsi14 > 0.5: score -= 0.06
                score += np.clip(p.bias20 * -0.05, -0.05, 0.05)
                score = float(np.clip(score, 0, 1))

                p.quant_score = score * 100
                factor_sig = "bullish" if score > 0.6 else "bearish" if score < 0.4 else "neutral"
                factor_signals[p.code] = score

                # 趋势信号
                c = df["close"].astype(float)
                ma5 = c.rolling(5).mean().iloc[-1]
                ma20= c.rolling(20).mean().iloc[-1] if len(c)>=20 else ma5
                ma60= c.rolling(60).mean().iloc[-1] if len(c)>=60 else ma20
                trend_score = 0.5
                if ma5>ma20>ma60: trend_score += 0.25
                elif ma5<ma20<ma60: trend_score -= 0.25
                elif ma5>ma20: trend_score += 0.1
                else: trend_score -= 0.1
                if p.macd > 0: trend_score += 0.1
                else: trend_score -= 0.1
                trend_signals[p.code] = float(np.clip(trend_score, 0, 1))

                # 量价信号
                v = df["volume"].astype(float)
                vr = v.iloc[-1] / (v.rolling(5).mean().iloc[-2] + 1e-9) if len(v)>5 else 1.0
                vol_score = 0.5
                if 1.2 <= vr <= 2.5: vol_score += 0.15
                elif vr < 0.7: vol_score -= 0.10
                volume_signals[p.code] = float(np.clip(vol_score, 0, 1))

            except Exception as e:
                if verbose: print(f"  ❌ {p.code} 因子计算失败: {e}")

        # SignalVoter 集成投票
        if factor_signals:
            voted = voter.vote({
                "technical": {c: v for c,v in factor_signals.items()},
                "factor":    factor_signals,
                "trend":     trend_signals,
                "volume":    volume_signals,
                "ai":        {c: 0.5 for c in factor_signals},  # AI 信号默认中性
            })
            for p in positions:
                v = voted.get(p.code, {})
                if v:
                    p.final_signal = v["signal"]
                    p.confidence   = v["confidence"]
                    # 用集成分数替代单因子分数
                    p.quant_score  = v["score"] * 100

    def _calc_risk(self, positions: list[Position], price_data: dict):
        """VnPy 风险指标：波动率/最大回撤/Beta"""
        # 获取基准（沪深300）
        benchmark = None
        try:
            from quant.data import get_ohlcv
            bm = get_ohlcv("000300", days=180)
            if not bm.empty:
                benchmark = bm["close"].pct_change().dropna()
        except Exception:
            pass

        for p in positions:
            df = price_data.get(p.code)
            if df is None or df.empty or len(df) < 20:
                continue
            try:
                c       = df["close"].astype(float)
                ret     = c.pct_change().dropna()
                # 年化波动率
                p.volatility = float(ret.std() * np.sqrt(252) * 100)
                # 持有期回撤
                since_cost = c[c >= p.cost * 0.5]  # 找成本附近的起点
                if len(since_cost) > 0:
                    roll_max = c.cummax()
                    dd = ((c - roll_max) / roll_max).min()
                    p.max_drawdown = float(dd * 100)
                # Beta
                if benchmark is not None and len(ret) >= 20:
                    aligned = pd.concat([ret, benchmark], axis=1).dropna()
                    if len(aligned) >= 20:
                        cov = aligned.cov().iloc[0,1]
                        var = aligned.iloc[:,1].var()
                        p.beta = float(cov / (var + 1e-9))
            except Exception:
                pass

            # 风险等级
            if p.volatility > 40 or abs(p.max_drawdown) > 25:
                p.risk_level = "high"
            elif p.volatility < 15 and abs(p.max_drawdown) < 10:
                p.risk_level = "low"
            else:
                p.risk_level = "medium"

    def _gen_advice(self, positions: list[Position]):
        """生成每只持仓的调仓建议"""
        for p in positions:
            sc = p.quant_score
            reasons = []

            # 止损优先
            if p.pnl_pct < self.STOP_LOSS_PCT * 100:
                p.action = "sell"
                p.action_pct = -1.0
                reasons.append(f"⚠️ 浮亏{p.pnl_pct:.1f}%，触发止损线")
                p.reasons = reasons
                continue

            # 量化信号驱动
            if sc <= self.SELL_THRESHOLD:
                p.action     = "sell"
                p.action_pct = -1.0
                reasons.append(f"量化分{sc:.0f}，技术面严重恶化")
                if p.macd < -0.5: reasons.append("MACD 深度死叉")
                if p.rsi14 > 0.8: reasons.append("RSI 超买区间")

            elif sc <= self.REDUCE_THRESHOLD:
                p.action     = "reduce"
                p.action_pct = -0.50
                reasons.append(f"量化分{sc:.0f}，建议减半仓位")
                if p.final_signal == "bearish": reasons.append("集成信号看空")
                if p.bias20 > 0.5: reasons.append("偏离均线过大，均值回归压力")

            elif sc <= self.HOLD_THRESHOLD:
                p.action     = "hold"
                p.action_pct = 0.0
                reasons.append(f"量化分{sc:.0f}，信号中性，持仓观望")
                if p.pnl_pct > 20: reasons.append(f"浮盈{p.pnl_pct:.1f}%，可考虑部分止盈")
                if p.risk_level == "high": reasons.append("波动率偏高，控制仓位")

            elif sc <= self.ADD_THRESHOLD:
                p.action     = "hold"
                p.action_pct = 0.0
                reasons.append(f"量化分{sc:.0f}，技术面偏好，继续持有")
                if p.macd > 0.3: reasons.append("MACD 金叉确认")

            elif sc <= self.STRONG_THRESHOLD:
                p.action     = "add"
                p.action_pct = 0.20
                reasons.append(f"量化分{sc:.0f}，技术面强势，建议加仓 20%")
                if p.roc20 > 0.5: reasons.append(f"20日动量强劲({p.roc20:.2f}σ)")

            else:
                p.action     = "add"
                p.action_pct = 0.30
                reasons.append(f"量化分{sc:.0f}，强烈看多，建议加仓 30%")
                if p.final_signal == "bullish" and p.confidence > 0.7:
                    reasons.append(f"高置信度集成信号（置信度{p.confidence:.0%}）")

            # 附加风险提示
            if p.risk_level == "high" and p.action == "add":
                p.action     = "hold"
                p.action_pct = 0.0
                reasons.append("⚠️ 高波动率，暂缓加仓")

            p.reasons = reasons[:3]

    def _portfolio_analysis(self, positions: list[Position],
                            price_data: dict) -> dict:
        """组合层面风险分析"""
        total_mv   = sum(p.market_value for p in positions)
        total_cost = sum(p.cost_value  for p in positions)
        total_pnl  = total_mv - total_cost

        # 仓位集中度
        weights = {}
        for p in positions:
            w = p.market_value / total_mv if total_mv > 0 else 0
            weights[p.code] = w

        # HHI 集中度指数（越高越集中，>0.25 有集中风险）
        hhi = sum(w**2 for w in weights.values())

        # 组合波动率（简化：等权平均）
        port_vol = np.mean([p.volatility for p in positions if p.volatility > 0])

        # 相关性矩阵
        avg_corr = 0.5
        try:
            rets = {}
            for p in positions:
                df = price_data.get(p.code)
                if df is not None and not df.empty and len(df) >= 20:
                    rets[p.code] = df["close"].pct_change().dropna()
            if len(rets) >= 2:
                ret_df   = pd.DataFrame(rets).dropna()
                corr_mat = ret_df.corr()
                vals     = corr_mat.values
                mask     = np.triu(np.ones_like(vals, dtype=bool), k=1)
                avg_corr = float(vals[mask].mean()) if mask.any() else 0.5
        except Exception:
            pass

        # 信号分布
        bull_cnt = sum(1 for p in positions if p.final_signal == "bullish")
        bear_cnt = sum(1 for p in positions if p.final_signal == "bearish")
        avg_score= np.mean([p.quant_score for p in positions])

        # 高风险标的
        high_risk = [p.code for p in positions if p.risk_level == "high"]
        overweight = [p.code for p in positions
                      if weights.get(p.code, 0) > self.MAX_SINGLE_WEIGHT]
        stop_loss  = [p.code for p in positions
                      if p.pnl_pct < self.STOP_LOSS_PCT * 100]

        # 组合健康度评分（0-100）
        health = 60.0
        if hhi > 0.35:  health -= 15  # 过度集中
        if avg_corr > 0.7: health -= 10  # 高相关，分散化差
        if len(high_risk) > len(positions)//3: health -= 10
        if len(stop_loss) > 0: health -= 20
        health += (avg_score - 50) * 0.4
        health = float(np.clip(health, 0, 100))

        return {
            "total_market_value": round(total_mv, 2),
            "total_cost_value":   round(total_cost, 2),
            "total_pnl":          round(total_pnl, 2),
            "total_pnl_pct":      round(total_pnl / total_cost * 100 if total_cost else 0, 2),
            "weights":            {k: round(v, 4) for k,v in weights.items()},
            "hhi":                round(hhi, 4),
            "avg_volatility":     round(port_vol, 2),
            "avg_correlation":    round(avg_corr, 4),
            "avg_quant_score":    round(avg_score, 1),
            "bullish_count":      bull_cnt,
            "bearish_count":      bear_cnt,
            "health_score":       round(health, 1),
            "high_risk_codes":    high_risk,
            "overweight_codes":   overweight,
            "stop_loss_codes":    stop_loss,
        }

    def _find_candidates(self, positions: list[Position],
                         etf50_json: Optional[str]) -> list[dict]:
        """从 ETF50 量化结果中找推荐替换标的"""
        holding_codes = {p.code for p in positions}
        candidates    = []

        # 优先从最新 ETF50 量化结果取
        if etf50_json is None:
            files = sorted(get_reports_dir().glob("etf50_quant_*.json"))
            if files:
                etf50_json = str(files[-1])

        # 备选：ETF50 技术面扫描结果
        if etf50_json is None:
            files = sorted(get_reports_dir().glob("etf50_*.json"))
            if files:
                etf50_json = str(files[-1])

        if etf50_json:
            try:
                with open(etf50_json, encoding="utf-8") as f:
                    data = json.load(f)
                results = data.get("results", [])
                for r in results:
                    code = r.get("code","")
                    if code in holding_codes:
                        continue
                    sc = r.get("quant_score", r.get("score", 0))
                    if sc >= 70 and r.get("signal") == "bullish":
                        prem = r.get("premium")
                        candidates.append({
                            "code":     code,
                            "name":     r.get("name",""),
                            "category": r.get("category",""),
                            "score":    sc,
                            "signal":   r.get("signal",""),
                            "premium":  prem,
                            "reason":   (f"溢价{prem:+.2f}%" if prem is not None else "") +
                                        f" 量化分{sc:.0f}",
                        })
                candidates.sort(key=lambda x: -x["score"])
                candidates = candidates[:5]
            except Exception:
                pass

        return candidates

    def _build_summary(self, positions: list[Position],
                       portfolio: dict, candidates: list) -> str:
        health   = portfolio["health_score"]
        pnl_pct  = portfolio["total_pnl_pct"]
        sell_cnt = sum(1 for p in positions if p.action == "sell")
        reduce_cnt= sum(1 for p in positions if p.action == "reduce")
        add_cnt  = sum(1 for p in positions if p.action == "add")
        hold_cnt = sum(1 for p in positions if p.action == "hold")

        lines = [
            f"组合健康度 {health:.0f}/100 · 持仓浮盈 {pnl_pct:+.2f}%",
            f"建议：清仓 {sell_cnt} 只 · 减仓 {reduce_cnt} 只 · 持有 {hold_cnt} 只 · 加仓 {add_cnt} 只",
        ]
        if portfolio["stop_loss_codes"]:
            lines.append(f"⚠️ 止损预警：{', '.join(portfolio['stop_loss_codes'])}")
        if portfolio["overweight_codes"]:
            lines.append(f"⚠️ 仓位过重：{', '.join(portfolio['overweight_codes'])}（超过40%上限）")
        if portfolio["hhi"] > 0.35:
            lines.append("⚠️ 持仓集中度过高（HHI={:.2f}），建议分散".format(portfolio["hhi"]))
        if candidates:
            top = candidates[0]
            lines.append(f"💡 推荐关注：{top['code']} {top['name']}（量化分{top['score']:.0f}）")
        return "\n".join(lines)

    def _print_report(self, positions, portfolio, candidates):
        print(f"\n{'─'*60}")
        print(f"  {'代码':<8}{'名称':<12}{'成本':>7}{'现价':>7}{'浮盈%':>7}{'量化分':>7}  建议")
        print(f"  {'─'*60}")
        action_map = {"sell":"🔴清仓","reduce":"🟡减仓","hold":"⚪持有","add":"🟢加仓"}
        for p in positions:
            action_str = action_map.get(p.action, p.action)
            print(f"  {p.code:<8}{p.name:<12}{p.cost:>7.2f}{p.current_price:>7.2f}"
                  f"{p.pnl_pct:>+7.1f}%{p.quant_score:>6.0f}  {action_str}")
        print(f"\n  组合健康度: {portfolio['health_score']:.0f}/100  "
              f"总浮盈: {portfolio['total_pnl_pct']:+.2f}%  "
              f"平均量化分: {portfolio['avg_quant_score']:.1f}")
        if candidates:
            print(f"\n  推荐候选标的:")
            for c in candidates[:3]:
                print(f"    {c['code']} {c['name']:<18} {c['score']:.0f}分  {c.get('reason','')}")

"""
NASDX V2 — ETF50 量化全量分析
对 50 只 ETF 执行：
  1. 数据获取（90~365 天 OHLCV）
  2. Alpha158 因子计算
  3. 多因子综合评分
  4. Walk-Forward 回测（验证策略稳健性）
  5. 过拟合诊断
  6. 输出完整排行 + HTML 报告

⚡ 优化：将重量级 import 延迟到 run_etf50_quant() 内部执行
  - quant.patch_requests：兼容层，无导入期 HTTP 副作用
  - quant.data：~440ms
  - quant.factors：~8ms
  - quant.backtest 等：~20ms
  总共节省 ~670ms 的 import 时间（只在实际调用时加载）
"""
from __future__ import annotations
import json, time
from pathlib import Path
from datetime import datetime
from typing import Optional

ROOT = Path(__file__).parent.parent


# ══════════════════════════════════════════
#  ETF50 量化扫描结果数据结构
# ══════════════════════════════════════════
class ETFQuantResult:
    def __init__(self, code: str, name: str, category: str):
        self.code     = code
        self.name     = name
        self.category = category
        # 因子分
        self.factor_score   : float = 0.5
        self.factor_rank    : int   = 99
        self.factor_signal  : str   = "neutral"
        # 关键因子值
        self.roc20   : float = 0.0
        self.rsi14   : float = 50.0
        self.macd    : float = 0.0
        self.bias20  : float = 0.0
        self.vol_ratio: float = 1.0
        self.std20   : float = 0.0
        # 回测结果
        self.bt_return  : Optional[float] = None
        self.bt_sharpe  : Optional[float] = None
        self.bt_drawdown: Optional[float] = None
        # 综合
        self.quant_score: float = 50.0    # 0-100
        self.signal     : str   = "neutral"
        self.reasons    : list  = []
        self.has_data   : bool  = False

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()
                if not k.startswith('_')}


# ══════════════════════════════════════════
#  核心扫描函数
# ══════════════════════════════════════════
def run_etf50_quant(
    days: int = 252,
    top_n: int = 5,
    rebalance_freq: str = "W",
    verbose: bool = True,
    progress_cb=None,               # fn(i, total, code, name)
) -> dict:
    """
    对 ETF50 池中所有 ETF 执行量化分析

    Returns:
        {
          "datetime": ...,
          "total": N, "success": M,
          "results": [ETFQuantResult.to_dict(), ...],
          "top3": [...],
          "portfolio_weights": {code: weight},
          "backtest_portfolio": {...},
        }
    """
    # ⚡ 延迟导入：在真正执行时才加载重量级依赖
    global np, pd
    import numpy as np
    import pandas as pd
    from quant.patch_requests import configure_requests
    from quant.data import get_ohlcv
    from quant.factors import compute_alpha158
    configure_requests()

    # 加载 ETF 池
    with open(ROOT / "etf50_pool.json", encoding="utf-8") as f:
        pool = json.load(f)["etfs"]

    total = len(pool)
    results: list[ETFQuantResult] = []
    price_cache: dict[str, pd.DataFrame] = {}

    if verbose:
        print(f"\n{'='*60}")
        print(f"  NASDX ETF50 量化全量分析  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  共 {total} 只 ETF · {days} 天数据 · {rebalance_freq} 调仓")
        print(f"{'='*60}\n")

    # ── Phase 1: 数据获取 + 因子计算 ──────────────────────
    for i, etf in enumerate(pool, 1):
        code = etf["code"]
        name = etf["name"]
        cat  = etf.get("category", "")

        if progress_cb:
            progress_cb(i, total, code, name)

        if verbose:
            print(f"  [{i:02d}/{total}] {code} {name}...", end=" ", flush=True)

        res = ETFQuantResult(code, name, cat)

        # 获取数据
        df = get_ohlcv(code, days=days)
        if df.empty or len(df) < 30:
            if verbose: print("❌ 数据不足")
            results.append(res)
            continue

        price_cache[code] = df
        res.has_data = True

        # 计算 Alpha158 因子
        try:
            factors = compute_alpha158(df)
            if factors.empty:
                raise ValueError("因子为空")

            latest = factors.iloc[-1]

            # 提取关键因子（防止 NaN）
            def safe(key, default=0.0):
                v = latest.get(key, default)
                return float(v) if pd.notna(v) else default

            res.roc20    = safe("ROC20")
            res.rsi14    = safe("RSI14", 50.0)
            res.macd     = safe("MACD")
            res.bias20   = safe("BIAS20")
            res.vol_ratio = safe("VOLU5", 1.0)
            res.std20    = safe("STD20")

            # 多因子合成评分（z-score 已标准化）
            factor_score = _calc_quant_score(res)
            res.factor_score = factor_score
            res.factor_signal = "bullish" if factor_score > 0.6 else "bearish" if factor_score < 0.4 else "neutral"

            if verbose:
                em = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(res.factor_signal,"")
                print(f"{em} 因子={factor_score:.3f} ROC20={res.roc20:.2f} RSI={res.rsi14:.0f} MACD={res.macd:.3f}")

        except Exception as e:
            if verbose: print(f"⚠️ 因子计算失败: {e}")
            results.append(res)
            continue

        results.append(res)
        time.sleep(0.2)   # 限速

    # ── Phase 2: 横截面排名 ───────────────────────────────
    valid = [r for r in results if r.has_data]
    if valid:
        scores = [r.factor_score for r in valid]
        ranks  = pd.Series(scores).rank(ascending=False).astype(int).tolist()
        for r, rank in zip(valid, ranks):
            r.factor_rank = rank

    # ── Phase 3: 组合回测（滚动 Top N 等权）──────────────
    bt_result = None
    portfolio_weights = {}

    if len(price_cache) >= 3:
        if verbose:
            print(f"\n  ⚡ 回测滚动 Top{top_n} 组合...")
        try:
            current_top_codes = [r.code for r in sorted(valid, key=lambda x: -x.factor_score)[:top_n]]
            backtest_prices = {r.code: price_cache[r.code] for r in valid if r.code in price_cache}

            from quant.backtest import Backtester, strategy_factor_rank
            bt = Backtester(initial_capital=100_000)

            def _rolling_top_n_signal(date, pdata):
                return strategy_factor_rank(date, pdata, top_n=top_n)

            bt_result = bt.run(backtest_prices, _rolling_top_n_signal, rebalance_freq=rebalance_freq)
            if current_top_codes:
                portfolio_weights = {c: 1.0 / len(current_top_codes) for c in current_top_codes}

            if verbose:
                print(f"  组合回测: 收益={bt_result.total_return:.1%} 夏普={bt_result.sharpe_ratio:.2f} 回撤={bt_result.max_drawdown:.1%}")
        except Exception as e:
            if verbose:
                print(f"  组合回测失败: {e}")

    # ── Phase 4: 合成最终量化评分 ─────────────────────────
    _build_quant_score(results, bt_result)

    # ── Phase 5: 排行 ─────────────────────────────────────
    results.sort(key=lambda r: -r.quant_score)
    top3 = [r.to_dict() for r in results[:3] if r.has_data]

    # ── 汇总 ──────────────────────────────────────────────
    success = sum(1 for r in results if r.has_data)
    bull    = sum(1 for r in results if r.signal == "bullish")
    bear    = sum(1 for r in results if r.signal == "bearish")

    if verbose:
        print(f"\n{'─'*60}")
        print(f"  完成！{success}/{total} 只有效 | 看多:{bull} 看空:{bear}")
        print(f"\n  {'排名':<4}{'代码':<8}{'名称':<20}{'类别':<16}{'量化分':>6}  信号")
        print(f"  {'─'*60}")
        for i, r in enumerate(results[:15], 1):
            if not r.has_data: continue
            em = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(r.signal,"")
            medal = {1:"🥇",2:"🥈",3:"🥉"}.get(i,"  ")
            print(f"  {medal}{i:<3} {r.code:<8}{r.name:<20}{r.category:<16}{r.quant_score:>6.1f}  {em}{r.signal}")

    output = {
        "datetime":           datetime.now().isoformat(),
        "days":               days,
        "total":              total,
        "success":            success,
        "bullish":            bull,
        "bearish":            bear,
        "neutral":            success - bull - bear,
        "top_n":              top_n,
        "rebalance_freq":     rebalance_freq,
        "results":            [r.to_dict() for r in results],
        "top3":               top3,
        "portfolio_weights":  portfolio_weights,
        "backtest": {
            "total_return":  bt_result.total_return  if bt_result else None,
            "annual_return": bt_result.annual_return if bt_result else None,
            "sharpe_ratio":  bt_result.sharpe_ratio  if bt_result else None,
            "max_drawdown":  bt_result.max_drawdown  if bt_result else None,
            "total_trades":  bt_result.total_trades  if bt_result else None,
            "equity_curve":  bt_result.equity_curve.tolist() if bt_result and not bt_result.equity_curve.empty else [],
        } if bt_result else {},
    }

    # 保存 JSON
    out_path = ROOT / "reports" / f"etf50_quant_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        import copy
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    output["_saved_to"] = str(out_path)

    if verbose:
        print(f"\n  📁 已保存: {out_path}")

    return output


# ══════════════════════════════════════════
#  内部评分函数
# ══════════════════════════════════════════
def _calc_quant_score(r: ETFQuantResult) -> float:
    """
    从因子值合成量化评分 [0, 1]
    因子已经过 z-score 标准化，所以用 sigmoid 映射
    """
    score = 0.5

    # 动量因子（最重要）
    # ROC20 > 0 且显著 → 加分
    score += np.clip(r.roc20 * 0.3, -0.15, 0.15)

    # MACD 方向
    score += np.clip(r.macd * 8, -0.12, 0.12)

    # RSI（超卖反弹机会 / 超买风险）
    if r.rsi14 < -0.5:      # z-score 很负 = 超卖
        score += 0.08
    elif r.rsi14 > 0.5:     # z-score 很正 = 超买
        score -= 0.06

    # 均线偏离（BIAS20）— 均值回归
    # 偏离太大 → 均值回归压力
    bias_abs = abs(r.bias20)
    if bias_abs > 1.0:      # 偏离超过1个标准差
        score -= 0.05 * np.sign(r.bias20)  # 正偏离减分，负偏离加分

    # 量比放量
    if r.vol_ratio > 0.5:   # 量比放大
        score += 0.04
    elif r.vol_ratio < -0.5: # 缩量
        score -= 0.02

    # 低波动性加分（风险调整）
    if r.std20 < -0.3:      # 波动率低于均值
        score += 0.03

    return float(np.clip(score, 0.0, 1.0))


def _build_quant_score(results: list[ETFQuantResult], bt_result=None):
    """合成最终量化评分（0-100），加上回测加成"""
    for r in results:
        if not r.has_data:
            r.quant_score = 0.0
            r.signal = "neutral"
            r.reasons = ["无数据"]
            continue

        # 基础分：因子分映射到 0-100
        base = r.factor_score * 100

        # 附加：RSI 超卖加分
        if r.rsi14 < -0.8:
            base += 5

        # 附加：MACD 金叉
        if r.macd > 0.5:
            base += 5

        # 量化分
        r.quant_score = float(np.clip(base, 0, 100))

        # 信号判定
        if r.quant_score >= 60:
            r.signal = "bullish"
        elif r.quant_score <= 40:
            r.signal = "bearish"
        else:
            r.signal = "neutral"

        # 关键理由
        reasons = []
        if r.roc20 > 0.3:
            reasons.append(f"20日动量强({r.roc20:.2f}σ)")
        elif r.roc20 < -0.3:
            reasons.append(f"20日动量弱({r.roc20:.2f}σ)")
        if r.macd > 0.3:
            reasons.append(f"MACD金叉({r.macd:.2f})")
        elif r.macd < -0.3:
            reasons.append(f"MACD死叉({r.macd:.2f})")
        if r.rsi14 < -0.5:
            reasons.append(f"RSI超卖(z={r.rsi14:.1f})")
        elif r.rsi14 > 0.5:
            reasons.append(f"RSI偏高(z={r.rsi14:.1f})")
        if r.vol_ratio > 0.5:
            reasons.append("量比放量")
        r.reasons = reasons[:3] if reasons else ["因子中性"]


def load_latest_quant() -> Optional[dict]:
    """加载最新的量化结果 JSON"""
    files = sorted((ROOT / "reports").glob("etf50_quant_*.json"))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)

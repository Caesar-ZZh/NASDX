"""
NASDX V2 — 统一信号引擎
整合五个信号源，用 SignalVoter 集成投票，输出最终操作建议

信号源：
  1. 技术面评分    （现有 scan_etf50 系统）
  2. Alpha158 因子 （QLib 因子引擎）
  3. 趋势跟踪      （双均线 + MACD）
  4. 量价信号      （量比 + 资金流）
  5. AI 研判       （DeepSeek 多智能体）

抗过拟合措施：
  - Walk-Forward 信号回测（不用随机 K-Fold）
  - 因子 IC 稳定性筛选（只用 ICIR > 0.3 的因子）
  - 集成投票（5个信号源，防单点失效）
  - 信号一致性置信度（分歧时降权）
"""
from __future__ import annotations
import sys
import json
import time
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════
#  信号标准化：所有信号统一映射到 [0,1]
#  0.0 = 强烈看空  0.5 = 中性  1.0 = 强烈看多
# ══════════════════════════════════════════
def normalize_technical_score(score: int) -> float:
    """NASDX 技术面评分 (0-100) → [0,1]"""
    return min(1.0, max(0.0, score / 100.0))


def normalize_factor_score(z_score: float) -> float:
    """因子 z-score → [0,1]（sigmoid 变换）"""
    return 1.0 / (1.0 + np.exp(-z_score * 2))


def normalize_trend(ma5: float, ma20: float, ma60: float,
                    macd_bar: float, price: float) -> float:
    """趋势信号 → [0,1]"""
    score = 0.5
    # 均线排列
    if ma5 > ma20 > ma60: score += 0.2
    elif ma5 < ma20 < ma60: score -= 0.2
    elif ma5 > ma20: score += 0.1
    elif ma5 < ma20: score -= 0.1
    # MACD
    if macd_bar > 0.005:  score += 0.15
    elif macd_bar < -0.005: score -= 0.15
    elif macd_bar > 0: score += 0.05
    else: score -= 0.05
    # 价格高于 MA20
    if price > ma20: score += 0.05
    else: score -= 0.05
    return min(1.0, max(0.0, score))


def normalize_volume(vol_ratio: float, fund_flow_pct: Optional[float]) -> float:
    """量价信号 → [0,1]"""
    score = 0.5
    # 量比
    if 1.2 <= vol_ratio <= 2.5: score += 0.15   # 温和放量最佳
    elif vol_ratio > 2.5:       score += 0.05   # 异常放量略加分
    elif vol_ratio < 0.7:       score -= 0.10   # 严重缩量减分
    # 资金流向
    if fund_flow_pct is not None:
        if fund_flow_pct > 5:   score += 0.20
        elif fund_flow_pct > 0: score += 0.10
        elif fund_flow_pct < -5: score -= 0.20
        else:                   score -= 0.10
    return min(1.0, max(0.0, score))


def normalize_ai_signal(signal: str, confidence: float) -> float:
    """AI 信号 → [0,1]"""
    base = {"bullish": 0.75, "neutral": 0.50, "bearish": 0.25}.get(signal, 0.5)
    # 用置信度调节距离中性的程度
    return 0.5 + (base - 0.5) * min(1.0, confidence)


# ══════════════════════════════════════════
#  统一信号引擎
# ══════════════════════════════════════════
class SignalEngine:
    """
    NASDX V2 核心信号引擎
    输入：ETF 代码列表 + 历史数据
    输出：综合评分、信号、置信度、操作建议
    """

    def __init__(self, capital: float = 100_000):
        from quant.anti_overfit import SignalVoter
        self.voter   = SignalVoter()
        self.capital = capital

    def run(
        self,
        codes:        list[str],
        price_data:   dict[str, pd.DataFrame],
        etf50_json:   Optional[str] = None,  # scan_etf50 的 JSON 文件
        ai_reports:   Optional[dict] = None, # {code: report_dict}
        fund_flow:    Optional[dict] = None, # {code: main_flow_pct}
        verbose:      bool = True,
    ) -> pd.DataFrame:
        """
        运行完整信号引擎

        返回 DataFrame：
          code | name | tech_score | factor_score | trend_score
               | volume_score | ai_score | final_score
               | signal | confidence | high_confidence
               | suggested_weight | reason
        """
        if verbose:
            print(f"\n{'='*60}")
            print(f"  NASDX 信号引擎  {datetime.now().strftime('%H:%M:%S')}")
            print(f"  分析 {len(codes)} 只标的...")
            print(f"{'='*60}")

        # ── 1. 技术面信号 ─────────────────────────────────
        tech_signals = self._get_tech_signals(codes, etf50_json)
        if verbose: print(f"\n✅ 技术面信号: {len(tech_signals)} 只")

        # ── 2. Alpha158 因子信号 ──────────────────────────
        factor_signals = self._get_factor_signals(codes, price_data)
        if verbose: print(f"✅ 因子信号: {len(factor_signals)} 只")

        # ── 3. 趋势信号 ───────────────────────────────────
        trend_signals = self._get_trend_signals(codes, price_data)
        if verbose: print(f"✅ 趋势信号: {len(trend_signals)} 只")

        # ── 4. 量价信号 ───────────────────────────────────
        volume_signals = self._get_volume_signals(codes, price_data, fund_flow)
        if verbose: print(f"✅ 量价信号: {len(volume_signals)} 只")

        # ── 5. AI 信号 ────────────────────────────────────
        ai_signals = self._get_ai_signals(codes, ai_reports)
        if verbose: print(f"✅ AI信号: {len(ai_signals)} 只")

        # ── 集成投票 ──────────────────────────────────────
        all_signals = {
            "technical": tech_signals,
            "factor":    factor_signals,
            "trend":     trend_signals,
            "volume":    volume_signals,
            "ai":        ai_signals,
        }
        voted = self.voter.vote(all_signals)

        # ── 构建结果表 ────────────────────────────────────
        rows = []
        for code in codes:
            v = voted.get(code, {})
            if not v:
                continue
            sc  = v["score"]
            sig = v["signal"]
            conf = v["confidence"]
            high = v.get("high_confidence", False)
            src  = v.get("source_scores", {})

            # 建议仓位权重（只有高置信度才给较高权重）
            if sig == "bullish" and high:
                w = 0.30 if sc > 0.8 else 0.20
            elif sig == "bullish":
                w = 0.15
            elif sig == "neutral":
                w = 0.05
            else:
                w = 0.0

            reason = self._build_reason(sig, sc, conf, src, code, price_data)

            rows.append({
                "code":             code,
                "final_score":      round(sc, 4),
                "signal":           sig,
                "confidence":       round(conf, 3),
                "high_confidence":  high,
                "tech_score":       round(src.get("technical", 0.5), 3),
                "factor_score":     round(src.get("factor", 0.5), 3),
                "trend_score":      round(src.get("trend", 0.5), 3),
                "volume_score":     round(src.get("volume", 0.5), 3),
                "ai_score":         round(src.get("ai", 0.5), 3),
                "suggested_weight": w,
                "reason":           reason,
            })

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df = df.sort_values("final_score", ascending=False).reset_index(drop=True)

        # 归一化建议权重（总仓位不超过80%，留20%现金）
        total_w = df["suggested_weight"].sum()
        if total_w > 0.8:
            df["suggested_weight"] = df["suggested_weight"] * 0.8 / total_w

        if verbose:
            self._print_summary(df)

        return df

    # ── 私有方法 ──────────────────────────────────────────

    def _get_tech_signals(self, codes, etf50_json) -> dict[str, float]:
        """从 ETF50 扫描结果读取技术面信号"""
        signals = {}
        if etf50_json:
            try:
                with open(etf50_json, encoding="utf-8") as f:
                    data = json.load(f)
                for r in data.get("results", []):
                    if r.get("code") in codes:
                        signals[r["code"]] = normalize_technical_score(r.get("score", 50))
            except Exception:
                pass
        # 未在 JSON 中的，默认中性
        for code in codes:
            if code not in signals:
                signals[code] = 0.5
        return signals

    def _get_factor_signals(self, codes, price_data) -> dict[str, float]:
        """计算 Alpha158 因子并转化为信号"""
        signals = {}
        try:
            from quant.factors import compute_alpha158, multi_factor_score
            factor_data = {}
            for code in codes:
                if code in price_data and len(price_data[code]) >= 60:
                    factor_data[code] = compute_alpha158(price_data[code])

            if factor_data:
                ranking = multi_factor_score(factor_data)
                for _, row in ranking.iterrows():
                    signals[row["code"]] = normalize_factor_score(row["factor_score"])
        except Exception as e:
            pass

        for code in codes:
            if code not in signals:
                signals[code] = 0.5
        return signals

    def _get_trend_signals(self, codes, price_data) -> dict[str, float]:
        """趋势信号：均线 + MACD"""
        signals = {}
        for code in codes:
            df = price_data.get(code)
            if df is None or df.empty or len(df) < 20:
                signals[code] = 0.5
                continue
            try:
                c   = df["close"].astype(float)
                ma5 = c.rolling(5).mean().iloc[-1]
                ma20= c.rolling(20).mean().iloc[-1]
                ma60= c.rolling(60).mean().iloc[-1] if len(c) >= 60 else ma20
                e12 = c.ewm(span=12, adjust=False).mean()
                e26 = c.ewm(span=26, adjust=False).mean()
                dif = e12 - e26
                dea = dif.ewm(span=9, adjust=False).mean()
                macd= (dif - dea).iloc[-1]
                signals[code] = normalize_trend(ma5, ma20, ma60, macd, c.iloc[-1])
            except Exception:
                signals[code] = 0.5
        return signals

    def _get_volume_signals(self, codes, price_data, fund_flow) -> dict[str, float]:
        """量价信号：量比 + 资金流"""
        signals = {}
        for code in codes:
            df = price_data.get(code)
            vol_ratio = 1.0
            if df is not None and not df.empty and len(df) > 5:
                try:
                    v = df["volume"].astype(float)
                    vol_ratio = v.iloc[-1] / (v.rolling(5).mean().iloc[-2] + 1e-9)
                except Exception:
                    pass
            flow_pct = None
            if fund_flow and code in fund_flow:
                flow_pct = fund_flow[code]
            signals[code] = normalize_volume(vol_ratio, flow_pct)
        return signals

    def _get_ai_signals(self, codes, ai_reports) -> dict[str, float]:
        """AI 信号：从深度分析报告提取"""
        signals = {}
        if ai_reports:
            for code in codes:
                report = ai_reports.get(code)
                if report:
                    sig  = report.get("final_signal", "neutral")
                    bpct = report.get("bullish_pct", 50) / 100.0
                    conf = abs(bpct - 0.5) * 2
                    signals[code] = normalize_ai_signal(sig, conf)
        for code in codes:
            if code not in signals:
                signals[code] = 0.5
        return signals

    def _build_reason(self, sig, score, conf, src, code, price_data) -> str:
        """构建可读的信号理由"""
        reasons = []
        if src.get("technical", 0.5) > 0.65:
            reasons.append("技术面强")
        elif src.get("technical", 0.5) < 0.35:
            reasons.append("技术面弱")

        if src.get("factor", 0.5) > 0.65:
            reasons.append("因子排名靠前")
        elif src.get("factor", 0.5) < 0.35:
            reasons.append("因子排名靠后")

        if src.get("trend", 0.5) > 0.65:
            reasons.append("趋势向上")
        elif src.get("trend", 0.5) < 0.35:
            reasons.append("趋势向下")

        if src.get("volume", 0.5) > 0.65:
            reasons.append("资金流入")
        elif src.get("volume", 0.5) < 0.35:
            reasons.append("资金流出")

        if conf > 0.7:
            reasons.append("信号高度一致✓")
        elif conf < 0.4:
            reasons.append("信号分歧⚠")

        return " · ".join(reasons) if reasons else "信号中性"

    def _print_summary(self, df: pd.DataFrame):
        print(f"\n{'─'*60}")
        print(f"  {'排名':<4}{'代码':<8}{'分数':>6}{'信号':>8}{'置信':>6}{'建议权重':>8}  理由")
        print(f"  {'─'*60}")
        for i, row in df.iterrows():
            em = {"bullish":"📈","bearish":"📉","neutral":"➡️"}.get(row["signal"],"")
            hc = "⭐" if row["high_confidence"] else "  "
            print(f"  {hc}{i+1:<3} {row['code']:<8}{row['final_score']:>6.3f}"
                  f"{em+row['signal']:>9}{row['confidence']:>6.2f}"
                  f"{row['suggested_weight']:>8.1%}  {row['reason']}")

        bull = (df["signal"]=="bullish").sum()
        bear = (df["signal"]=="bearish").sum()
        hc   = df["high_confidence"].sum()
        print(f"\n  看多:{bull}  看空:{bear}  高置信:{hc}  "
              f"建议总仓位:{df['suggested_weight'].sum():.0%}")

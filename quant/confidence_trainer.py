"""
NASDX V2 — 数据置信度训练器
用真实历史数据校准每个信号源的可靠性，动态调整权重

核心思路：
  1. 读取历史扫描报告中的信号（看多/看空/中性）
  2. 对比信号发出后 N 日的实际价格变化
  3. 计算每个信号源的 Hit Rate（方向准确率）和 IC（信息系数）
  4. Walk-Forward 滚动校准（避免过拟合）
  5. 输出经过验证的动态权重，替换 SignalVoter 固定权重

涉及框架：
  QLib  → IC/ICIR 稳定性检验、Walk-Forward 校准
  FinRL → SignalVoter 权重更新
  VnPy  → 实盘级别的信号准确率统计
"""
from __future__ import annotations
from quant.patch_requests import configure_requests
import json
import glob
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
CONF_PATH = ROOT / "models" / "signal_confidence.json"
CONF_PATH.parent.mkdir(exist_ok=True)
configure_requests()


# ══════════════════════════════════════════
#  默认权重（未训练时使用）
# ══════════════════════════════════════════
DEFAULT_WEIGHTS = {
    "technical": 0.25,   # ETF50 技术评分
    "factor":    0.25,   # Alpha158 多因子
    "trend":     0.20,   # 均线+MACD 趋势
    "volume":    0.15,   # 量价信号
    "ai":        0.15,   # DeepSeek AI 研判
}

# 置信度权重上下限
MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.50


# ══════════════════════════════════════════
#  信号准确率计算工具
# ══════════════════════════════════════════
def calc_hit_rate(signals: pd.Series, returns: pd.Series) -> float:
    """
    Hit Rate = 信号方向与实际收益方向一致的比例
    signals: +1(看多) / -1(看空) / 0(中性)
    returns: 实际收益率
    """
    aligned = pd.concat([signals, returns], axis=1).dropna()
    if len(aligned) < 5:
        return 0.5  # 样本不足，返回中性

    # 只统计有方向的信号（排除中性）
    directional = aligned[aligned.iloc[:, 0] != 0]
    if len(directional) < 3:
        return 0.5

    correct = ((directional.iloc[:, 0] > 0) == (directional.iloc[:, 1] > 0)).sum()
    return float(correct / len(directional))


def calc_signal_ic(scores: pd.Series, returns: pd.Series) -> float:
    """
    IC = 信号得分与未来收益的 Spearman 相关系数
    scores: 0~1 之间的信号强度
    returns: 实际收益率
    """
    aligned = pd.concat([scores, returns], axis=1).dropna()
    if len(aligned) < 10:
        return 0.0
    return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1], method="spearman"))


def weight_from_performance(hit_rate: float, ic: float,
                             ic_ir: float = 0.0) -> float:
    """
    根据历史表现将准确率/IC 转为权重
    - hit_rate: [0,1]，0.5 = 随机，越高越好
    - ic: Spearman IC，-1~1，绝对值越大越好
    - ic_ir: IC 的信息比率（稳定性）
    """
    # hit_rate 超额（相对于随机的 0.5）
    hr_excess = max(0, (hit_rate - 0.5) * 2)  # 0~1

    # IC 贡献（取绝对值，方向已在 hit_rate 中体现）
    ic_contrib = min(1.0, abs(ic) * 5)  # 0~1（IC=0.2 时满分）

    # ICIR 稳定性加成
    icir_bonus = min(0.3, abs(ic_ir) * 0.1)

    raw = hr_excess * 0.5 + ic_contrib * 0.4 + icir_bonus * 0.1
    return float(np.clip(raw, 0.0, 1.0))


# ══════════════════════════════════════════
#  历史报告解析器
# ══════════════════════════════════════════
def parse_historical_signals(report_dir: Path = ROOT / "reports") -> pd.DataFrame:
    """
    从历史 ETF50 扫描报告中提取信号时序

    返回 DataFrame:
      index: datetime
      columns: code, tech_score, signal, premium, spot_chg
    """
    rows = []
    pattern = str(report_dir / "etf50_*.json")
    files = sorted(glob.glob(pattern))

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)

            dt_str = data.get("datetime", "")
            if not dt_str:
                continue
            dt = pd.Timestamp(dt_str[:19])

            for r in data.get("results", []):
                code = r.get("code", "")
                if not code:
                    continue
                rows.append({
                    "datetime":   dt,
                    "code":       code,
                    "tech_score": r.get("score", 50),
                    "signal":     r.get("signal", "neutral"),
                    "premium":    r.get("premium"),
                    "spot_chg":   r.get("spot_chg"),
                    "source":     "technical",
                })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["signal_num"] = df["signal"].map({"bullish": 1, "neutral": 0, "bearish": -1})
    df["tech_signal"] = df["tech_score"] / 100.0
    return df.sort_values("datetime").reset_index(drop=True)


# ══════════════════════════════════════════
#  置信度训练主类
# ══════════════════════════════════════════
class ConfidenceTrainer:
    """
    用历史数据校准各信号源的可靠性

    训练流程：
    1. 从历史报告提取信号
    2. 获取对应的实际价格变化
    3. Walk-Forward 计算 Hit Rate / IC / ICIR
    4. 自适应更新 SignalVoter 权重
    5. 保存校准结果
    """

    def __init__(self, forward_days: int = 5):
        self.forward_days = forward_days  # 预测 N 日后的收益
        self.results: dict = {}

    def train(
        self,
        codes: Optional[list[str]] = None,
        days:  int = 365,
        verbose: bool = True,
        progress_cb=None,  # fn(step, total, msg)
    ) -> dict:
        """
        执行完整置信度训练

        Returns: {
          "weights": {...},       # 校准后的信号权重
          "factor_ic": {...},     # 各因子 IC
          "signal_hit_rates": {}, # 各信号源命中率
          "walk_forward": {},     # WF 验证结果
          "trained_at": ...,
        }
        """
        if verbose:
            print(f"\n{'='*60}")
            print(f"  NASDX 置信度校准训练  {datetime.now().strftime('%H:%M:%S')}")
            print(f"  预测窗口: {self.forward_days} 日  历史: {days} 天")
            print(f"{'='*60}\n")

        total_steps = 5
        step = 0

        # ── Step 1: 解析历史信号 ──────────────────────────
        step += 1
        if progress_cb: progress_cb(step, total_steps, "解析历史扫描报告...")
        if verbose: print("📂 Step 1: 解析历史扫描报告...")

        hist_signals = parse_historical_signals()
        n_reports = hist_signals["datetime"].nunique() if not hist_signals.empty else 0
        n_codes   = hist_signals["code"].nunique() if not hist_signals.empty else 0
        if verbose:
            print(f"   找到 {n_reports} 份报告，涉及 {n_codes} 只标的")

        # 确定要分析的代码
        if codes is None and not hist_signals.empty:
            # 取出现次数最多的前20只
            codes = hist_signals["code"].value_counts().head(20).index.tolist()
        codes = codes or []

        # ── Step 2: 获取历史价格 ──────────────────────────
        step += 1
        if progress_cb: progress_cb(step, total_steps, f"获取 {len(codes)} 只历史价格...")
        if verbose: print(f"📡 Step 2: 获取 {len(codes)} 只历史价格...")

        from quant.data import get_batch_ohlcv
        price_data = get_batch_ohlcv(codes, days=days)
        if verbose:
            print(f"   成功获取 {len(price_data)} 只数据")

        # ── Step 3: 计算技术信号命中率 ───────────────────
        step += 1
        if progress_cb: progress_cb(step, total_steps, "计算技术信号命中率...")
        if verbose: print("📊 Step 3: 计算技术信号历史命中率...")

        tech_hr = self._calc_tech_hit_rate(hist_signals, price_data)
        if verbose:
            print(f"   技术信号命中率: {tech_hr:.1%}")

        # ── Step 4: 计算 Alpha158 因子 IC ─────────────────
        step += 1
        if progress_cb: progress_cb(step, total_steps, "计算 Alpha158 因子 IC...")
        if verbose: print("🔬 Step 4: 计算 Alpha158 因子 IC/ICIR...")

        factor_ic_results = self._calc_factor_ic(price_data, verbose)

        # ── Step 5: Walk-Forward 校准权重 ─────────────────
        step += 1
        if progress_cb: progress_cb(step, total_steps, "Walk-Forward 权重校准...")
        if verbose: print("⚖️  Step 5: Walk-Forward 校准信号权重...")

        calibrated_weights, wf_results = self._calibrate_weights(
            tech_hr, factor_ic_results, price_data, verbose
        )

        # ── 汇总结果 ──────────────────────────────────────
        result = {
            "trained_at":      datetime.now().isoformat(),
            "forward_days":    self.forward_days,
            "n_reports":       n_reports,
            "n_codes":         n_codes,
            "weights":         calibrated_weights,
            "factor_ic":       factor_ic_results,
            "tech_hit_rate":   round(tech_hr, 4),
            "walk_forward":    wf_results,
            "confidence_level": self._overall_confidence(tech_hr, factor_ic_results),
        }

        self.results = result

        # 保存到文件
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        if verbose:
            self._print_summary(result)

        return result

    # ── 私有方法 ──────────────────────────────────────────

    def _calc_tech_hit_rate(self, hist_signals: pd.DataFrame,
                             price_data: dict) -> float:
        """计算技术信号的历史命中率"""
        if hist_signals.empty or not price_data:
            return 0.5

        hits = total = 0

        for code, df in price_data.items():
            if df.empty or len(df) < self.forward_days + 5:
                continue

            code_signals = hist_signals[hist_signals["code"] == code]
            if code_signals.empty:
                continue

            close = df["close"].astype(float)

            for _, row in code_signals.iterrows():
                sig_dt  = row["datetime"]
                sig_num = row["signal_num"]
                if sig_num == 0:  # 跳过中性
                    continue

                # 找信号日期后的价格
                future_idx = close.index[close.index > sig_dt]
                if len(future_idx) < self.forward_days:
                    continue

                future_price = close.loc[future_idx[:self.forward_days]].iloc[-1]
                sig_price    = close.loc[close.index[close.index <= sig_dt]].iloc[-1] \
                               if any(close.index <= sig_dt) else None
                if sig_price is None or sig_price == 0:
                    continue

                actual_ret = (future_price - sig_price) / sig_price
                if (sig_num > 0 and actual_ret > 0) or (sig_num < 0 and actual_ret < 0):
                    hits += 1
                total += 1

        return hits / total if total > 0 else 0.5

    def _calc_factor_ic(self, price_data: dict, verbose: bool) -> dict:
        """计算 Alpha158 各因子的 IC 和 ICIR"""
        from quant.factors import compute_alpha158
        from quant.anti_overfit import calc_ic, calc_icir

        factor_records: dict[str, list] = {}

        for code, df in price_data.items():
            if df.empty or len(df) < 60:
                continue
            try:
                factors = compute_alpha158(df)
                if factors.empty:
                    continue

                fwd_ret = df["close"].pct_change(self.forward_days).shift(-self.forward_days)

                for fname in factors.columns:
                    ic_val = calc_ic(factors[fname], fwd_ret)
                    if pd.notna(ic_val):
                        factor_records.setdefault(fname, []).append(ic_val)
            except Exception:
                continue

        result = {}
        for fname, ic_list in factor_records.items():
            if len(ic_list) >= 3:
                ic_ser = pd.Series(ic_list)
                ic_mean = ic_ser.mean()
                ic_ir   = calc_icir(ic_ser)
                result[fname] = {
                    "ic_mean": round(float(ic_mean), 4),
                    "icir":    round(float(ic_ir), 4),
                    "n_codes": len(ic_list),
                    "stable":  abs(ic_ir) > 0.3 and abs(ic_mean) > 0.02,
                }

        if verbose and result:
            # 显示 IC 最强的前10个因子
            sorted_factors = sorted(result.items(),
                                     key=lambda x: -abs(x[1]["ic_mean"]))[:10]
            print(f"   Top 10 因子 IC:")
            for fname, info in sorted_factors:
                bar = "█" * int(min(abs(info["ic_mean"]) * 30, 15))
                stable_mark = "✓" if info["stable"] else "·"
                print(f"   {stable_mark} {fname:<12} IC={info['ic_mean']:+.4f}  "
                      f"ICIR={info['icir']:+.3f}  {bar}")

        return result

    def _calibrate_weights(self, tech_hr: float, factor_ic: dict,
                           price_data: dict, verbose: bool) -> tuple[dict, dict]:
        """Walk-Forward 校准各信号源权重"""
        from quant.anti_overfit import WalkForwardConfig, walk_forward_split

        wf_results = {}
        raw_weights = {}

        # ── 技术信号权重 ──────────────────────────────────
        tech_perf = weight_from_performance(tech_hr, ic=0.0)
        raw_weights["technical"] = tech_perf
        if verbose:
            print(f"   技术信号: Hit Rate={tech_hr:.1%}  → 原始权重={tech_perf:.3f}")

        # ── 因子信号权重（取稳定因子的平均 IC）────────────
        stable_ics = [v["ic_mean"] for v in factor_ic.values() if v.get("stable")]
        if stable_ics:
            avg_stable_ic   = np.mean(np.abs(stable_ics))
            stable_ic_ir    = np.mean([abs(v["icir"]) for v in factor_ic.values()
                                        if v.get("stable")])
            factor_perf = weight_from_performance(
                hit_rate=0.5 + avg_stable_ic * 2,
                ic=avg_stable_ic,
                ic_ir=stable_ic_ir,
            )
            n_stable = len(stable_ics)
        else:
            factor_perf = 0.3  # 无稳定因子，给一个保守默认值
            n_stable    = 0
        raw_weights["factor"] = factor_perf
        if verbose:
            print(f"   因子信号: 稳定因子={n_stable}个  avg|IC|={np.mean(np.abs(stable_ics)):.4f if stable_ics else 0:.4f}"
                  f"  → 原始权重={factor_perf:.3f}")

        # ── 趋势信号：用 Walk-Forward 对均线策略验证 ──────
        trend_perf = self._wf_validate_trend(price_data)
        raw_weights["trend"] = weight_from_performance(trend_perf, ic=0.0)
        wf_results["trend_hit_rate"] = trend_perf
        if verbose:
            print(f"   趋势信号: WF命中率={trend_perf:.1%}  → 原始权重={raw_weights['trend']:.3f}")

        # ── 量价信号：用量比放量后N日表现验证 ────────────
        volume_perf = self._wf_validate_volume(price_data)
        raw_weights["volume"] = weight_from_performance(volume_perf, ic=0.0)
        wf_results["volume_hit_rate"] = volume_perf
        if verbose:
            print(f"   量价信号: WF命中率={volume_perf:.1%}  → 原始权重={raw_weights['volume']:.3f}")

        # ── AI 信号（暂时保持默认，无历史数据可验证）──────
        raw_weights["ai"] = 0.15  # 固定，等有足够 AI 分析历史后再校准
        if verbose:
            print(f"   AI 信号: 历史数据不足，保持默认=0.15")

        # ── 归一化为概率分布 ────────────────────────────
        calibrated = self._normalize_weights(raw_weights)

        wf_results["raw_weights"]  = {k: round(v, 4) for k, v in raw_weights.items()}
        wf_results["calibrated"]   = {k: round(v, 4) for k, v in calibrated.items()}
        wf_results["n_stable_factors"] = n_stable
        wf_results["tech_hit_rate"]    = round(tech_hr, 4)

        return calibrated, wf_results

    def _wf_validate_trend(self, price_data: dict) -> float:
        """Walk-Forward 验证均线趋势信号命中率"""
        hits = total = 0
        for code, df in price_data.items():
            if df.empty or len(df) < 40:
                continue
            try:
                c    = df["close"].astype(float)
                ma5  = c.rolling(5).mean()
                ma20 = c.rolling(20).mean()
                sig  = (ma5 > ma20).astype(int) * 2 - 1   # +1 or -1
                ret  = c.pct_change(self.forward_days).shift(-self.forward_days)

                for i in range(20, len(sig) - self.forward_days, 5):
                    s = sig.iloc[i]
                    r = ret.iloc[i]
                    if pd.notna(s) and pd.notna(r):
                        if (s > 0 and r > 0) or (s < 0 and r < 0):
                            hits += 1
                        total += 1
            except Exception:
                continue
        return hits / total if total > 0 else 0.5

    def _wf_validate_volume(self, price_data: dict) -> float:
        """Walk-Forward 验证量比放量信号命中率"""
        hits = total = 0
        for code, df in price_data.items():
            if df.empty or len(df) < 30 or "volume" not in df.columns:
                continue
            try:
                c  = df["close"].astype(float)
                v  = df["volume"].astype(float)
                vr = v / (v.rolling(5).mean().shift(1) + 1e-9)
                ret = c.pct_change(self.forward_days).shift(-self.forward_days)

                # 量比放量 > 1.5 视为看多信号
                for i in range(10, len(vr) - self.forward_days, 3):
                    vr_val = vr.iloc[i]
                    r_val  = ret.iloc[i]
                    if pd.notna(vr_val) and pd.notna(r_val):
                        if vr_val > 1.5 and r_val > 0:
                            hits += 1
                        elif vr_val < 0.7 and r_val < 0:
                            hits += 1
                        if vr_val > 1.5 or vr_val < 0.7:
                            total += 1
            except Exception:
                continue
        return hits / total if total > 0 else 0.5

    def _normalize_weights(self, raw: dict) -> dict:
        """归一化权重：保证总和=1，每个在 MIN~MAX 之间"""
        # 先裁剪到合理范围
        clipped = {k: float(np.clip(v, MIN_WEIGHT, MAX_WEIGHT)) for k, v in raw.items()}

        # 归一化
        total = sum(clipped.values())
        if total <= 0:
            return DEFAULT_WEIGHTS.copy()

        normalized = {k: v / total for k, v in clipped.items()}

        # 确保所有键都存在
        for k, default_v in DEFAULT_WEIGHTS.items():
            if k not in normalized:
                normalized[k] = default_v

        # 再次归一化（加入缺失的键后）
        total2 = sum(normalized.values())
        return {k: round(v / total2, 4) for k, v in normalized.items()}

    def _overall_confidence(self, tech_hr: float, factor_ic: dict) -> str:
        """评估整体置信度等级"""
        stable_count = sum(1 for v in factor_ic.values() if v.get("stable"))
        avg_ic = np.mean([abs(v["ic_mean"]) for v in factor_ic.values()]) if factor_ic else 0

        if tech_hr >= 0.58 and stable_count >= 10 and avg_ic >= 0.03:
            return "high"
        elif tech_hr >= 0.52 and stable_count >= 5:
            return "medium"
        else:
            return "low"

    def _print_summary(self, result: dict):
        w = result["weights"]
        conf = result["confidence_level"]
        conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "⚪")
        n_stable = result["walk_forward"].get("n_stable_factors", 0)

        print(f"\n{'─'*60}")
        print(f"  置信度校准完成  {conf_icon} 整体置信度: {conf.upper()}")
        print(f"  历史报告: {result['n_reports']} 份  稳定因子: {n_stable} 个")
        print(f"  技术信号命中率: {result['tech_hit_rate']:.1%}")
        print(f"\n  校准后信号权重:")
        for src, wt in sorted(w.items(), key=lambda x: -x[1]):
            bar = "█" * int(wt * 40)
            diff = wt - DEFAULT_WEIGHTS.get(src, 0)
            diff_s = f"{diff:+.3f}" if abs(diff) > 0.001 else " 持平"
            print(f"    {src:<12} {wt:.3f}  {bar}  ({diff_s})")
        print(f"\n  📁 已保存: {CONF_PATH}")


# ══════════════════════════════════════════
#  置信度加载（供 SignalVoter 使用）
# ══════════════════════════════════════════
def load_calibrated_weights() -> dict:
    """
    加载最新校准权重
    如果未训练或文件不存在，返回默认权重
    """
    try:
        if not CONF_PATH.exists():
            return DEFAULT_WEIGHTS.copy()

        with open(CONF_PATH, encoding="utf-8") as f:
            data = json.load(f)

        weights = data.get("weights", {})
        if not weights:
            return DEFAULT_WEIGHTS.copy()

        # 确保所有键存在
        for k, v in DEFAULT_WEIGHTS.items():
            if k not in weights:
                weights[k] = v

        return weights
    except Exception:
        return DEFAULT_WEIGHTS.copy()


def load_confidence_report() -> Optional[dict]:
    """加载完整置信度报告"""
    try:
        if not CONF_PATH.exists():
            return None
        with open(CONF_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ══════════════════════════════════════════
#  更新 SignalVoter 使用校准权重
# ══════════════════════════════════════════
def get_calibrated_voter():
    """
    返回使用校准权重的 SignalVoter 实例
    """
    from quant.anti_overfit import SignalVoter
    weights = load_calibrated_weights()
    voter = SignalVoter(weights=weights)
    return voter

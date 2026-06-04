"""
NASDX V2 — 回测引擎
参考 VnPy BacktestingEngine 设计，纯 pandas 实现，无需安装 VnPy
支持：策略回测 / 绩效评估 / 参数优化
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, Callable


@dataclass
class Trade:
    """单笔交易记录"""
    date:       str
    code:       str
    direction:  str     # buy / sell
    price:      float
    shares:     int
    amount:     float   # 实际金额
    commission: float   # 手续费


@dataclass
class BacktestResult:
    """回测结果"""
    total_return:    float = 0.0
    annual_return:   float = 0.0
    max_drawdown:    float = 0.0
    sharpe_ratio:    float = 0.0
    calmar_ratio:    float = 0.0
    win_rate:        float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades:    int   = 0
    equity_curve:    pd.Series = field(default_factory=pd.Series)
    trades:          list[Trade] = field(default_factory=list)
    daily_pnl:       pd.Series = field(default_factory=pd.Series)

    def summary(self) -> str:
        return (
            f"总收益: {self.total_return:.2%}\n"
            f"年化收益: {self.annual_return:.2%}\n"
            f"最大回撤: {self.max_drawdown:.2%}\n"
            f"夏普比率: {self.sharpe_ratio:.3f}\n"
            f"卡玛比率: {self.calmar_ratio:.3f}\n"
            f"胜率: {self.win_rate:.2%}\n"
            f"盈亏比: {self.profit_loss_ratio:.2f}\n"
            f"总交易: {self.total_trades} 笔"
        )


class Backtester:
    """
    轻量级回测引擎
    参考 VnPy BacktestingEngine 设计
    """

    def __init__(
        self,
        initial_capital: float = 100_000,
        commission_rate: float = 0.0003,    # 万3手续费
        stamp_duty:      float = 0.001,     # 千1印花税（卖出）
        min_shares:      int   = 100,       # 最小交易单位
        slippage:        float = 0.001,     # 滑点（价格的0.1%）
    ):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.stamp_duty      = stamp_duty
        self.min_shares      = min_shares
        self.slippage        = slippage

    def run(
        self,
        price_data:  dict[str, pd.DataFrame],  # {code: OHLCV}
        signal_func: Callable,                  # 信号函数
        rebalance_freq: str = "W",              # W=周 M=月 D=日
    ) -> BacktestResult:
        """
        运行回测
        signal_func(date, price_data) → dict{code: weight}（权重合计≤1）
        """
        # 合并所有收盘价
        close_all = pd.DataFrame({
            code: df["close"]
            for code, df in price_data.items()
            if not df.empty
        })
        close_all = close_all.sort_index().dropna(how="all")

        if close_all.empty:
            return BacktestResult()

        # 确定再平衡日期
        dates = close_all.index
        if rebalance_freq == "W":
            rebal_dates = set(dates[dates.weekday == 0])  # 每周一
        elif rebalance_freq == "M":
            rebal_dates = set(dates[dates.is_month_start])
        else:
            rebal_dates = set(dates)

        # 初始化
        capital    = self.initial_capital
        holdings   = {}          # {code: shares}
        cost_basis = {}          # {code: avg_cost}
        equity     = []
        trades     = []
        pnl_list   = []

        prev_equity = capital

        for i, date in enumerate(dates):
            # 当日收盘价
            today_close = close_all.loc[date].to_dict()

            # 计算持仓市值
            market_value = sum(
                holdings.get(c, 0) * today_close.get(c, 0)
                for c in holdings
            )
            total_equity = capital + market_value

            # 再平衡
            if date in rebal_dates:
                # 调用信号函数
                past_data = {c: df[df.index <= date] for c, df in price_data.items()}
                target_weights = signal_func(date, past_data)

                if target_weights:
                    capital, holdings, cost_basis, day_trades = self._rebalance(
                        date, target_weights, today_close,
                        capital, holdings, cost_basis, total_equity
                    )
                    trades.extend(day_trades)

                    # 重新计算
                    market_value = sum(
                        holdings.get(c, 0) * today_close.get(c, 0)
                        for c in holdings
                    )
                    total_equity = capital + market_value

            daily_pnl = total_equity - prev_equity
            pnl_list.append({"date": date, "equity": total_equity, "pnl": daily_pnl})
            prev_equity = total_equity

        result_df  = pd.DataFrame(pnl_list).set_index("date")
        equity_ser = result_df["equity"]
        daily_pnl  = result_df["pnl"]

        return self._calc_metrics(equity_ser, daily_pnl, trades)

    def _rebalance(self, date, target_weights, prices, capital, holdings, cost_basis, total_equity):
        """执行再平衡"""
        new_trades = []

        # 先卖出不在目标中或需减仓的
        for code in list(holdings.keys()):
            target_w = target_weights.get(code, 0)
            target_val = total_equity * target_w
            cur_price = prices.get(code, 0)
            if cur_price <= 0:
                continue
            cur_val = holdings[code] * cur_price
            if cur_val > target_val * 1.05:  # 超过目标5%才调
                sell_val  = cur_val - target_val
                sell_sh   = min(int(sell_val / cur_price / self.min_shares) * self.min_shares,
                                holdings[code])
                if sell_sh >= self.min_shares:
                    exec_price = cur_price * (1 - self.slippage)
                    amount     = sell_sh * exec_price
                    commission = amount * self.commission_rate + amount * self.stamp_duty
                    capital   += amount - commission
                    holdings[code] -= sell_sh
                    if holdings[code] <= 0:
                        del holdings[code]
                        del cost_basis[code]
                    new_trades.append(Trade(str(date), code, "sell",
                                            exec_price, sell_sh, amount, commission))

        # 再买入目标仓位
        for code, w in sorted(target_weights.items(), key=lambda x: -x[1]):
            if w <= 0:
                continue
            cur_price = prices.get(code, 0)
            if cur_price <= 0:
                continue
            target_val = total_equity * w
            cur_val    = holdings.get(code, 0) * cur_price
            buy_val    = target_val - cur_val
            if buy_val < self.min_shares * cur_price:
                continue
            buy_sh = int(buy_val / cur_price / self.min_shares) * self.min_shares
            if buy_sh <= 0:
                continue
            exec_price = cur_price * (1 + self.slippage)
            amount     = buy_sh * exec_price
            commission = amount * self.commission_rate
            if amount + commission > capital:
                buy_sh = int(capital / (exec_price * (1 + self.commission_rate))
                             / self.min_shares) * self.min_shares
                if buy_sh <= 0:
                    continue
                exec_price = cur_price * (1 + self.slippage)
                amount     = buy_sh * exec_price
                commission = amount * self.commission_rate
            capital -= amount + commission
            prev_sh = holdings.get(code, 0)
            holdings[code] = prev_sh + buy_sh
            cost_basis[code] = (cost_basis.get(code, exec_price) * prev_sh +
                                 exec_price * buy_sh) / holdings[code]
            new_trades.append(Trade(str(date), code, "buy",
                                    exec_price, buy_sh, amount, commission))

        return capital, holdings, cost_basis, new_trades

    def _calc_metrics(self, equity: pd.Series, daily_pnl: pd.Series, trades: list) -> BacktestResult:
        """计算绩效指标"""
        if equity.empty:
            return BacktestResult()

        total_days   = len(equity)
        total_return = equity.iloc[-1] / equity.iloc[0] - 1
        annual_return = (1 + total_return) ** (252 / total_days) - 1

        # 最大回撤
        roll_max = equity.cummax()
        drawdown = (equity - roll_max) / roll_max
        max_dd   = drawdown.min()

        # 夏普比率（年化）
        daily_ret = equity.pct_change().dropna()
        sharpe = daily_ret.mean() / (daily_ret.std() + 1e-9) * np.sqrt(252)

        # 卡玛比率
        calmar = annual_return / (abs(max_dd) + 1e-9)

        # 胜率 / 盈亏比
        pnls = [t.amount * (1 if t.direction == "sell" else -1)
                - t.commission for t in trades]
        wins  = [p for p in pnls if p > 0]
        losses= [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls) if pnls else 0
        pl_ratio = (sum(wins) / len(wins) if wins else 0) / \
                   (abs(sum(losses)) / len(losses) if losses else 1e-9)

        return BacktestResult(
            total_return   = total_return,
            annual_return  = annual_return,
            max_drawdown   = max_dd,
            sharpe_ratio   = sharpe,
            calmar_ratio   = calmar,
            win_rate       = win_rate,
            profit_loss_ratio = pl_ratio,
            total_trades   = len(trades),
            equity_curve   = equity,
            trades         = trades,
            daily_pnl      = daily_pnl,
        )


# ══════════════════════════════════════════
#  预置策略信号函数
# ══════════════════════════════════════════
def strategy_momentum(date, price_data: dict, top_n: int = 3) -> dict:
    """
    动量策略：买入过去20日涨幅最高的 top_n 只
    等权配置
    """
    from quant.factors import compute_alpha158
    scores = {}
    for code, df in price_data.items():
        if len(df) < 25:
            continue
        ret = df["close"].pct_change(20).iloc[-1]
        if pd.notna(ret):
            scores[code] = ret

    if not scores:
        return {}

    top = sorted(scores, key=scores.get, reverse=True)[:top_n]
    return {c: 1.0 / top_n for c in top}


def strategy_mean_reversion(date, price_data: dict, top_n: int = 3) -> dict:
    """
    均值回归策略：买入过去5日跌幅最大的 top_n 只（反转）
    """
    scores = {}
    for code, df in price_data.items():
        if len(df) < 10:
            continue
        ret = df["close"].pct_change(5).iloc[-1]
        if pd.notna(ret):
            scores[code] = ret

    top = sorted(scores, key=scores.get)[:top_n]  # 跌得最多的
    return {c: 1.0 / top_n for c in top} if top else {}


def strategy_factor_rank(date, price_data: dict, top_n: int = 5) -> dict:
    """
    多因子排名策略：使用 Alpha158 因子合成评分
    """
    from quant.factors import compute_alpha158, multi_factor_score
    factor_data = {}
    for code, df in price_data.items():
        if len(df) >= 60:
            factor_data[code] = compute_alpha158(df)

    if not factor_data:
        return {}

    ranking = multi_factor_score(factor_data)
    if ranking.empty:
        return {}

    top = ranking.head(top_n)["code"].tolist()
    return {c: 1.0 / top_n for c in top}

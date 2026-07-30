"""
NASDX V2 — 回测引擎
参考 VnPy BacktestingEngine 设计，纯 pandas 实现，无需安装 VnPy
支持：策略回测 / 绩效评估 / 参数优化
"""
from __future__ import annotations
import numbers
import warnings

import numpy as np
import pandas as pd
from collections.abc import Mapping
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
    total_trades:    int   = 0      # 订单数（每笔 buy/sell 各计一次）
    completed_trades: int  = 0      # 闭环已实现盈亏笔数（仅卖出实现，issue #42）
    equity_curve:    pd.Series = field(default_factory=pd.Series)
    trades:          list[Trade] = field(default_factory=list)
    daily_pnl:       pd.Series = field(default_factory=pd.Series)
    # 诊断事件（issue #44/#45）：停牌估值 / 跳过交易 / stale 超限 / 权重归一化
    # 每条为 dict: {"date","code"?,"type",...}
    diagnostics:     list = field(default_factory=list)
    # 权重执行报告（issue #45）：每个调仓日记录策略请求权重 vs 实际成交后权重，
    # 暴露整手取整/手续费/现金约束造成的跟踪误差。
    # 每条为 dict: {"date","requested":{code:w},"executed":{code:w}}
    weight_allocations: list = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"总收益: {self.total_return:.2%}\n"
            f"年化收益: {self.annual_return:.2%}\n"
            f"最大回撤: {self.max_drawdown:.2%}\n"
            f"夏普比率: {self.sharpe_ratio:.3f}\n"
            f"卡玛比率: {self.calmar_ratio:.3f}\n"
            f"胜率: {self.win_rate:.2%}\n"
            f"盈亏比: {self.profit_loss_ratio:.2f}\n"
            f"总订单: {self.total_trades} 笔 / 闭环交易: {self.completed_trades} 笔"
        )


class Backtester:
    """
    轻量级回测引擎
    参考 VnPy BacktestingEngine 设计

    构造参数契约（issue #64，全部在 ``__init__`` 里 fail-fast 校验）：

    ==================  ====================================================
    参数                 合法取值
    ==================  ====================================================
    initial_capital     有限正数（issue #48）
    commission_rate     有限实数，0 <= x < 1（不允许负费率＝凭空返现）
    stamp_duty          有限实数，0 <= x < 1
    slippage            有限实数，0 <= x < 1（负滑点会造成负执行价＝凭空造钱；
                        >=1 会让卖出执行价 <= 0）
    min_shares          正整数（每手股数），拒绝 0/负数/bool/float/str
    max_stale_days      非负整数，拒绝 bool/float/NaN
    normalize_weights   严格 bool（issue #45 的显式 opt-in 开关）
    ==================  ====================================================

    任何非法值都在【构造时】抛 ValueError，早于 ``signal_func`` 调用、
    早于任何行情读取与账户状态产生，因此不可能出现"负执行价 / 免费持仓 /
    非有限净值 / 下单时 ZeroDivisionError"这类跑到一半才暴露的问题。
    """

    def __init__(
        self,
        initial_capital: float = 100_000,
        commission_rate: float = 0.0003,    # 万3手续费
        stamp_duty:      float = 0.001,     # 千1印花税（卖出）
        min_shares:      int   = 100,       # 最小交易单位
        slippage:        float = 0.001,     # 滑点（价格的0.1%）
        max_stale_days:  int   = 20,        # 估值价最大陈旧天数（issue #44）
        normalize_weights: bool = False,    # 显式 opt-in：Σ>1 等比归一化（issue #45）
    ):
        # 执行/成本参数 fail-fast 校验（issue #48 初始资金 + issue #64 其余参数）。
        # 所有非法配置在【构造时】就被拒绝——早于 signal_func 调用、早于任何
        # 行情读取与账户状态产生，杜绝"跑完才崩"或"跑出不可能的收益"。
        cfg = validate_backtester_config(
            initial_capital   = initial_capital,
            commission_rate   = commission_rate,
            stamp_duty        = stamp_duty,
            min_shares        = min_shares,
            slippage          = slippage,
            max_stale_days    = max_stale_days,
            normalize_weights = normalize_weights,
        )
        self.initial_capital = cfg["initial_capital"]
        self.commission_rate = cfg["commission_rate"]
        self.stamp_duty      = cfg["stamp_duty"]
        self.min_shares      = cfg["min_shares"]
        self.slippage        = cfg["slippage"]
        # 权重契约（issue #45）：默认 fail-fast——策略输出含 NaN/inf/负值/
        # 非数值/未知标的/单权重>1/Σ>1+容差 时抛 WeightValidationError，
        # 不产生任何交易、不修改账户状态。normalize_weights=True 为显式
        # opt-in：仅对【类型合法】且 Σ>1 的权重做等比归一化并记录诊断；
        # NaN/inf/bool/负值/未知标的在任何模式下都直接拒绝。
        self.normalize_weights = cfg["normalize_weights"]
        # stale-price 策略（issue #44）：持仓估值允许沿用最近有效收盘价，
        # 但连续超过 max_stale_days 个交易日无有效报价时发出显式告警
        # （diagnostics + warnings.warn），提示长期停牌/退市标的估值已不可靠。
        self.max_stale_days  = cfg["max_stale_days"]

    def run(
        self,
        price_data:  dict[str, pd.DataFrame],  # {code: OHLCV}
        signal_func: Callable,                  # 信号函数
        rebalance_freq: str = "W",              # W/W-first/W-last/M/M-first/M-last/D
    ) -> BacktestResult:
        """
        运行回测
        signal_func(date, past_data) → dict{code: weight}（权重合计≤1）
        past_data 只包含 date 之前的行情，避免用当日收盘生成同日成交信号。

        调仓约定（issue #43）：调仓日从【实际交易日索引】按周期分组产生，
        每个周期恰好一次；周一/月初休市不会跳过整个周期。
        - "W"  == "W-first"：每个交易周的第一个交易日调仓（周一休市→顺延周二）
        - "M"  == "M-first"：每个交易月的第一个交易日调仓（1 日休市→顺延首个交易日）
        - "W-last" / "M-last"：周期最后一个交易日调仓
        - "D"：每个交易日调仓
        信号仍只使用严格早于调仓日的数据（no-lookahead）。

        重复/日内多行策略（issue #43 重开验收）：索引出现完全相同的时间戳
        （脏数据或日内多行）时，引擎【按位置逐行】处理——调仓日按位置集合
        判定（同一周期只触发一次，不因日期比较双调仓）；当日执行价/估值价
        用 `iloc[i]` 位置取行（绝不 `.loc[date]`，后者对重复时间戳返回
        DataFrame 会破坏定价）；信号切片用 `df.index < date`，调仓日当天的
        所有重复行均不可见（no-lookahead 对重复行同样成立）。同一输入的
        处理结果是确定性的。

        缺价/停牌策略（issue #44）——估值价与执行价分离：
        - 估值价（valuation price）：持仓市值使用最近一个有效收盘价前向填充
          （ffill），单日停牌不再把持仓按 0 元计价制造虚假净值暴跌；
          连续无有效报价超过 max_stale_days 个交易日时发出显式告警
          （result.diagnostics + warnings.warn），提示长期停牌/退市标的。
        - 执行价（execution price）：买卖必须使用【当日】有限且为正的原始
          收盘价；当日无有效报价的标的既不能买入也不能卖出（含清仓路径），
          持仓保留到恢复报价后再处理。
        - 有效价定义：finite 且 > 0（NaN/inf/0/负价均视为无效）。

        权重契约（issue #45）——validation 与 normalization 分层：
        - 默认 fail-fast：signal_func 返回值必须是 Mapping，每个权重为
          实数且 finite、0 <= w <= 1，Σw <= 1 + 容差，标的必须在
          price_data 中；违反任何一条抛 WeightValidationError（消息含
          日期/违规权重/总和），该调仓日的现金、持仓、成本、净值、
          成交记录全部保持不变（异常先于任何状态修改抛出）。
        - bool / 字符串 / None / 非数值一律拒绝（True 不会被当作 1.0）。
        - 归一化是显式 opt-in（normalize_weights=True）：仅当权重类型全部
          合法且 Σ>1 时等比缩放至 Σ=1，并写入 diagnostics
          （type="weights_normalized"，含 requested/normalized/scale）。
        - 权重为 0 视为"不持有该标的"被剔除；全部为 0 或空 dict 视为
          清仓信号（issue #49）。
        - 每个调仓日在 result.weight_allocations 记录 requested vs
          executed 权重，暴露整手取整/费用/现金约束造成的跟踪误差。
        - 等价 Mapping（不同插入顺序）产生相同的校验结果与目标分配：
          买入顺序按 (权重降序, 代码升序) 确定，与插入顺序无关。
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

        # 估值价/执行价分离（issue #44）：
        #   clean_close：仅保留有限且 >0 的当日原始价（执行价来源）
        #   valuation_close：clean_close 前向填充（持仓估值来源）
        numeric_close = close_all.apply(pd.to_numeric, errors="coerce")
        finite_mask = numeric_close.apply(np.isfinite) & (numeric_close > 0)
        clean_close = numeric_close.where(finite_mask)
        valuation_close = clean_close.ffill()

        # 确定再平衡日期（issue #43）：
        # 从实际交易日索引按周期分组，默认取每个周期的【第一个交易日】，
        # 支持显式 first/last 约定；用位置掩码避免重复日期双调仓。
        dates = close_all.index
        rebal_positions = _rebalance_positions(dates, rebalance_freq)
        known_symbols = set(close_all.columns)   # 权重契约校验用（issue #45）

        # 初始化
        capital    = self.initial_capital
        holdings   = {}          # {code: shares}
        cost_basis = {}          # {code: avg_cost}
        trades     = []
        pnl_list   = []
        diagnostics: list[dict] = []
        weight_allocations: list[dict] = []   # requested vs executed（issue #45）
        stale_warned: set[str] = set()   # 已发过超限告警的标的
        last_valid_pos: dict[str, int] = {}  # {code: 最近有效报价的位置}

        prev_equity = capital
        realized_pnl: list[float] = []  # 闭环已实现盈亏（issue #42）

        for i, date in enumerate(dates):
            # 按位置取行，重复时间戳不会取出多行 DataFrame
            today_exec = clean_close.iloc[i]        # 当日执行价（无效=NaN）
            today_val  = valuation_close.iloc[i]    # 当日估值价（ffill）
            today_close = today_exec.to_dict()

            for c, p in today_close.items():
                if not pd.isna(p):
                    last_valid_pos[c] = i

            def _valuation_price(code: str) -> float:
                """持仓估值价：最近有效收盘价；从未有过有效报价则为 0。"""
                p = today_val.get(code)
                return 0.0 if (p is None or pd.isna(p)) else float(p)

            # 持仓估值 + stale 诊断（issue #44）
            market_value = 0.0
            for c in holdings:
                market_value += holdings.get(c, 0) * _valuation_price(c)
                if pd.isna(today_close.get(c)):
                    stale_age = i - last_valid_pos[c] if c in last_valid_pos else i + 1
                    diagnostics.append({
                        "date": str(date), "code": c,
                        "type": "stale_valuation", "stale_age": stale_age,
                    })
                    if stale_age > self.max_stale_days and c not in stale_warned:
                        stale_warned.add(c)
                        diagnostics.append({
                            "date": str(date), "code": c,
                            "type": "stale_limit_exceeded", "stale_age": stale_age,
                        })
                        warnings.warn(
                            f"[backtest] {c} 已连续 {stale_age} 个交易日无有效报价"
                            f"（超过 max_stale_days={self.max_stale_days}），"
                            f"持仓估值沿用最近有效价，可能已不可靠（长期停牌/退市）",
                            RuntimeWarning,
                        )
            total_equity = capital + market_value

            # 再平衡（按位置判断，重复日期行不会双调仓，issue #43）
            if i in rebal_positions:
                # 信号只能看见上一根 bar；当日价格仅用于执行与估值。
                past_data = {
                    c: sliced
                    for c, df in price_data.items()
                    if not (sliced := df[df.index < date]).empty
                }
                target_weights = signal_func(date, past_data) if past_data else {}

                # 权重契约（issue #45）：默认 fail-fast，非法输出在任何账户
                # 状态变化前抛 WeightValidationError；归一化仅显式 opt-in。
                target_weights = validate_target_weights(
                    target_weights,
                    date=date,
                    known_symbols=known_symbols,
                    allow_over_allocation=self.normalize_weights,
                )
                if self.normalize_weights:
                    target_weights, norm_info = normalize_target_weights(target_weights)
                    if norm_info is not None:
                        diagnostics.append({
                            "date": str(date),
                            "type": "weights_normalized",
                            "requested_total": norm_info["requested_total"],
                            "scale": norm_info["scale"],
                            "normalized": dict(target_weights),
                        })

                # 当日无有效执行价的目标标的：记录跳过交易诊断（issue #44）
                for c in target_weights:
                    if pd.isna(today_close.get(c)):
                        diagnostics.append({
                            "date": str(date), "code": c,
                            "type": "skipped_trade",
                        })

                if not target_weights:
                    # 清仓信号（空 dict）：卖出全部【当日有有效执行价】的持仓；
                    # 停牌标的保留，恢复报价后再清（issue #44/#49）
                    capital, holdings, cost_basis, day_trades, day_pnl = self._liquidate(
                        date, today_close, capital, holdings, cost_basis
                    )
                    trades.extend(day_trades)
                    realized_pnl.extend(day_pnl)
                else:
                    capital, holdings, cost_basis, day_trades, day_pnl = self._rebalance(
                        date, target_weights, today_close,
                        capital, holdings, cost_basis, total_equity
                    )
                    trades.extend(day_trades)
                    realized_pnl.extend(day_pnl)

                # 调仓后重新估值（估值价 ffill，issue #44）
                market_value = sum(
                    holdings.get(c, 0) * _valuation_price(c)
                    for c in holdings
                )
                total_equity = capital + market_value

                # requested vs executed 权重报告（issue #45）：
                # 整手取整/费用/现金约束造成的跟踪误差在此可见。
                if target_weights:
                    executed = {
                        c: (holdings.get(c, 0) * _valuation_price(c) / total_equity
                            if total_equity > 0 else 0.0)
                        for c in target_weights
                    }
                    weight_allocations.append({
                        "date": str(date),
                        "requested": dict(target_weights),
                        "executed": executed,
                    })

            daily_pnl = total_equity - prev_equity
            pnl_list.append({"date": date, "equity": total_equity, "pnl": daily_pnl})
            prev_equity = total_equity

        result_df  = pd.DataFrame(pnl_list).set_index("date")
        equity_ser = result_df["equity"]
        daily_pnl  = result_df["pnl"]

        result = self._calc_metrics(
            equity_ser, daily_pnl, trades,
            initial_capital=self.initial_capital,
            realized_pnl=realized_pnl,
        )
        result.diagnostics = diagnostics
        result.weight_allocations = weight_allocations
        return result

    def _rebalance(self, date, target_weights, prices, capital, holdings, cost_basis, total_equity):
        """执行再平衡，返回 (capital, holdings, cost_basis, trades, realized_pnl)

        prices 为【当日执行价】：仅当有限且 >0 时才允许买卖（issue #44）。
        """
        new_trades = []
        realized = []

        def _safe(code):
            """当日执行价：无效（缺失/NaN/inf/<=0）返回 0.0，调用方跳过交易"""
            return _exec_price(prices, code)

        # 先卖出不在目标中或需减仓的
        for code in list(holdings.keys()):
            target_w = target_weights.get(code, 0)
            target_val = total_equity * target_w
            cur_price = _safe(code)
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
                    avg_cost   = cost_basis.get(code, exec_price)
                    # 闭环盈亏：avg_cost 已含摊销后的买入手续费，此处再扣卖出费用，
                    # 双边费用恰好各计一次（issue #42）
                    realized.append((exec_price - avg_cost) * sell_sh - commission)
                    capital   += amount - commission
                    holdings[code] -= sell_sh
                    if holdings[code] <= 0:
                        del holdings[code]
                        del cost_basis[code]
                    new_trades.append(Trade(str(date), code, "sell",
                                            exec_price, sell_sh, amount, commission))
        # 再买入目标仓位（issue #45：按 (权重降序, 代码升序) 确定性排序，
        # 等价 Mapping 不同插入顺序产生完全相同的成交序列）
        for code, w in sorted(target_weights.items(), key=lambda x: (-x[1], x[0])):
            if w <= 0:
                continue
            cur_price = _safe(code)   # NaN（停牌）视作 0，跳过买入，避免 int(NaN) 崩溃
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
            # 加权平均成本 = (原持仓成本 + 本次买入金额 + 买入手续费) / 总股数
            # 买入手续费摊入成本基础，部分卖出时按股数比例自然释放（issue #42：
            # 双边费用在 realized PnL 中恰好计入一次，realized 与现金变化可对账）。
            cost_basis[code] = (cost_basis.get(code, 0.0) * prev_sh +
                                 amount + commission) / holdings[code]
            new_trades.append(Trade(str(date), code, "buy",
                                    exec_price, buy_sh, amount, commission))

        return capital, holdings, cost_basis, new_trades, realized

    def _liquidate(self, date, prices, capital, holdings, cost_basis):
        """清仓：卖出全部【当日有有效执行价】的持仓。

        无有效执行价（停牌/缺价）的标的保留持仓与成本基础，等恢复报价后
        再处理——绝不静默抹掉股份（issue #44）。
        返回 (capital, holdings, cost_basis, trades, realized_pnl)。
        """
        new_trades = []
        realized = []
        for code in list(holdings.keys()):
            cur_price = _exec_price(prices, code)
            if cur_price <= 0:
                continue  # 当日无有效执行价：保留持仓，不得凭空清除
            sell_sh   = holdings[code]
            exec_price = cur_price * (1 - self.slippage)
            amount     = sell_sh * exec_price
            commission = amount * self.commission_rate + amount * self.stamp_duty
            avg_cost   = cost_basis.get(code, exec_price)
            realized.append((exec_price - avg_cost) * sell_sh - commission)
            capital   += amount - commission
            del holdings[code]
            cost_basis.pop(code, None)
            new_trades.append(Trade(str(date), code, "sell",
                                    exec_price, sell_sh, amount, commission))
        return capital, holdings, cost_basis, new_trades, realized


    def _calc_metrics(
        self,
        equity: pd.Series,
        daily_pnl: pd.Series,
        trades: list,
        initial_capital: float = 100_000.0,
        realized_pnl: list[float] | None = None,
    ) -> BacktestResult:
        """计算绩效指标。

        统一初始 NAV 基线（issue #48）：total_return、annual_return、
        最大回撤、夏普输入收益序列全部基于同一条【前置了
        initial_capital 的净值序列】计算——首日交易成本/盈亏同时计入
        总收益、首期收益观测和回撤基线；一日回测的负收益不会再出现
        "total_return<0 但 max_drawdown==0、夏普无首期亏损"的不一致。
        年化周期数 = 收益观测数 = len(equity)（初始点→每个记录日），
        与 252/total_days 的年化口径一致。

        initial_capital 必须为有限正数（构造 Backtester 时已 fail-fast
        校验；直接调用本方法时同样拒绝非法值，不回退首条 equity）。
        """
        if equity.empty:
            return BacktestResult()

        initial_capital = _validate_initial_capital(initial_capital)

        total_days   = len(equity)
        # 前置初始资金，构成统一基线净值序列（issue #48）
        nav = pd.concat(
            [pd.Series([float(initial_capital)]),
             equity.astype(float).reset_index(drop=True)],
            ignore_index=True,
        )
        total_return = nav.iloc[-1] / initial_capital - 1
        annual_return = (1 + total_return) ** (252 / total_days) - 1

        # 最大回撤（基线含初始资金：首日亏损立即体现为回撤）
        roll_max = nav.cummax()
        drawdown = (nav - roll_max) / roll_max
        max_dd   = drawdown.min()

        # 夏普比率（年化）：收益序列同样从初始资金起算，
        # 首期观测 = equity[0]/initial_capital - 1（含首日交易成本）
        daily_ret = nav.pct_change().dropna()
        sharpe = daily_ret.mean() / (daily_ret.std() + 1e-9) * np.sqrt(252)

        # 卡玛比率
        calmar = annual_return / (abs(max_dd) + 1e-9)

        # 胜率 / 盈亏比：用【闭环已实现盈亏】（issue #42）。
        # 成本法：加权平均成本（含摊销的买入手续费）；每次卖出实现一笔 PnL，
        # 已扣除卖出手续费+印花税，全部平仓后 Σrealized == 现金净变化。
        pnls = [p for p in (realized_pnl or []) if p is not None]
        completed = len(pnls)
        if not pnls:
            # 回退：无已实现盈亏时按交易现金流估算（仅兼容老调用路径）
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
            completed_trades = completed,
            equity_curve   = equity,
            trades         = trades,
            daily_pnl      = daily_pnl,
        )


def _validate_initial_capital(value) -> float:
    """校验初始资金（issue #48）：必须是有限正数，否则抛 ValueError。

    NaN、±inf、0、负数、bool、字符串、None 等一律拒绝——在模拟开始前
    fail-fast，绝不静默回退到首条 equity 作为收益基准。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise ValueError(
            f"initial_capital 必须是数值类型，收到 {type(value).__name__}: {value!r}"
        )
    f = float(value)
    if not np.isfinite(f) or f <= 0:
        raise ValueError(
            f"initial_capital 必须是有限正数，收到 {value!r}"
        )
    return f


def _is_real_number(value) -> bool:
    """是否为可参与算术的实数标量（bool 不算：True/False 不是费率/价格）"""
    return (not isinstance(value, bool)
            and isinstance(value, (int, float, np.integer, np.floating)))


def _is_integer(value) -> bool:
    """是否为真整数标量（拒绝 bool / float / numpy 浮点 / 字符串）"""
    return (not isinstance(value, bool)
            and isinstance(value, (int, np.integer)))


def _validate_unit_fraction(value, name: str, *, why: str) -> float:
    """成本/滑点类比例参数（issue #64）：必须是有限实数且 0 <= x < 1。

    负值会把成本变成返现、把执行价压成负数（凭空造钱）；>= 1 会让
    卖出执行价 <= 0 或单笔费用吞掉全部本金。NaN/±inf 会顺着现金、
    执行价、手续费一路污染到净值和整数下单量，因此一并拒绝。
    """
    if not _is_real_number(value):
        raise ValueError(
            f"{name} 必须是数值类型，收到 {type(value).__name__}: {value!r}"
        )
    f = float(value)
    if not np.isfinite(f):
        raise ValueError(f"{name} 必须是有限数值（不能是 NaN/inf），收到 {value!r}")
    if f < 0:
        raise ValueError(f"{name} 不能为负（{why}），收到 {value!r}")
    if f >= 1:
        raise ValueError(f"{name} 必须小于 1（100%），收到 {value!r}")
    return f


def _validate_min_shares(value) -> int:
    """最小交易单位（issue #64）：必须是正整数。

    0 会在 ``_rebalance()`` 的 ``/ self.min_shares`` 处触发
    ZeroDivisionError（下单时才崩），负数/浮点/bool/字符串同样会让
    整数化手数失去意义，全部在构造时拒绝。
    """
    if not _is_integer(value):
        raise ValueError(
            f"min_shares 必须是正整数（每手股数），"
            f"收到 {type(value).__name__}: {value!r}"
        )
    iv = int(value)
    if iv <= 0:
        raise ValueError(f"min_shares 必须是正整数（每手股数），收到 {value!r}")
    return iv


def _validate_max_stale_days(value) -> int:
    """估值价最大陈旧天数（issue #64）：必须是非负整数（0 = 不容忍陈旧价）。"""
    if not _is_integer(value):
        raise ValueError(
            f"max_stale_days 必须是非负整数（交易日数），"
            f"收到 {type(value).__name__}: {value!r}"
        )
    iv = int(value)
    if iv < 0:
        raise ValueError(f"max_stale_days 必须是非负整数（交易日数），收到 {value!r}")
    return iv


def _validate_bool(value, name: str) -> bool:
    """严格布尔开关（issue #64）：只接受 bool / numpy.bool_。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, np.bool_):
        return bool(value)
    raise ValueError(
        f"{name} 必须是 bool，收到 {type(value).__name__}: {value!r}"
    )


def validate_backtester_config(
    *,
    initial_capital,
    commission_rate,
    stamp_duty,
    min_shares,
    slippage,
    max_stale_days,
    normalize_weights,
) -> dict:
    """集中式回测配置校验（issue #64）。

    在任何行情读取、策略回调、账户状态产生之前一次性校验全部
    执行/成本参数，非法配置抛 ValueError（参数名 + 收到的原值）。
    规则见 ``Backtester`` 类文档。返回规范化后的配置字典
    （数值统一为 float/int，便于调用方直接落盘或复现）。
    """
    return {
        "initial_capital": _validate_initial_capital(initial_capital),
        "commission_rate": _validate_unit_fraction(
            commission_rate, "commission_rate", why="负手续费等于凭空返现"),
        "stamp_duty": _validate_unit_fraction(
            stamp_duty, "stamp_duty", why="负印花税等于凭空返现"),
        "slippage": _validate_unit_fraction(
            slippage, "slippage", why="负滑点会造成负执行价，等于买入还倒贴现金"),
        "min_shares": _validate_min_shares(min_shares),
        "max_stale_days": _validate_max_stale_days(max_stale_days),
        "normalize_weights": _validate_bool(normalize_weights, "normalize_weights"),
    }


def _valid_price(value) -> bool:
    """有效价守卫（issue #44）：有限且 > 0 才可用于估值/执行。

    NaN、±inf、None、0、负价、不可转 float 的值一律无效。
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(f)) and f > 0


def _exec_price(prices: dict, code: str) -> float:
    """当日执行价：无效返回 0.0（调用方以 <=0 跳过交易，永不 int(NaN)）"""
    p = prices.get(code)
    return float(p) if _valid_price(p) else 0.0


def _rebalance_positions(dates: pd.DatetimeIndex, rebalance_freq: str) -> set[int]:
    """生成调仓日的【位置索引】集合（issue #43）。

    从实际交易日索引按周期分组（to_period 含年份，跨年不会把不同年份的
    同周号并组），每个周期恰好触发一次：
      - "W" / "W-first"：交易周第一个交易日（周一休市→顺延到周二等）
      - "W-last"       ：交易周最后一个交易日
      - "M" / "M-first"：交易月第一个交易日（1 日休市→顺延到首个交易日）
      - "M-last"       ：交易月最后一个交易日
      - "D" 及其他     ：每个交易日
    返回位置集合而非日期集合：重复/日内多行时同一周期只触发一次，
    且不会因日期比较把重复行各调一次仓。
    """
    n = len(dates)
    if n == 0:
        return set()

    freq = (rebalance_freq or "D").strip()
    key = freq.upper()
    if key in ("W", "W-FIRST", "W-LAST"):
        periods = dates.normalize().to_period("W")
    elif key in ("M", "M-FIRST", "M-LAST"):
        periods = dates.normalize().to_period("M")
    else:  # "D" 或未知频率：每个交易日
        return set(range(n))

    take_last = key.endswith("-LAST")
    positions: set[int] = set()
    prev_period = None
    for pos, p in enumerate(periods):
        if p != prev_period:
            if take_last and prev_period is not None:
                positions.add(pos - 1)   # 上一周期的最后一行
            if not take_last:
                positions.add(pos)       # 本周期的第一行
            prev_period = p
    if take_last:
        positions.add(n - 1)             # 最后一个周期的末行
    return positions


# 权重总和容差（issue #45）：吸收 1/3*3 == 1.0000000000000002 一类浮点误差
WEIGHT_SUM_TOLERANCE = 1e-6


class WeightValidationError(ValueError):
    """策略权重契约违规（issue #45）。

    在任何账户状态（现金/持仓/成本/净值/成交记录）变化之前抛出，
    消息包含调仓日期、违规权重与总和，便于定位策略 bug。
    """

    def __init__(self, message: str, *, date=None, weights=None, total=None):
        super().__init__(message)
        self.date = date
        self.weights = weights
        self.total = total


def validate_target_weights(
    target_weights,
    *,
    date=None,
    known_symbols: set | None = None,
    tolerance: float = WEIGHT_SUM_TOLERANCE,
    allow_over_allocation: bool = False,
) -> dict[str, float]:
    """校验策略输出权重（issue #45）——fail-fast，不做任何静默修正。

    契约：
    - target_weights 必须是 Mapping（None 视为空信号 {}）；
    - 每个权重必须是实数（bool / np.bool_ / 字符串 / None 一律拒绝，
      True 不会被当作 1.0）；
    - 必须 finite（NaN / ±inf 拒绝）；
    - 必须 >= 0（负权重拒绝——当前引擎只做多；做空需求应实现为
      文档化的显式模式，而非静默丢弃）；
    - known_symbols 给定时，未知标的拒绝（不再被静默按 0 元计价）；
    - 默认（allow_over_allocation=False）单权重 > 1 + tolerance 或
      Σ > 1 + tolerance 时拒绝；allow_over_allocation=True 仅跳过这
      两条"超配"检查（供显式 opt-in 归一化使用），类型/有限性/负值/
      未知标的检查在任何模式下都生效。

    返回：剔除 0 权重后的 {code: float} 副本。全 0 或空输入返回 {}
    （引擎将其视为清仓信号，issue #49）。
    违规抛 WeightValidationError，绝不修改输入。
    """
    if target_weights is None:
        return {}
    if not isinstance(target_weights, Mapping):
        raise WeightValidationError(
            f"[{date}] 策略输出必须是 Mapping{{code: weight}}，"
            f"实际为 {type(target_weights).__name__}",
            date=date, weights=target_weights,
        )

    cleaned: dict[str, float] = {}
    for code, w in target_weights.items():
        if isinstance(w, bool) or isinstance(w, np.bool_):
            raise WeightValidationError(
                f"[{date}] 权重必须是实数，标的 {code!r} 为 bool（{w!r}）",
                date=date, weights=dict(target_weights),
            )
        if not isinstance(w, numbers.Real):
            raise WeightValidationError(
                f"[{date}] 权重必须是实数，标的 {code!r} 为 "
                f"{type(w).__name__}（{w!r}）",
                date=date, weights=dict(target_weights),
            )
        wf = float(w)
        if not np.isfinite(wf):
            raise WeightValidationError(
                f"[{date}] 权重必须有限，标的 {code!r} 为 {wf!r}",
                date=date, weights=dict(target_weights),
            )
        if wf < 0:
            raise WeightValidationError(
                f"[{date}] 负权重被拒绝（当前引擎只做多），"
                f"标的 {code!r} 为 {wf!r}",
                date=date, weights=dict(target_weights),
            )
        if known_symbols is not None and code not in known_symbols:
            raise WeightValidationError(
                f"[{date}] 未知标的 {code!r} 不在行情数据中，拒绝下单",
                date=date, weights=dict(target_weights),
            )
        if not allow_over_allocation and wf > 1.0 + tolerance:
            raise WeightValidationError(
                f"[{date}] 单标的权重不得超过 1，标的 {code!r} 为 {wf!r}",
                date=date, weights=dict(target_weights),
            )
        if wf > 0:
            cleaned[code] = wf

    total = sum(cleaned.values())
    if not allow_over_allocation and total > 1.0 + tolerance:
        raise WeightValidationError(
            f"[{date}] 目标权重总和 {total!r} 超过 1 + 容差（{tolerance}），"
            f"违规权重: {cleaned!r}。若确需自动归一化，请显式使用 "
            f"Backtester(normalize_weights=True)",
            date=date, weights=cleaned, total=total,
        )
    return cleaned


def normalize_target_weights(
    weights: dict[str, float],
    tolerance: float = WEIGHT_SUM_TOLERANCE,
) -> tuple[dict[str, float], dict | None]:
    """显式 opt-in 归一化（issue #45）：Σ>1 时等比缩放至 Σ=1。

    仅接受已通过 validate_target_weights(allow_over_allocation=True)
    的权重（正、有限）。等比缩放保持相对比例，结果与 dict 插入顺序无关。
    返回 (normalized, info)：未触发归一化时 info 为 None，
    触发时 info = {"reason","requested_total","scale"}。
    """
    total = sum(weights.values())
    if total > 1.0 + tolerance:
        scale = 1.0 / total
        normalized = {c: v * scale for c, v in weights.items()}
        return normalized, {
            "reason": "sum_exceeds_one",
            "requested_total": total,
            "scale": scale,
        }
    return dict(weights), None


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


# 回测内因子矩阵缓存：同一标的、相同窗口（行数 + 末日期 + 末收盘）只算一次，
# 避免每个调仓日重算全量 Alpha158（日频回测的 O(重) 冗余，见 issue #32）。
_factor_matrix_cache: dict[str, object] = {}


def _factor_cache_key(code: str, df: pd.DataFrame) -> str:
    try:
        last_idx = str(df.index[-1])
        last_close = float(df["close"].iloc[-1])
        return f"{code}|{len(df)}|{last_idx}|{last_close:.6f}"
    except Exception:
        return f"{code}|{len(df)}"


def strategy_factor_rank(date, price_data: dict, top_n: int = 5) -> dict:
    """
    多因子排名策略：使用 Alpha158 因子合成评分
    """
    from quant.factors import compute_alpha158, multi_factor_score
    factor_data = {}
    for code, df in price_data.items():
        if len(df) >= 60:
            key = _factor_cache_key(code, df)
            cached = _factor_matrix_cache.get(key)
            if cached is None:
                cached = compute_alpha158(df)
                _factor_matrix_cache[key] = cached
            factor_data[code] = cached

    if not factor_data:
        return {}

    ranking = multi_factor_score(factor_data)
    if ranking.empty:
        return {}

    top = ranking.head(top_n)["code"].tolist()
    return {c: 1.0 / top_n for c in top}

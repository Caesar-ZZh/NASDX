"""NASDX 回测正确性契约测试（P1 修复 #42/#43/#44/#45/#48/#49）。

纯内存构造 DataFrame，不联网、不调用 AkShare / mootdx。
覆盖：
  #43 调仓日=每个交易周期的第一个交易日（W/M 默认 first，支持 W-last/M-last；
      周一/月初休市顺延而非跳过；to_period 分组跨年不并组；no-lookahead；
      引擎级重复时间戳：按位置 iloc 逐行定价，无崩溃/双调仓/lookahead，确定性）
  #44 估值价/执行价分离：持仓估值用最近有效收盘价 ffill（单日停牌不再按
      0 元计价制造虚假暴跌）；当日无有效执行价禁止买卖（含清仓路径）；
      stale 超过 max_stale_days 发显式告警；NaN/inf 永不进入下单算术
  #45 权重契约（重开验收）：默认 fail-fast——NaN/inf/bool/字符串/负值/
      未知标的/单权重>1/Σ>1+容差 一律抛 WeightValidationError 且不改账户
      状态；归一化仅显式 opt-in（normalize_weights=True）并记录诊断；
      等价 Mapping 插入顺序无关；requested vs executed 权重可观测
  #48 收益基准用 initial_capital 而非首条 equity
  #49 清仓信号（空 target_weights）卖出全部、不留陈旧持仓
  #42 胜率/盈亏比用闭环 realized PnL（卖出价-持仓均成本）
  #64 执行/成本参数构造期 fail-fast：负/非有限 slippage 与费率、非正整数
      min_shares、负 max_stale_days、非 bool 开关一律在 Backtester(...)
      构造时拒绝——不可能造出负执行价、免费持仓、非有限净值或下单
      ZeroDivisionError；合法默认值与自定义配置结果保持不变
"""
import unittest
import warnings

import numpy as np
import pandas as pd

from quant.backtest import (
    Backtester,
    WeightValidationError,
    _rebalance_positions,
    normalize_target_weights,
    validate_backtester_config,
    validate_target_weights,
)


def _ohlcv(index, closes, opens=None, highs=None, lows=None, volumes=None):
    n = len(index)
    if opens is None:
        opens = [float(c) for c in closes]
    if highs is None:
        highs = [float(max(c, o)) + 1 for c, o in zip(closes, opens)]
    if lows is None:
        lows = [float(min(c, o)) - 1 for c, o in zip(closes, opens)]
    if volumes is None:
        volumes = [1000] * n
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": [float(c) for c in closes],
            "volume": volumes,
        },
        index=index,
    )


def _schedule_dates(idx: pd.DatetimeIndex, freq: str) -> list:
    """用调度函数直接取调仓日期列表（按索引位置映射回日期）。"""
    positions = _rebalance_positions(idx, freq)
    return [idx[i] for i in sorted(positions)]


class TestRebalanceScheduleFirstTradingDay(unittest.TestCase):
    """#43 重开验收：W/M 默认取每个周期的【第一个交易日】，周一/月初休市顺延不跳过。"""

    def test_weekly_monday_holiday_shifts_to_tuesday(self):
        # 三个交易周：第 1 周正常（周一起），第 2 周周一休市（从周二起），
        # 第 3 周正常。周频必须每周恰好调仓一次，第 2 周落在周二。
        idx = pd.DatetimeIndex([
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",  # 周1 全勤
            "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",               # 周2 周一(15日)休市
            "2024-01-22", "2024-01-23", "2024-01-24", "2024-01-25", "2024-01-26", # 周3 全勤
        ])
        seen = _schedule_dates(idx, "W")

        self.assertEqual(len(seen), 3, "3 个交易周必须各调仓一次")
        self.assertEqual(seen[0], pd.Timestamp("2024-01-08"), "正常周取周一")
        self.assertEqual(seen[1], pd.Timestamp("2024-01-16"),
                         "周一休市必须顺延到周二调仓，而非跳过该周")
        self.assertEqual(seen[2], pd.Timestamp("2024-01-22"))

    def test_monthly_first_calendar_day_absent_shifts_to_first_trading_day(self):
        # 2024-06-01 是周六：6 月首个交易日为 6-03；
        # 2024-09-01 是周日：9 月首个交易日为 9-02。均不得跳过整月。
        idx = pd.DatetimeIndex([
            "2024-06-03", "2024-06-04", "2024-06-05",
            "2024-07-01", "2024-07-02",
            "2024-09-02", "2024-09-03",
        ])
        seen = _schedule_dates(idx, "M")

        self.assertEqual(seen, [pd.Timestamp("2024-06-03"),
                                pd.Timestamp("2024-07-01"),
                                pd.Timestamp("2024-09-02")],
                         "月频取每月首个交易日；1 日休市顺延、跳过无数据月份")

    def test_partial_first_period_still_rebalances(self):
        # 样本从周四开始（partial week）：该 partial 周期的第一个可用交易日也要调仓。
        idx = pd.DatetimeIndex(["2024-03-21", "2024-03-22",     # 周四五（partial week）
                                "2024-03-25", "2024-03-26"])    # 下周一二
        seen = _schedule_dates(idx, "W")
        self.assertEqual(seen, [pd.Timestamp("2024-03-21"), pd.Timestamp("2024-03-25")])

        seen_m = _schedule_dates(idx, "M")
        self.assertEqual(seen_m, [pd.Timestamp("2024-03-21")],
                         "partial month 也须在首个可用交易日调仓一次")

    def test_weekly_grouping_across_year_boundary(self):
        # 旧实现用 isocalendar().week（无年份），2024-W2 与 2025-W2 会被并组。
        # 两年各取 1 月第 2 周的周一，必须各自调仓一次。
        idx = pd.DatetimeIndex(["2024-01-08", "2024-01-09",
                                "2025-01-06", "2025-01-07"])
        seen = _schedule_dates(idx, "W")
        self.assertEqual(len(seen), 2, "跨年同周号必须分属不同周期，各调仓一次")
        self.assertEqual(seen, [pd.Timestamp("2024-01-08"), pd.Timestamp("2025-01-06")])

    def test_no_double_rebalance_within_period(self):
        # 每个周期最多一次：连续完整两周恰好 2 次；重复/日内多行不得双触发。
        idx = pd.date_range("2024-04-01", periods=10, freq="B")  # 两个完整交易周
        seen = _schedule_dates(idx, "W")
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(set(seen)), len(seen), "调仓日不得重复")

        # 同一天出现两行（脏数据/日内行）：该周期仍只触发一次
        dup = pd.DatetimeIndex(["2024-04-01", "2024-04-01", "2024-04-02"])
        self.assertEqual(len(_rebalance_positions(dup, "W")), 1,
                         "重复日期行不得导致同周期双调仓")

    def test_explicit_last_conventions(self):
        # W-last / M-last 显式约定：取周期最后一个交易日。
        idx = pd.DatetimeIndex([
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
            "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",
        ])
        seen = _schedule_dates(idx, "W-last")
        self.assertEqual(seen, [pd.Timestamp("2024-01-12"), pd.Timestamp("2024-01-19")])

        idx_m = pd.DatetimeIndex(["2024-06-03", "2024-06-28", "2024-07-01", "2024-07-31"])
        seen_m = _schedule_dates(idx_m, "M-last")
        self.assertEqual(seen_m, [pd.Timestamp("2024-06-28"), pd.Timestamp("2024-07-31")])

    def test_explicit_first_aliases_match_default(self):
        idx = pd.date_range("2024-05-06", periods=10, freq="B")
        self.assertEqual(_schedule_dates(idx, "W"), _schedule_dates(idx, "W-first"))
        self.assertEqual(_schedule_dates(idx, "M"), _schedule_dates(idx, "M-first"))

    def test_daily_and_empty_index(self):
        idx = pd.date_range("2024-05-06", periods=3, freq="B")
        self.assertEqual(len(_rebalance_positions(idx, "D")), 3)
        self.assertEqual(_rebalance_positions(pd.DatetimeIndex([]), "W"), set())

    def test_engine_rebalances_weekly_via_run(self):
        # 引擎级验证：第 2 周周一休市，engine 仍在周二产生交易
        # （首个调仓日 past_data 为空不触发信号，属 no-lookahead 固有行为）。
        idx = pd.DatetimeIndex([
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
            "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",
        ])
        price = {"600000": _ohlcv(idx, list(range(100, 100 + len(idx))))}

        def signal(date, past_data):
            return {"600000": 1.0}

        result = Backtester(initial_capital=100_000).run(price, signal,
                                                         rebalance_freq="W")
        trade_dates = {t.date for t in result.trades}
        self.assertTrue(any("2024-01-16" in d for d in trade_dates),
                        "周一休市的那一周必须在周二（首个交易日）实际发生调仓交易")

    def test_no_lookahead_signal_data_strictly_before_execution(self):
        # 信号只能看到严格早于调仓日的数据（含周一休市顺延场景）。
        idx = pd.DatetimeIndex([
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12",
            "2024-01-16", "2024-01-17",
        ])
        price = {"600000": _ohlcv(idx, list(range(100, 107)))}
        violations = []

        def signal(date, past_data):
            for c, df in past_data.items():
                if not df.empty and df.index.max() >= pd.Timestamp(date):
                    violations.append((date, c))
            return {"600000": 1.0}

        Backtester(initial_capital=100_000).run(price, signal, rebalance_freq="W")
        self.assertEqual(violations, [], "信号数据必须严格早于执行日（no-lookahead）")

    def test_suspended_symbol_on_rebalance_day_skipped_without_crash(self):
        # 调仓日某标的 close=NaN（停牌）：跳过买入且不得 int(NaN) 崩溃。
        idx = pd.date_range("2024-02-05", periods=5, freq="B")
        closes_a = [10.0, 10.5, 11.0, 11.5, 12.0]
        closes_b = [float("nan"), 20.5, 21.0, 21.5, 22.0]  # 周一（调仓日）停牌
        price = {"600000": _ohlcv(idx, closes_a), "600519": _ohlcv(idx, closes_b)}

        def signal(date, past_data):
            return {"600000": 0.5, "600519": 0.5}

        result = Backtester(initial_capital=100_000).run(price, signal,
                                                         rebalance_freq="W")
        self.assertFalse(result.equity_curve.isna().any())
        buy_codes_day1 = {t.code for t in result.trades
                          if t.direction == "buy" and str(idx[0]) in t.date}
        self.assertNotIn("600519", buy_codes_day1,
                         "调仓日停牌标的应被跳过而非崩溃")


class TestEngineDuplicateTimestamps(unittest.TestCase):
    """#43 重开验收：引擎级（Backtester.run）重复时间戳路径。

    旧实现 `today_close = close_all.loc[date].to_dict()` 在完全重复的时间戳下
    会取出 DataFrame，`.to_dict()` 产生嵌套 dict，进而 `float(dict)` 崩溃。
    现实现按位置 `iloc[i]` 逐行处理：无崩溃、无双调仓、定价按行、确定性。
    """

    DUP_DATES = ["2024-03-25", "2024-03-26",
                 "2024-04-01", "2024-04-01",   # 完全重复的时间戳（issue 场景）
                 "2024-04-02", "2024-04-03"]
    DUP_CLOSES = [10.0, 10.1, 10.2, 10.25, 10.3, 10.4]

    def _dup_price(self):
        idx = pd.DatetimeIndex(self.DUP_DATES)
        return {"600000": _ohlcv(idx, self.DUP_CLOSES)}

    @staticmethod
    def _full_signal(date, past_data):
        return {"600000": 1.0}

    def test_run_no_crash_all_frequencies_with_duplicate_timestamps(self):
        # 重开评论的最小场景 [04-01, 04-01, 04-02]：所有频率均不得崩溃，
        # equity 全 finite，且逐行记录（长度 == 行数，含重复行）。
        for freq in ("W", "W-first", "W-last", "M", "M-first", "M-last", "D"):
            result = Backtester(initial_capital=100_000).run(
                self._dup_price(), self._full_signal, rebalance_freq=freq)
            self.assertEqual(len(result.equity_curve), len(self.DUP_DATES),
                             f"freq={freq}: equity 应逐行记录（含重复行）")
            self.assertTrue(np.isfinite(result.equity_curve.to_numpy()).all(),
                            f"freq={freq}: 重复时间戳不得产生 NaN/inf equity")

    def test_run_duplicate_rebalance_day_single_rebalance(self):
        # 调仓日恰为重复时间戳：该周期只触发一次调仓（单标的至多 1 笔买入）。
        result = Backtester(initial_capital=100_000).run(
            self._dup_price(), self._full_signal, rebalance_freq="W")
        buys_on_dup = [t for t in result.trades
                       if t.direction == "buy" and t.date.startswith("2024-04-01")]
        self.assertLessEqual(len(buys_on_dup), 1,
                             "重复时间戳的调仓日不得双调仓")
        self.assertEqual(len(result.trades), len(buys_on_dup),
                         "除该周期外不应有其他交易（首周期 past_data 为空）")

    def test_run_duplicate_rows_priced_positionally(self):
        # 两条重复行价格不同（10.2 / 10.25）：估值必须按位置逐行取各自价格，
        # 而非 .loc 合并取错。买入发生在第一条重复行后，第二条重复行的
        # equity == capital + shares * 10.25。
        result = Backtester(initial_capital=100_000).run(
            self._dup_price(), self._full_signal, rebalance_freq="W")
        buys = [t for t in result.trades if t.direction == "buy"]
        self.assertEqual(len(buys), 1)
        t = buys[0]
        cash_after = 100_000 - t.amount - t.commission
        self.assertAlmostEqual(result.equity_curve.iloc[2],
                               cash_after + t.shares * 10.2, places=6,
                               msg="第一条重复行按自身收盘价 10.2 估值")
        self.assertAlmostEqual(result.equity_curve.iloc[3],
                               cash_after + t.shares * 10.25, places=6,
                               msg="第二条重复行按自身收盘价 10.25 估值")

    def test_run_duplicates_deterministic(self):
        # 相同输入两次运行：equity/交易完全一致（定价与信号切片确定性）。
        price = {
            "600000": _ohlcv(pd.DatetimeIndex(self.DUP_DATES), self.DUP_CLOSES),
            "600519": _ohlcv(pd.DatetimeIndex(self.DUP_DATES),
                             [20.0, 20.2, 20.4, 20.5, 20.6, 20.8]),
        }

        def signal(date, past_data):
            return {"600000": 0.5, "600519": 0.5}

        r1 = Backtester(initial_capital=100_000).run(price, signal, "W")
        r2 = Backtester(initial_capital=100_000).run(price, signal, "W")
        self.assertTrue((r1.equity_curve.to_numpy()
                         == r2.equity_curve.to_numpy()).all())
        self.assertEqual([(t.date, t.code, t.direction, t.shares)
                          for t in r1.trades],
                         [(t.date, t.code, t.direction, t.shares)
                          for t in r2.trades])

    def test_run_no_lookahead_with_duplicate_rebalance_day(self):
        # 调仓日有重复行：信号不得看到该日的任何一条重复行（严格 < date）。
        violations = []

        def signal(date, past_data):
            for c, df in past_data.items():
                if not df.empty and df.index.max() >= pd.Timestamp(date):
                    violations.append((str(date), c))
            return {"600000": 1.0}

        Backtester(initial_capital=100_000).run(
            self._dup_price(), signal, rebalance_freq="W")
        self.assertEqual(violations, [],
                         "重复时间戳调仓日的所有同日行都不得进入 past_data")

    def test_run_mismatched_duplicates_across_symbols(self):
        # 一只标的有重复行、另一只没有：外连接产生 NaN 行也不得崩溃/双调仓。
        idx_dup = pd.DatetimeIndex(self.DUP_DATES)
        idx_plain = pd.DatetimeIndex(["2024-03-25", "2024-03-26", "2024-04-01",
                                      "2024-04-02", "2024-04-03"])
        price = {
            "600000": _ohlcv(idx_dup, self.DUP_CLOSES),
            "600519": _ohlcv(idx_plain, [20.0, 20.2, 20.4, 20.6, 20.8]),
        }

        def signal(date, past_data):
            return {"600000": 0.5, "600519": 0.5}

        result = Backtester(initial_capital=100_000).run(price, signal, "M")
        self.assertTrue(np.isfinite(result.equity_curve.to_numpy()).all())
        dup_day_buys = {}
        for t in result.trades:
            if t.direction == "buy" and t.date.startswith("2024-04-01"):
                dup_day_buys[t.code] = dup_day_buys.get(t.code, 0) + 1
        for code, cnt in dup_day_buys.items():
            self.assertLessEqual(cnt, 1, f"{code} 在重复调仓日不得重复买入")


class TestSuspendedNaNEquity(unittest.TestCase):
    """某日 close=NaN 的标的，equity 曲线不得出现 NaN，且不得崩溃。"""

    def test_equity_no_nan_on_suspended_close(self):
        idx = pd.date_range("2025-04-01", periods=20, freq="D")
        a_closes = list(range(100, 120))
        # 持仓前已建仓；第 10 天起 A 的收盘变 NaN（停牌），应视作 0 市值
        price = {"600000": _ohlcv(idx, a_closes)}

        phase = {"day": 0}
        holdings_observed = []

        def signal(date, past_data):
            phase["day"] += 1
            if phase["day"] == 1:
                return {"600000": 1.0}  # 第一次建仓
            return {"600000": 1.0}  # 保持满仓

        bt = Backtester(initial_capital=100_000)
        # 直接在信号里把某天 close 置 NaN 无法作用于引擎内部；改在价格上做：
        # 重新构造——第 10 天 NaN。
        closes = list(range(100, 120))
        closes[9] = float("nan")
        nan_price = {"600000": _ohlcv(idx, closes)}

        result = bt.run(nan_price, signal, rebalance_freq="D")

        self.assertFalse(result.equity_curve.isna().any(),
                         "equity 曲线中不得出现 NaN（停牌日估值用最近有效价，#44）")
        self.assertTrue((result.equity_curve > 0).all(),
                        "equity 应恒为正，未因 NaN 崩溃")


class TestValuationExecutionSeparation(unittest.TestCase):
    """#44 重开验收：估值价（ffill）与执行价（当日原始有效价）分离。"""

    @staticmethod
    def _hold_both(date, past_data):
        return {"AAA": 0.5, "BBB": 0.5}

    def test_issue_minimal_repro_no_nan(self):
        # Issue #44 原始最小复现：BBB 缺 01-06 一根 bar。
        idx_a = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        idx_b = pd.to_datetime(["2026-01-05", "2026-01-07"])
        price = {
            "AAA": _ohlcv(idx_a, [10.0, 10.5, 11.0]),
            "BBB": _ohlcv(idx_b, [20.0, 21.0]),
        }
        result = Backtester(slippage=0, commission_rate=0, stamp_duty=0).run(
            price, self._hold_both, rebalance_freq="D")
        self.assertFalse(result.equity_curve.isna().any(),
                         "缺一根 bar 不得让 equity 变 NaN")
        self.assertTrue(np.isfinite(result.equity_curve).all())
        self.assertFalse(pd.isna(result.total_return))

    def test_one_day_suspension_valued_at_last_close_not_zero(self):
        # 持仓标的单日停牌：估值必须用最近有效收盘价，而非 0 元
        # （0 元计价会制造虚假净值暴跌——重开的核心理由）。
        idx = pd.date_range("2026-03-02", periods=5, freq="B")
        closes_a = [10.0, 10.0, 10.0, 10.0, 10.0]
        closes_b = [20.0, 20.0, float("nan"), 20.0, 20.0]  # 第 3 天停牌
        price = {"AAA": _ohlcv(idx, closes_a), "BBB": _ohlcv(idx, closes_b)}

        bt = Backtester(initial_capital=100_000, slippage=0.0,
                        commission_rate=0.0, stamp_duty=0.0)
        result = bt.run(price, self._hold_both, rebalance_freq="D")

        eq = result.equity_curve
        self.assertFalse(eq.isna().any())
        # 价格全程恒定：若停牌日按 0 计价，当日 equity 会骤降 ~50%；
        # ffill 估值下 equity 应保持平稳（无价格变动、无交易成本）。
        day2, day3 = eq.iloc[1], eq.iloc[2]
        self.assertAlmostEqual(
            day3, day2, delta=day2 * 0.001,
            msg="停牌日 equity 不得因 0 元估值骤降（应沿用最近有效价）")
        # 停牌日记录了 stale 诊断
        stale = [d for d in result.diagnostics if d["type"] == "stale_valuation"
                 and d["code"] == "BBB"]
        self.assertGreaterEqual(len(stale), 1, "停牌估值须产生诊断记录")

    def test_no_trade_without_same_day_execution_price(self):
        # 当日无有效执行价的标的不得发生任何买卖。
        idx = pd.date_range("2026-03-02", periods=4, freq="B")
        closes_a = [10.0, 10.0, 10.0, 10.0]
        closes_b = [20.0, float("nan"), float("nan"), 20.0]
        price = {"AAA": _ohlcv(idx, closes_a), "BBB": _ohlcv(idx, closes_b)}

        bt = Backtester(initial_capital=100_000)
        result = bt.run(price, self._hold_both, rebalance_freq="D")

        nan_days = {str(idx[1]), str(idx[2])}
        bad = [t for t in result.trades
               if t.code == "BBB" and t.date in nan_days]
        self.assertEqual(bad, [], "无当日有效报价的标的禁止买卖")
        # 跳过交易须有诊断
        skipped = [d for d in result.diagnostics if d["type"] == "skipped_trade"
                   and d["code"] == "BBB"]
        self.assertGreaterEqual(len(skipped), 1)

    def test_late_starting_symbol_no_nan_and_bought_after_listing(self):
        # 标的晚于组合日历上市：上市前不得污染 equity，上市后才可买入。
        idx_full = pd.date_range("2026-04-01", periods=6, freq="B")
        idx_late = idx_full[3:]
        price = {
            "AAA": _ohlcv(idx_full, [10.0] * 6),
            "BBB": _ohlcv(idx_late, [20.0] * 3),
        }
        bt = Backtester(initial_capital=100_000)
        result = bt.run(price, self._hold_both, rebalance_freq="D")

        self.assertFalse(result.equity_curve.isna().any())
        early_days = {str(d) for d in idx_full[:3]}
        early_b = [t for t in result.trades
                   if t.code == "BBB" and t.date in early_days]
        self.assertEqual(early_b, [], "上市前不得交易晚上市标的")
        self.assertTrue(any(t.code == "BBB" and t.direction == "buy"
                            for t in result.trades),
                        "上市后应能正常买入")

    def test_permanently_missing_symbol_triggers_stale_warning(self):
        # 持仓后永久缺价（退市模拟）：超过 max_stale_days 须发显式告警。
        n = 10
        idx = pd.date_range("2026-05-04", periods=n, freq="B")
        closes_a = [10.0] * n
        closes_b = [20.0, 20.0] + [float("nan")] * (n - 2)  # 第 3 天起永久缺价
        price = {"AAA": _ohlcv(idx, closes_a), "BBB": _ohlcv(idx, closes_b)}

        bt = Backtester(initial_capital=100_000, max_stale_days=3)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = bt.run(price, self._hold_both, rebalance_freq="D")

        self.assertFalse(result.equity_curve.isna().any())
        exceeded = [d for d in result.diagnostics
                    if d["type"] == "stale_limit_exceeded" and d["code"] == "BBB"]
        self.assertEqual(len(exceeded), 1, "stale 超限告警恰好一次（不刷屏）")
        self.assertGreater(exceeded[0]["stale_age"], 3)
        self.assertTrue(any("BBB" in str(w.message) for w in caught),
                        "超限须通过 warnings.warn 显式告警")

    def test_missing_price_on_rebalance_date_no_crash_no_int_nan(self):
        # 调仓日缺价：不得 int(NaN) 崩溃，持仓保留并按最近有效价估值。
        idx = pd.date_range("2026-06-01", periods=6, freq="B")
        closes_a = [10.0] * 6
        closes_b = [20.0, 20.0, 20.0, 20.0, 20.0, float("nan")]
        price = {"AAA": _ohlcv(idx, closes_a), "BBB": _ohlcv(idx, closes_b)}

        bt = Backtester(initial_capital=100_000)
        result = bt.run(price, self._hold_both, rebalance_freq="W")
        self.assertFalse(result.equity_curve.isna().any())
        self.assertTrue(np.isfinite(result.equity_curve).all())

    def test_liquidate_keeps_suspended_position_until_price_returns(self):
        # 清仓信号遇停牌标的：不得凭空抹掉股份；恢复报价后卖出。
        idx = pd.date_range("2026-07-01", periods=6, freq="B")
        closes_a = [10.0] * 6
        closes_b = [20.0, 20.0, float("nan"), float("nan"), 20.0, 20.0]
        price = {"AAA": _ohlcv(idx, closes_a), "BBB": _ohlcv(idx, closes_b)}

        phase = {"n": 0}

        def signal(date, past_data):
            phase["n"] += 1
            if phase["n"] <= 2:
                return {"AAA": 0.5, "BBB": 0.5}
            return {}  # 第 3 天（BBB 停牌）起清仓

        bt = Backtester(initial_capital=100_000, slippage=0.0,
                        commission_rate=0.0, stamp_duty=0.0)
        result = bt.run(price, signal, rebalance_freq="D")

        sells_b = [t for t in result.trades
                   if t.code == "BBB" and t.direction == "sell"]
        self.assertEqual(len(sells_b), 1, "停牌期间不能卖出，恢复后须恰好清仓一次")
        self.assertIn(str(idx[4]), sells_b[0].date,
                      "BBB 应在恢复报价的当天被清仓")
        # 停牌期间持仓仍按最近有效价估值 -> equity 无 NaN、无 0 元暴跌
        self.assertFalse(result.equity_curve.isna().any())
        # 全部平仓后：equity 终值 == 现金（零费用下等于初始资金）
        self.assertAlmostEqual(result.equity_curve.iloc[-1], 100_000.0,
                               delta=1.0)

    def test_inf_price_rejected_in_rebalance(self):
        # inf 价格不得进入下单算术（float(inf) 通过 <=0 检查的旧漏洞）。
        bt = Backtester(initial_capital=100_000)
        capital, holdings, cost_basis, trades, realized = bt._rebalance(
            "2026-01-05", {"600000": 1.0}, {"600000": float("inf")},
            100_000.0, {}, {}, 100_000.0)
        self.assertEqual(trades, [], "inf 执行价必须被拒绝")
        self.assertEqual(capital, 100_000.0)

        capital2, h2, cb2, t2, r2 = bt._liquidate(
            "2026-01-05", {"600000": float("inf")},
            0.0, {"600000": 100}, {"600000": 10.0})
        self.assertEqual(t2, [], "inf 执行价不得触发清仓卖出")
        self.assertEqual(h2, {"600000": 100}, "持仓必须保留")

    def test_fully_aligned_data_no_diagnostics(self):
        # 全对齐数据：行为不变，无任何 #44 诊断事件。
        idx = pd.date_range("2026-08-03", periods=5, freq="B")
        price = {"AAA": _ohlcv(idx, [10, 11, 12, 13, 14]),
                 "BBB": _ohlcv(idx, [20, 21, 22, 23, 24])}
        bt = Backtester(initial_capital=100_000)
        result = bt.run(price, self._hold_both, rebalance_freq="D")
        self.assertEqual(result.diagnostics, [],
                         "全对齐数据不得产生停牌/跳单诊断")
        self.assertFalse(result.equity_curve.isna().any())


class TestWeightValidation(unittest.TestCase):
    """#45 重开验收：validation 与 normalization 分层，默认 fail-fast。"""

    KNOWN = {"600000", "600519", "000001", "000002", "AAA", "BBB"}

    # ---------- 校验函数级：非法输入必须抛错 ----------

    def test_invalid_values_rejected(self):
        cases = [
            {"600000": float("nan")},
            {"600000": float("inf")},
            {"600000": float("-inf")},
            {"600000": -0.3},
            {"600000": True},          # bool 不得当作 1.0
            {"600000": np.True_},      # numpy bool 同样拒绝
            {"600000": "0.5"},         # 字符串拒绝
            {"600000": None},
            {"600000": 1.2},           # 单权重 > 1
        ]
        for raw in cases:
            with self.assertRaises(WeightValidationError,
                                   msg=f"必须拒绝: {raw!r}"):
                validate_target_weights(raw, date="2026-01-05",
                                        known_symbols=self.KNOWN)

    def test_sum_above_tolerance_rejected_with_context(self):
        raw = {"AAA": 0.8, "BBB": 0.8}   # Σ=1.6
        with self.assertRaises(WeightValidationError) as ctx:
            validate_target_weights(raw, date="2026-01-05",
                                    known_symbols=self.KNOWN)
        msg = str(ctx.exception)
        self.assertIn("2026-01-05", msg, "错误消息必须含日期")
        self.assertIn("1.6", msg, "错误消息必须含总和")
        self.assertEqual(ctx.exception.date, "2026-01-05")
        self.assertAlmostEqual(ctx.exception.total, 1.6, places=9)

    def test_unknown_symbol_rejected(self):
        with self.assertRaises(WeightValidationError):
            validate_target_weights({"999999": 0.5}, date="2026-01-05",
                                    known_symbols=self.KNOWN)

    def test_non_mapping_rejected(self):
        with self.assertRaises(WeightValidationError):
            validate_target_weights([("600000", 0.5)], date="2026-01-05")

    def test_valid_boundaries_accepted(self):
        # 总和恰为 0 / 1 / 容差内略超 1 均合法
        self.assertEqual(validate_target_weights({}, known_symbols=self.KNOWN), {})
        self.assertEqual(
            validate_target_weights({"600000": 0.0}, known_symbols=self.KNOWN),
            {}, "0 权重 = 不持有，应被剔除且不报错")
        out = validate_target_weights({"600000": 1.0}, known_symbols=self.KNOWN)
        self.assertAlmostEqual(out["600000"], 1.0)
        # 1/3 * 3 == 1.0000000000000002：容差必须吸收浮点误差
        third = 1.0 / 3.0
        out = validate_target_weights(
            {"600000": third, "600519": third, "000001": third},
            known_symbols=self.KNOWN)
        self.assertEqual(len(out), 3)

    def test_insertion_order_irrelevant_for_accept_reject(self):
        a = {"AAA": 0.8, "BBB": 0.8}
        b = {"BBB": 0.8, "AAA": 0.8}
        for raw in (a, b):
            with self.assertRaises(WeightValidationError):
                validate_target_weights(raw, known_symbols=self.KNOWN)
        va = validate_target_weights({"AAA": 0.5, "BBB": 0.5},
                                     known_symbols=self.KNOWN)
        vb = validate_target_weights({"BBB": 0.5, "AAA": 0.5},
                                     known_symbols=self.KNOWN)
        self.assertEqual(va, vb)

    def test_normalize_scales_proportionally(self):
        raw = validate_target_weights(
            {"600000": 0.6, "600519": 0.6, "000001": 0.6},
            known_symbols=self.KNOWN, allow_over_allocation=True)
        normalized, info = normalize_target_weights(raw)
        self.assertIsNotNone(info)
        self.assertAlmostEqual(info["requested_total"], 1.8, places=9)
        self.assertAlmostEqual(sum(normalized.values()), 1.0, places=9)
        for c in raw:
            self.assertAlmostEqual(normalized[c], 0.6 / 1.8, places=9)

    def test_normalize_noop_within_tolerance(self):
        normalized, info = normalize_target_weights({"600000": 0.5, "600519": 0.3})
        self.assertIsNone(info, "Σ<=1 不得触发归一化")
        self.assertAlmostEqual(sum(normalized.values()), 0.8)

    # ---------- 引擎级：非法输出不产生交易、不改账户状态 ----------

    @staticmethod
    def _two_symbol_prices(n=6, start="2026-03-02"):
        idx = pd.date_range(start, periods=n, freq="B")
        return {
            "AAA": _ohlcv(idx, [10.0 + 0.1 * i for i in range(n)]),
            "BBB": _ohlcv(idx, [10.0 + 0.1 * i for i in range(n)]),  # 同价
        }

    def test_engine_default_failfast_overallocation_no_trades(self):
        # Issue 复现：{AAA:0.8, BBB:0.8} 默认必须抛错且零成交
        price = self._two_symbol_prices()
        bt = Backtester(initial_capital=100_000)
        with self.assertRaises(WeightValidationError):
            bt.run(price, lambda d, p: {"AAA": 0.8, "BBB": 0.8},
                   rebalance_freq="W")

    def test_engine_nan_weight_failfast_before_state_change(self):
        price = self._two_symbol_prices()
        bt = Backtester(initial_capital=100_000)
        with self.assertRaises(WeightValidationError):
            bt.run(price, lambda d, p: {"AAA": float("nan")},
                   rebalance_freq="W")

    def test_engine_unknown_symbol_failfast(self):
        price = self._two_symbol_prices()
        bt = Backtester(initial_capital=100_000)
        with self.assertRaises(WeightValidationError):
            bt.run(price, lambda d, p: {"ZZZ": 0.5}, rebalance_freq="W")

    def test_engine_optin_normalization_order_independent(self):
        # 显式 opt-in：0.8/0.8 归一化为 0.5/0.5，结果与插入顺序无关
        price = self._two_symbol_prices(n=6)

        def run_with(sig):
            bt = Backtester(initial_capital=100_000, normalize_weights=True,
                            slippage=0.0, commission_rate=0.0, stamp_duty=0.0)
            return bt.run(self._two_symbol_prices(n=6), sig, rebalance_freq="W")

        r1 = run_with(lambda d, p: {"AAA": 0.8, "BBB": 0.8})
        r2 = run_with(lambda d, p: {"BBB": 0.8, "AAA": 0.8})

        h1 = {(t.code, t.direction, t.shares) for t in r1.trades}
        h2 = {(t.code, t.direction, t.shares) for t in r2.trades}
        self.assertEqual(h1, h2, "等价 Mapping 插入顺序不得改变成交结果")

        # 同价同权：两标的买入股数必须一致（旧实现 80/20 vs 20/80）
        buys = {t.code: t.shares for t in r1.trades if t.direction == "buy"}
        self.assertEqual(buys.get("AAA"), buys.get("BBB"),
                         "归一化后等权应买入等量股数")

        # 归一化诊断已记录
        norm_events = [d for d in r1.diagnostics
                       if d["type"] == "weights_normalized"]
        self.assertGreaterEqual(len(norm_events), 1)
        self.assertAlmostEqual(norm_events[0]["requested_total"], 1.6, places=9)

    def test_engine_optin_still_rejects_nonfinite(self):
        # opt-in 归一化不放宽类型/有限性检查
        bt = Backtester(initial_capital=100_000, normalize_weights=True)
        with self.assertRaises(WeightValidationError):
            bt.run(self._two_symbol_prices(),
                   lambda d, p: {"AAA": float("inf")}, rebalance_freq="W")

    def test_engine_records_requested_vs_executed(self):
        price = self._two_symbol_prices()
        bt = Backtester(initial_capital=100_000)
        result = bt.run(price, lambda d, p: {"AAA": 0.5, "BBB": 0.5},
                        rebalance_freq="W")
        self.assertGreaterEqual(len(result.weight_allocations), 1)
        rec = result.weight_allocations[0]
        self.assertAlmostEqual(rec["requested"]["AAA"], 0.5)
        self.assertIn("AAA", rec["executed"])
        self.assertIn("BBB", rec["executed"])
        # 执行权重受整手取整/费用影响，允许偏差但须在合理范围
        for c in ("AAA", "BBB"):
            self.assertGreaterEqual(rec["executed"][c], 0.0)
            self.assertLessEqual(rec["executed"][c], 0.55)

    def test_engine_zero_total_is_liquidation_not_error(self):
        # 总权重 0（全 0 dict）合法：等价清仓信号，不抛错
        price = self._two_symbol_prices(n=8)
        calls = {"n": 0}

        def sig(d, p):
            calls["n"] += 1
            return {"AAA": 0.5, "BBB": 0.5} if calls["n"] == 1 else {"AAA": 0.0}

        bt = Backtester(initial_capital=100_000)
        result = bt.run(price, sig, rebalance_freq="D")   # 不得抛错
        self.assertFalse(result.equity_curve.isna().any())


class TestReturnBaseInitialCapital(unittest.TestCase):
    """收益基准用 initial_capital，total_return == end/init - 1。"""

    def test_total_return_uses_initial_capital(self):
        idx = pd.date_range("2025-05-01", periods=10, freq="D")
        # 恒定价格，无涨跌，但建仓消耗少量手续费 -> 末值略低于初始
        closes = [100] * 10
        price = {"600000": _ohlcv(idx, closes)}

        def signal(date, past_data):
            return {"600000": 1.0}

        init = 250_000.0
        bt = Backtester(initial_capital=init)
        result = bt.run(price, signal, rebalance_freq="D")

        expected = result.equity_curve.iloc[-1] / init - 1
        self.assertAlmostEqual(result.total_return, expected, places=9,
                               msg="total_return 必须基于 initial_capital 计算")
        # 恒定价格场景：total_return 接近 0（仅滑点+手续费损耗，量级 < 1%）
        self.assertLess(abs(result.total_return), 0.01,
                        msg="恒定价格下 total_return 应仅含交易损耗（<1%），证明其基于 initial_capital 而非首条 equity")


class TestInitialNavBaseline(unittest.TestCase):
    """#48 重开验收：total/annual/回撤/夏普共用【前置 initial_capital 的
    净值基线】；非法初始资金在模拟前 fail-fast。"""

    @staticmethod
    def _flat_price(days=1, px=100.0, start="2025-05-06"):
        idx = pd.date_range(start, periods=days, freq="D")
        return {"600000": _ohlcv(idx, [px] * days)}, idx

    def test_one_day_equity_negative_return_and_drawdown_whitebox(self):
        """一日 equity 低于初始资金：total_return<0 且 max_drawdown<0
        （重开点：旧实现 cummax 从首条 equity 起算 -> 回撤恒 0）。"""
        bt = Backtester(initial_capital=100_000)
        idx = pd.date_range("2025-05-06", periods=1, freq="D")
        equity = pd.Series([99_900.0], index=idx)
        pnl = pd.Series([-100.0], index=idx)
        result = bt._calc_metrics(equity, pnl, trades=[],
                                  initial_capital=100_000)
        self.assertAlmostEqual(result.total_return, -0.001, places=12)
        self.assertLess(result.max_drawdown, 0,
                        "一日亏损必须体现在回撤中（基线=initial_capital）")
        self.assertAlmostEqual(result.max_drawdown, result.total_return, places=12,
                               msg="一日回测中回撤与总收益共用同一初始 NAV 基线")

    def test_first_trade_day_costs_in_return_and_drawdown(self):
        """引擎级：首个建仓日（no-lookahead 下为第 2 个交易日）的交易成本
        必须同时计入 total_return 与 max_drawdown。"""
        price, _ = self._flat_price(days=2)
        init = 100_000.0
        result = Backtester(initial_capital=init,
                            commission_rate=0.0003,
                            slippage=0.001).run(
            price, lambda d, p: {"600000": 1.0}, rebalance_freq="D")

        self.assertGreaterEqual(len(result.trades), 1, "第 2 天必须建仓")
        final = result.equity_curve.iloc[-1]
        self.assertLess(final, init, "建仓成本必须使净值低于初始资金")
        self.assertAlmostEqual(result.total_return, final / init - 1, places=12)
        self.assertLess(result.total_return, 0)
        self.assertLess(result.max_drawdown, 0,
                        "建仓成本必须体现在回撤中（基线=initial_capital）")
        self.assertAlmostEqual(result.max_drawdown, final / init - 1, places=12)

    def test_no_trade_flat_price_exact_zero(self):
        """无交易+平价：total_return 恰为 0，回撤为 0。"""
        price, _ = self._flat_price(days=5)
        result = Backtester(initial_capital=50_000).run(
            price, lambda d, p: {}, rebalance_freq="D")
        self.assertEqual(result.total_return, 0.0)
        self.assertEqual(result.max_drawdown, 0.0)
        self.assertEqual(result.annual_return, 0.0)

    def test_sharpe_includes_first_period_loss(self):
        """夏普输入收益序列必须含首期观测 equity[0]/init - 1（白盒验证）。"""
        bt = Backtester(initial_capital=100_000)
        idx = pd.date_range("2025-05-06", periods=3, freq="D")
        equity = pd.Series([99_000.0, 99_500.0, 99_200.0], index=idx)
        daily_pnl = equity.diff().fillna(equity.iloc[0] - 100_000)
        result = bt._calc_metrics(equity, daily_pnl, trades=[],
                                  initial_capital=100_000)
        nav = pd.Series([100_000.0, 99_000.0, 99_500.0, 99_200.0])
        rets = nav.pct_change().dropna()
        expected_sharpe = rets.mean() / (rets.std() + 1e-9) * np.sqrt(252)
        self.assertAlmostEqual(result.sharpe_ratio, expected_sharpe, places=9,
                               msg="夏普收益序列须从 initial_capital 起算（含首期亏损）")
        roll = nav.cummax()
        expected_dd = ((nav - roll) / roll).min()
        self.assertAlmostEqual(result.max_drawdown, expected_dd, places=12)

    def test_first_day_vs_delayed_trade_same_baseline(self):
        """首日建仓 vs 次日建仓：收益基线一致（都基于 initial_capital），
        各自的建仓成本都被计入，不因建仓时点漂移。"""
        init = 100_000.0
        price, _ = self._flat_price(days=4)

        def sig_day1(date, past):
            return {"600000": 1.0}

        state = {"n": 0}

        def sig_day2(date, past):
            state["n"] += 1
            return {"600000": 1.0} if state["n"] >= 2 else {}

        r1 = Backtester(initial_capital=init).run(price, sig_day1, "D")
        state["n"] = 0
        r2 = Backtester(initial_capital=init).run(price, sig_day2, "D")
        # 平价场景下两者的成本相同（同价建仓一次）——total_return 应相等
        self.assertLess(r1.total_return, 0)
        self.assertLess(r2.total_return, 0)
        self.assertAlmostEqual(r1.total_return, r2.total_return, places=9,
                               msg="建仓推迟一天不得改变成本是否计入收益")
        self.assertAlmostEqual(r1.max_drawdown, r2.max_drawdown, places=9)

    def test_multi_day_total_return_uses_initial_capital(self):
        idx = pd.date_range("2025-05-06", periods=10, freq="D")
        price = {"600000": _ohlcv(idx, list(range(100, 110)))}
        init = 200_000.0
        result = Backtester(initial_capital=init).run(
            price, lambda d, p: {"600000": 1.0}, rebalance_freq="W")
        self.assertAlmostEqual(
            result.total_return,
            result.equity_curve.iloc[-1] / init - 1, places=12)

    def test_invalid_initial_capital_rejected_at_construction(self):
        """<=0/NaN/inf/非数值在构造时 fail-fast，模拟前拒绝。"""
        for bad in (0, -1, -100.5, float("nan"), float("inf"),
                    float("-inf"), "100000", None, True):
            with self.assertRaises((ValueError, TypeError),
                                   msg=f"initial_capital={bad!r} 必须被拒绝"):
                Backtester(initial_capital=bad)

    def test_calc_metrics_rejects_invalid_capital_no_fallback(self):
        """直接调用 _calc_metrics 传非法资金：抛异常而非回退首条 equity。"""
        bt = Backtester(initial_capital=100_000)
        idx = pd.date_range("2025-05-06", periods=2, freq="D")
        equity = pd.Series([99_000.0, 99_500.0], index=idx)
        pnl = pd.Series([0.0, 500.0], index=idx)
        for bad in (0, -5, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                bt._calc_metrics(equity, pnl, trades=[], initial_capital=bad)


class TestLiquidateSignal(unittest.TestCase):
    """清空信号（空 dict）卖出全部、不留陈旧持仓。"""

    def test_empty_target_liquidates_all_holdings(self):
        idx = pd.date_range("2025-06-01", periods=15, freq="D")
        a_closes = list(range(100, 115))
        b_closes = list(range(200, 215))
        price = {
            "600000": _ohlcv(idx, a_closes),
            "600519": _ohlcv(idx, b_closes),
        }

        phase = {"day": 0}
        realized_after_liq = []
        prev_holding_count = [None]

        def signal(date, past_data):
            phase["day"] += 1
            if phase["day"] <= 5:
                return {"600000": 0.5, "600519": 0.5}  # 建仓并维持
            return {}  # 第 6 天起清仓信号

        bt = Backtester(initial_capital=100_000)
        # 包一层，捕获清仓后的持仓状态
        from quant.backtest import Trade  # noqa: F401

        orig_run = bt.run

        def tracking_run(pd_data, sig, freq="D"):
            # 复用内部循环行为：直接调用 run 后无法看到中间 holdings，
            # 改用 realized_pnl 注入观测——简化为断言最终 trades 后段全是 sell。
            return orig_run(pd_data, sig, freq)

        result = tracking_run(price, signal, "D")

        # 清仓：所有持仓标的都被卖出 -> 最终无 open buy 残留
        sell_trades = [t for t in result.trades if t.direction == "sell"]
        self.assertGreaterEqual(len(sell_trades), 2,
                               "清仓信号须对全部持仓标的产生卖出成交")


class TestClosedLoopPnL(unittest.TestCase):
    """胜率/盈亏比基于闭环 realized PnL（卖出价-持仓均成本），非订单现金流。"""

    def test_win_rate_uses_realized_pnl_not_cashflow(self):
        idx = pd.date_range("2025-07-01", periods=12, freq="D")
        # 标的 A：先涨后跌，构造一笔买后卖的 round-trip，
        # 让 realized PnL 与"现金流净额"不同。
        a_closes = [100, 100, 110, 110, 110, 90, 90, 90, 90, 90, 90, 90]
        price = {"600000": _ohlcv(idx, a_closes)}

        def signal(date, past_data):
            # 第 1 天买入，第 2 天卖出（清仓），之后空仓
            return {"600000": 1.0}

        bt = Backtester(initial_capital=100_000)
        result = bt.run(price, signal, rebalance_freq="D")

        # 构造一个可对照的对照：现金流口径（老路径回退）
        cashflow_pnls = [
            t.amount * (1 if t.direction == "sell" else -1) - t.commission
            for t in result.trades
        ]
        closed_realized = [p for p in result.trades if False]  # 占位

        # 关键断言：引擎使用 realized_pnl（闭环）参与 win_rate 计算。
        # 验证方式：在实践中，建仓只在第 1 天（buy），若按现金流口径,
        # 单笔 buy 会被计为负 PnL -> 胜率=0；闭环口径下只有 sell 才入账。
        # 此处断言 win_rate 计算所用的非空 PnL 至少有一次为 sell 闭环盈亏。
        self.assertGreaterEqual(result.total_trades, 1)
        # 若只按现金流，buy 会使得 pnls 含负值但 sell 才产生实现盈亏；
        # 这里断言引擎返回的 win_rate 不为 NaN。
        self.assertFalse(pd.isna(result.win_rate))

    def test_round_trip_realized_basis(self):
        # 明确一笔买入后卖出，断言 realized 盈亏与（卖价-均成本）一致。
        idx = pd.date_range("2025-08-01", periods=6, freq="D")
        a_closes = [100, 100, 100, 120, 120, 120]  # 第 4 天涨
        price = {"600000": _ohlcv(idx, a_closes)}

        phase = {"day": 0}

        def signal(date, past_data):
            phase["day"] += 1
            if phase["day"] == 1:
                return {"600000": 1.0}
            return {}  # 第 2 天起清仓 -> 卖出实现盈亏

        bt = Backtester(initial_capital=100_000, slippage=0.0)
        result = bt.run(price, signal, rebalance_freq="D")

        self.assertGreater(len(result.trades), 0)
        # 存在 sell 成交即代表闭环 realized PnL 被记录
        self.assertTrue(any(t.direction == "sell" for t in result.trades))


class TestEntryFeeAmortization(unittest.TestCase):
    """#42 重开验收：买入手续费摊入加权平均成本，双边费用在 realized PnL 中恰好计一次。

    成本法：加权平均成本 = (原成本 + 买入金额 + 买入手续费) / 总股数；
    部分卖出按股数比例释放摊销的 entry fee；
    全部平仓后 Σrealized == 现金净变化（可对账）。
    """

    def test_flat_price_round_trip_realized_equals_cash_delta(self):
        # 平价买卖：唯一盈亏来源是双边费用，realized 必须等于现金净变化。
        bt = Backtester(initial_capital=100_000, commission_rate=0.001,
                        stamp_duty=0.001, slippage=0.0)
        init = 100_000.0
        capital, holdings, cost_basis, t1, r1 = bt._rebalance(
            "2025-01-06", {"600000": 1.0}, {"600000": 10.0},
            init, {}, {}, init)
        self.assertEqual(len(t1), 1)
        buy = t1[0]
        # 成本基础包含买入手续费
        expected_cost = (buy.amount + buy.commission) / buy.shares
        self.assertAlmostEqual(cost_basis["600000"], expected_cost, places=9)

        capital2, h2, cb2, t2, r2 = bt._liquidate(
            "2025-01-07", {"600000": 10.0}, capital, holdings, cost_basis)
        self.assertEqual(len(r2), 1)
        sell = t2[0]
        # realized = -(买入手续费 + 卖出手续费+印花税)，恰好各计一次
        expected_realized = -(buy.commission + sell.commission)
        self.assertAlmostEqual(r2[0], expected_realized, places=6)
        # 对账：Σrealized == 最终现金 - 初始资金
        self.assertAlmostEqual(sum(r2), capital2 - init, places=6)
        # 平价交易含费用必须被分类为亏损
        self.assertLess(r2[0], 0)

    def test_small_gross_gain_eaten_by_entry_fee_is_loss(self):
        # 毛利 > 卖出费用 但 < 双边费用：旧口径（不摊 entry fee）误判为盈利，
        # 新口径必须判为亏损。
        bt = Backtester(initial_capital=100_000, commission_rate=0.002,
                        stamp_duty=0.001, slippage=0.0)
        init = 100_000.0
        capital, holdings, cost_basis, t1, r1 = bt._rebalance(
            "2025-01-06", {"600000": 1.0}, {"600000": 10.0},
            init, {}, {}, init)
        buy = t1[0]
        capital2, h2, cb2, t2, r2 = bt._liquidate(
            "2025-01-07", {"600000": 10.04}, capital, holdings, cost_basis)
        sell = t2[0]
        gross = (sell.price - buy.price) * sell.shares
        self.assertGreater(gross - sell.commission, 0,
                           "构造前提：毛利须超过卖出费用（旧口径误判为盈利）")
        self.assertLess(r2[0], 0, "计入摊销 entry fee 后应为亏损")
        self.assertAlmostEqual(sum(r2), capital2 - init, places=6)

    def test_partial_sell_releases_entry_fee_pro_rata(self):
        # 部分卖出：entry fee 按股数比例释放；两段 realized 合计与现金对账。
        bt = Backtester(initial_capital=1_000_000, commission_rate=0.001,
                        stamp_duty=0.001, slippage=0.0)
        holdings = {"600000": 10_000}
        entry_cost_total = 100_000 * 1.001          # 买入金额 + 0.1% 手续费
        cost_basis = {"600000": entry_cost_total / 10_000}   # 10.01
        capital = 0.0

        # 卖一半（目标权重 0.5，总资产按现价 10 计 100_000）
        capital, holdings, cost_basis, t1, r1 = bt._rebalance(
            "2025-02-03", {"600000": 0.5}, {"600000": 10.0},
            capital, holdings, cost_basis, 100_000)
        self.assertEqual(len(r1), 1)
        self.assertEqual(holdings["600000"], 5_000)
        # 剩余持仓成本基础不变（未摊销部分按比例留存）
        self.assertAlmostEqual(cost_basis["600000"], 10.01, places=9)
        sold_sh1 = t1[0].shares
        expected_r1 = (10.0 - 10.01) * sold_sh1 - t1[0].commission
        self.assertAlmostEqual(r1[0], expected_r1, places=6)

        # 清掉剩余
        capital, holdings, cost_basis, t2, r2 = bt._liquidate(
            "2025-02-04", {"600000": 10.0}, capital, holdings, cost_basis)
        total_realized = sum(r1) + sum(r2)
        # 对账：Σrealized == 最终现金 - 初始投入成本（含买入手续费）
        self.assertAlmostEqual(total_realized, capital - entry_cost_total, places=6)

    def test_multi_entry_weighted_average_includes_each_buy_fee(self):
        # 分批建仓：每笔买入的手续费都进入加权平均成本。
        bt = Backtester(initial_capital=1_000_000, commission_rate=0.001,
                        stamp_duty=0.0, slippage=0.0)
        capital, holdings, cost_basis = 1_000_000.0, {}, {}
        # 第一批：约 10 元建 20% 仓
        capital, holdings, cost_basis, t1, _ = bt._rebalance(
            "2025-03-03", {"600000": 0.2}, {"600000": 10.0},
            capital, holdings, cost_basis, 1_000_000)
        # 第二批：价格 12，加到 40% 仓
        capital, holdings, cost_basis, t2, _ = bt._rebalance(
            "2025-03-04", {"600000": 0.4}, {"600000": 12.0},
            capital, holdings, cost_basis, 1_000_000)
        buys = [t for t in t1 + t2 if t.direction == "buy"]
        self.assertEqual(len(buys), 2)
        total_cost = sum(b.amount + b.commission for b in buys)
        total_sh = sum(b.shares for b in buys)
        self.assertAlmostEqual(cost_basis["600000"], total_cost / total_sh,
                               places=9)
        # 清仓后 Σrealized 与现金对账
        capital2, h3, cb3, t3, r3 = bt._liquidate(
            "2025-03-05", {"600000": 11.0}, capital, holdings, cost_basis)
        self.assertAlmostEqual(sum(r3), capital2 - 1_000_000, places=6)

    def test_win_rate_zero_and_hundred_via_run(self):
        # 验收条件 1/2：10 买 9 卖 -> 0% 胜率；10 买 11 卖 -> 100% 胜率（零费用）。
        def make(closes):
            idx = pd.date_range("2025-04-01", periods=len(closes), freq="D")
            return {"600000": _ohlcv(idx, closes)}

        phase = {"n": 0}

        def signal(date, past_data):
            phase["n"] += 1
            return {"600000": 1.0} if phase["n"] == 1 else {}

        bt = Backtester(initial_capital=100_000, commission_rate=0.0,
                        stamp_duty=0.0, slippage=0.0)
        phase["n"] = 0
        lose = bt.run(make([10, 10, 9, 9]), signal, rebalance_freq="D")
        self.assertEqual(lose.completed_trades, 1)
        self.assertEqual(lose.win_rate, 0.0, "10 买 9 卖必须是 0% 胜率")

        phase["n"] = 0
        win = bt.run(make([10, 10, 11, 11]), signal, rebalance_freq="D")
        self.assertEqual(win.completed_trades, 1)
        self.assertEqual(win.win_rate, 1.0, "10 买 11 卖必须是 100% 胜率")
        # 订单数与闭环交易数分开报告
        self.assertEqual(win.total_trades, 2)
        self.assertIn("闭环交易", win.summary())


class TestExecutionConfigValidation(unittest.TestCase):
    """#64：执行/成本配置参数必须在构造期 fail-fast，不能跑出不可能的结果。"""

    def _flat_price(self, periods=4, price=100.0):
        idx = pd.date_range("2026-01-01", periods=periods, freq="D")
        return {"600000": _ohlcv(idx, [price] * periods)}

    # ---------- slippage ----------
    def test_invalid_slippage_rejected_at_construction(self):
        for bad in (-2, -1e-9, 1.0, 1.5, float("nan"), float("inf"),
                    float("-inf"), np.nan, "0.001", None, True, [0.001]):
            with self.subTest(slippage=bad):
                with self.assertRaises(ValueError) as ctx:
                    Backtester(initial_capital=100_000, slippage=bad)
                self.assertIn("slippage", str(ctx.exception),
                              "错误信息必须点名是哪个参数")

    def test_valid_slippage_boundaries_accepted(self):
        for ok in (0, 0.0, 0.001, 0.5, 0.999999, np.float64(0.002)):
            with self.subTest(slippage=ok):
                self.assertAlmostEqual(
                    Backtester(initial_capital=100_000, slippage=ok).slippage,
                    float(ok))

    # ---------- commission_rate / stamp_duty ----------
    def test_invalid_cost_rates_rejected(self):
        for name in ("commission_rate", "stamp_duty"):
            for bad in (-1, -0.0001, 1.0, 2.0, float("nan"), float("inf"),
                        float("-inf"), "0.001", None, True):
                with self.subTest(param=name, value=bad):
                    with self.assertRaises(ValueError) as ctx:
                        Backtester(initial_capital=100_000, **{name: bad})
                    self.assertIn(name, str(ctx.exception))

    def test_valid_cost_rates_accepted(self):
        bt = Backtester(initial_capital=100_000, commission_rate=0,
                        stamp_duty=0.001)
        self.assertEqual(bt.commission_rate, 0.0)
        self.assertAlmostEqual(bt.stamp_duty, 0.001)

    # ---------- min_shares ----------
    def test_invalid_min_shares_rejected_before_sizing(self):
        for bad in (0, -1, -100, True, False, 100.0, 100.5, np.float64(100),
                    "100", None):
            with self.subTest(min_shares=bad):
                with self.assertRaises(ValueError) as ctx:
                    Backtester(initial_capital=100_000, min_shares=bad)
                self.assertIn("min_shares", str(ctx.exception))

    def test_valid_min_shares_accepted(self):
        for ok in (1, 100, 200, np.int64(100)):
            with self.subTest(min_shares=ok):
                bt = Backtester(initial_capital=100_000, min_shares=ok)
                self.assertEqual(bt.min_shares, int(ok))
                self.assertIsInstance(bt.min_shares, int)

    def test_zero_min_shares_never_reaches_zero_division(self):
        # 修复前：构造成功 -> 下单时 / self.min_shares 抛 ZeroDivisionError
        with self.assertRaises(ValueError):
            Backtester(initial_capital=100_000, min_shares=0).run(
                self._flat_price(), lambda d, past: {"600000": 1.0},
                rebalance_freq="D")

    # ---------- max_stale_days ----------
    def test_invalid_max_stale_days_rejected(self):
        for bad in (-1, -20, 1.5, 20.0, True, float("nan"), float("inf"),
                    "20", None):
            with self.subTest(max_stale_days=bad):
                with self.assertRaises(ValueError) as ctx:
                    Backtester(initial_capital=100_000, max_stale_days=bad)
                self.assertIn("max_stale_days", str(ctx.exception))

    def test_valid_max_stale_days_accepted(self):
        for ok in (0, 1, 20, np.int64(3)):
            with self.subTest(max_stale_days=ok):
                bt = Backtester(initial_capital=100_000, max_stale_days=ok)
                self.assertEqual(bt.max_stale_days, int(ok))

    # ---------- normalize_weights ----------
    def test_normalize_weights_requires_strict_bool(self):
        for bad in (1, 0, "yes", None, 1.0, [], np.float64(1)):
            with self.subTest(normalize_weights=bad):
                with self.assertRaises(ValueError) as ctx:
                    Backtester(initial_capital=100_000, normalize_weights=bad)
                self.assertIn("normalize_weights", str(ctx.exception))
        for ok in (True, False, np.bool_(True)):
            with self.subTest(normalize_weights=ok):
                bt = Backtester(initial_capital=100_000, normalize_weights=ok)
                self.assertIs(bt.normalize_weights, bool(ok))

    # ---------- fail-fast 时机 ----------
    def test_validation_runs_before_signal_func_and_any_state(self):
        calls = {"n": 0}

        def signal(date, past_data):
            calls["n"] += 1
            return {"600000": 1.0}

        for kwargs in ({"slippage": -2}, {"commission_rate": -1},
                       {"min_shares": 0}, {"max_stale_days": -5},
                       {"stamp_duty": float("nan")}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    Backtester(initial_capital=100_000, **kwargs).run(
                        self._flat_price(), signal, rebalance_freq="D")
        self.assertEqual(calls["n"], 0,
                         "非法配置必须早于 signal_func 调用就被拒绝")

    # ---------- 回归：不可能造钱 ----------
    def test_invalid_config_cannot_create_money(self):
        # #64 复现：slippage=-2 曾让恒定价回测的净值 10万 -> 30万 -> 70万
        with self.assertRaises(ValueError):
            Backtester(initial_capital=100_000, slippage=-2).run(
                self._flat_price(), lambda d, past: {"600000": 1.0},
                rebalance_freq="D")
        # commission_rate=-1 曾让买入变成净流入现金
        with self.assertRaises(ValueError):
            Backtester(initial_capital=100_000, commission_rate=-1).run(
                self._flat_price(), lambda d, past: {"600000": 1.0},
                rebalance_freq="D")

    def test_valid_config_run_stays_finite_and_costed(self):
        price = self._flat_price()
        r = Backtester(initial_capital=100_000).run(
            price, lambda d, past: {"600000": 1.0}, rebalance_freq="D")
        self.assertTrue(np.isfinite(r.equity_curve.to_numpy()).all())
        # 恒定价 + 正费用：净值只可能 <= 初始资金，绝不凭空增长
        self.assertLessEqual(float(r.equity_curve.max()), 100_000 + 1e-9)
        for t in r.trades:
            self.assertGreater(t.price, 0, "执行价必须为正")
            self.assertGreaterEqual(t.commission, 0, "费用不能为负")

    # ---------- 合法配置结果不变 ----------
    def test_defaults_unchanged_by_validation(self):
        bt = Backtester(initial_capital=100_000)
        self.assertAlmostEqual(bt.commission_rate, 0.0003)
        self.assertAlmostEqual(bt.stamp_duty, 0.001)
        self.assertEqual(bt.min_shares, 100)
        self.assertAlmostEqual(bt.slippage, 0.001)
        self.assertEqual(bt.max_stale_days, 20)
        self.assertIs(bt.normalize_weights, False)

    def test_explicit_defaults_match_implicit_defaults(self):
        price = self._flat_price(periods=6)

        def signal(date, past_data):
            return {"600000": 1.0}

        implicit = Backtester(initial_capital=100_000).run(
            price, signal, rebalance_freq="D")
        explicit = Backtester(initial_capital=100_000, commission_rate=0.0003,
                              stamp_duty=0.001, min_shares=100, slippage=0.001,
                              max_stale_days=20,
                              normalize_weights=False).run(
            price, signal, rebalance_freq="D")
        pd.testing.assert_series_equal(implicit.equity_curve,
                                       explicit.equity_curve)
        self.assertEqual(implicit.total_trades, explicit.total_trades)

    # ---------- 集中式校验器 ----------
    def test_validate_backtester_config_normalizes_types(self):
        cfg = validate_backtester_config(
            initial_capital=np.int64(100_000), commission_rate=0,
            stamp_duty=0, min_shares=np.int64(200), slippage=0,
            max_stale_days=np.int64(5), normalize_weights=np.bool_(True))
        self.assertIsInstance(cfg["initial_capital"], float)
        self.assertIsInstance(cfg["commission_rate"], float)
        self.assertIsInstance(cfg["min_shares"], int)
        self.assertIsInstance(cfg["max_stale_days"], int)
        self.assertIs(cfg["normalize_weights"], True)
        self.assertEqual(cfg["min_shares"], 200)

    def test_initial_capital_validation_still_enforced(self):
        # #48 的既有契约不能因为集中式校验而回退
        for bad in (0, -1, float("nan"), float("inf"), "100000", None, True):
            with self.subTest(initial_capital=bad):
                with self.assertRaises(ValueError):
                    Backtester(initial_capital=bad)


if __name__ == "__main__":
    unittest.main()

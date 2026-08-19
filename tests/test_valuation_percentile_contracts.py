"""valuation_percentile 契约测试（纯 mock，不联网）。"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


class FakeAkshare:
    """模拟 akshare，按 (symbol, indicator, period) 返回预置 DataFrame。"""

    def __init__(self):
        self.calls: list[tuple] = []

    def stock_zh_valuation_baidu(self, symbol: str, indicator: str, period: str):
        self.calls.append((symbol, indicator, period))
        pe_values = [12.0, 14.0, 13.0, 15.0, 18.0, 20.0, 19.0, 22.0, 25.0, 30.0]
        pb_values = [1.0, 1.2, 1.1, 1.3, 1.5, 1.8, 1.7, 2.0, 2.3, 2.8]
        if indicator == "市盈率(TTM)":
            vals = pe_values
        elif indicator == "市净率":
            vals = pb_values
        else:
            vals = []
        df = MagicMock()
        df.iloc = MagicMock()
        col_series = MagicMock()
        col_series.dropna.return_value.astype.return_value.tolist.return_value = vals
        df.iloc.__getitem__ = MagicMock(side_effect=lambda x: col_series if x == 1 else col_series)
        return df


def _make_module(fake_ak):
    with patch.dict("sys.modules", {"akshare": MagicMock()}):
        import nasdx.valuation as mod
        mod._akshare = MagicMock(return_value=fake_ak)
        return mod


def test_normal_case():
    fake = FakeAkshare()
    mod = _make_module(fake)
    result = mod.valuation_percentile("600519")

    assert result["code"] == "600519"
    assert result["period"] == "近5年"
    assert "pe_ttm" in result["metrics"]
    assert "pb" in result["metrics"]

    pe = result["metrics"]["pe_ttm"]
    assert pe["current"] == 30.0
    assert pe["n"] == 10
    # 当前 30.0 = max，below=9, percentile≈90.0
    assert pe["percentile"] == pytest.approx(90.0, abs=1)
    assert pe["min"] == 12.0
    assert pe["max"] == 30.0
    # p50 应在中间值附近（线性插值）
    assert 15.0 <= pe["p50"] <= 20.0

    pb = result["metrics"]["pb"]
    assert pb["current"] == 2.8
    assert pb["n"] == 10
    assert pb["min"] == 1.0
    assert pb["max"] == 2.8


def test_empty_sequence():
    """指标返回空序列时对应 metric 不出现，不应抛异常。"""
    fake = FakeAkshare()
    mod = _make_module(fake)

    def empty_df(symbol, indicator, period):
        df = MagicMock()
        col = MagicMock()
        col.dropna.return_value.astype.return_value.tolist.return_value = []
        df.iloc = MagicMock()
        df.iloc.__getitem__ = MagicMock(return_value=col)
        return df

    with patch.object(mod, "_akshare", MagicMock(return_value=MagicMock(stock_zh_valuation_baidu=empty_df))):
        result = mod.valuation_percentile("600519")
    assert result["metrics"] == {}


def test_single_value():
    """只有单条记录：percentile 应为 0.0（below/(n-1) 分母退化）。"""
    fake = FakeAkshare()
    mod = _make_module(fake)

    def single_df(symbol, indicator, period):
        df = MagicMock()
        col = MagicMock()
        col.dropna.return_value.astype.return_value.tolist.return_value = [18.0]
        df.iloc = MagicMock()
        df.iloc.__getitem__ = MagicMock(return_value=col)
        return df

    with patch.object(mod, "_akshare", MagicMock(return_value=MagicMock(stock_zh_valuation_baidu=single_df))):
        result = mod.valuation_percentile("600519")

    pe = result["metrics"]["pe_ttm"]
    assert pe["current"] == 18.0
    assert pe["n"] == 1
    assert pe["percentile"] == 0.0
    assert pe["min"] == pe["max"] == 18.0
    assert pe["p20"] == pe["p50"] == pe["p80"] == 18.0


def test_dependency_missing():
    import nasdx.valuation as mod
    with patch.dict("sys.modules", {"akshare": None}):
        with pytest.raises(mod.DependencyMissing):
            mod._akshare()


def test_percentile_interp_boundary():
    mod = _make_module(FakeAkshare())
    assert mod._percentile_interp([], 0.5) == 0.0
    assert mod._percentile_interp([42.0], 0.5) == 42.0
    assert mod._percentile_interp([10.0, 20.0], 0.0) == 10.0
    assert mod._percentile_interp([10.0, 20.0], 1.0) == 20.0
    assert mod._percentile_interp([10.0, 20.0], 0.5) == 15.0

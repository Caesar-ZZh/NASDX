"""加密货币行情模块合同测试

测试重点：
  - Binance / OKX 响应解析正确性
  - 限频退避机制
  - 合规标注存在
  - 缓存行为

不联网，全量 mock。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# 确保 nasdx 可导入
import nasdx.crypto as crypto_module


# ─── Fixtures ────────────────────────────────────────────────────────────────

BINANCE_TICKER_MOCK = {
    "symbol": "BTCUSDT",
    "priceChange": "123.45",
    "priceChangePercent": "0.50",
    "weightedAvgPrice": "65000.00",
    "prevClosePrice": "64800.00",
    "lastPrice": "65123.45",
    "lastQty": "0.001",
    "bidPrice": "65120.00",
    "askPrice": "65125.00",
    "openPrice": "64800.00",
    "highPrice": "65500.00",
    "lowPrice": "64700.00",
    "volume": "12345.67",
    "quoteVolume": "800000000.00",
    "openTime": 1700000000000,
    "closeTime": 1700003600000,
}

OKX_TICKER_MOCK = {
    "code": "0",
    "msg": "",
    "data": [
        {
            "instId": "BTC-USDT",
            "last": "65123.45",
            "lastSz": "0.001",
            "open24h": "64800.00",
            "high24h": "65500.00",
            "low24h": "64700.00",
            "vol24h": "12345.67",
            "volCcy24h": "800000000.00",
            "sodUtc0": "64800.00",
            "sodUtc8": "64800.00",
        }
    ],
}

BINANCE_KLINES_MOCK = [
    [1700000000000, "64800.00", "65000.00", "64700.00", "65100.00", "100.00", 0],
    [1700003600000, "65100.00", "65200.00", "64900.00", "65123.45", "120.00", 0],
]

OKX_KLINES_MOCK = {
    "code": "0",
    "msg": "",
    "data": [
        ["1700003600000", "65100.00", "65200.00", "64900.00", "65123.45", "120.00", "0", "0"],
        ["1700000000000", "64800.00", "65000.00", "64700.00", "65100.00", "100.00", "0", "0"],
    ],
}


@pytest.fixture
def client():
    return crypto_module.CryptoDataClient()


@pytest.fixture
def patched_cache():
    """清除缓存，避免测试间污染"""
    with patch.object(crypto_module, "_cache_get", return_value=None):
        with patch.object(crypto_module, "_cache_set") as mock_set:
            yield mock_set


# ─── 合规标注测试 ────────────────────────────────────────────────────────────

def test_compliance_note_present(client, patched_cache):
    """所有返回数据必须包含合规标注"""
    with patch.object(crypto_module.requests.Session, "get") as mock_get:
        mock_get.return_value.json.return_value = BINANCE_TICKER_MOCK
        mock_get.return_value.status_code = 200

        result = client.get_ticker_24hr(crypto_module.Exchange.BINANCE, symbol="BTCUSDT")

        assert result.get("compliance_note") == crypto_module.COMPLIANCE_NOTE


def test_ticker_contains_data(client, patched_cache):
    """ticker 数据必须包含交易所、数据、时间戳"""
    with patch.object(crypto_module.requests.Session, "get") as mock_get:
        mock_get.return_value.json.return_value = BINANCE_TICKER_MOCK
        mock_get.return_value.status_code = 200

        result = client.get_ticker_24hr(crypto_module.Exchange.BINANCE, symbol="BTCUSDT")

        assert result["exchange"] == "binance"
        assert isinstance(result["data"], list)
        assert "fetched_at" in result


# ─── Binance 解析测试 ────────────────────────────────────────────────────────

def test_binance_ticker_parse(client, patched_cache):
    """Binance ticker 响应解析正确"""
    with patch.object(crypto_module.requests.Session, "get") as mock_get:
        mock_get.return_value.json.return_value = BINANCE_TICKER_MOCK
        mock_get.return_value.status_code = 200

        result = client.get_ticker_24hr(crypto_module.Exchange.BINANCE, symbol="BTCUSDT")

        assert len(result["data"]) == 1
        item = result["data"][0]
        assert item["symbol"] == "BTCUSDT"
        assert float(item["lastPrice"]) == 65123.45
        assert float(item["quoteVolume"]) == 800000000.00


def test_binance_klines_parse(client, patched_cache):
    """Binance klines 响应解析正确"""
    with patch.object(crypto_module.requests.Session, "get") as mock_get:
        mock_get.return_value.json.return_value = BINANCE_KLINES_MOCK
        mock_get.return_value.status_code = 200

        result = client.get_klines(crypto_module.Exchange.BINANCE, symbol="BTCUSDT", interval="1h", limit=2)

        assert len(result["klines"]) == 2
        assert result["klines"][0]["open"] == 64800.0
        assert result["klines"][0]["high"] == 65000.0
        assert result["klines"][0]["low"] == 64700.0
        assert result["klines"][0]["close"] == 65100.0
        assert result["klines"][0]["volume"] == 100.0


# ─── OKX 解析测试 ────────────────────────────────────────────────────────────

def test_okx_ticker_parse(client, patched_cache):
    """OKX ticker 响应解析正确"""
    with patch.object(crypto_module.requests.Session, "get") as mock_get:
        mock_get.return_value.json.return_value = OKX_TICKER_MOCK
        mock_get.return_value.status_code = 200

        result = client.get_ticker_24hr(crypto_module.Exchange.OKX, symbol="BTC-USDT")

        assert len(result["data"]) == 1
        item = result["data"][0]
        assert item["instId"] == "BTC-USDT"
        assert float(item["last"]) == 65123.45
        assert float(item["volCcy24h"]) == 800000000.00


def test_okx_klines_parse(client, patched_cache):
    """OKX klines 响应解析正确，时间顺序应为升序（旧→新）"""
    with patch.object(crypto_module.requests.Session, "get") as mock_get:
        mock_get.return_value.json.return_value = OKX_KLINES_MOCK
        mock_get.return_value.status_code = 200

        result = client.get_klines(crypto_module.Exchange.OKX, symbol="BTC-USDT", interval="1H", limit=2)

        assert len(result["klines"]) == 2
        # OKX 原始数据最新在前，解析后应反转
        assert result["klines"][0]["open_time"] == "1700000000000"
        assert result["klines"][1]["open_time"] == "1700003600000"


# ─── 限频退避测试 ────────────────────────────────────────────────────────────

def test_rate_limiter_acquisition(client):
    """限流器应等待最小间隔后放行"""
    limiter = crypto_module.RateLimiter(min_interval=0.1)

    start = time.time()
    limiter.acquire()
    first = time.time()
    limiter.acquire()
    second = time.time()

    assert first - start < 0.05  # 第一次应立即通过
    assert second - first >= 0.09  # 第二次应等待


def test_ticker_24hr_respects_rate_limit(client, patched_cache):
    """连续调用 ticker 应遵守限流间隔"""
    call_times: list[float] = []

    def mock_get(url, **kwargs):
        call_times.append(time.time())
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = BINANCE_TICKER_MOCK
        return resp

    with patch.object(crypto_module.requests.Session, "get", side_effect=mock_get):
        client.get_ticker_24hr(crypto_module.Exchange.BINANCE, symbol="BTCUSDT")
        client.get_ticker_24hr(crypto_module.Exchange.BINANCE, symbol="ETHUSDT")

    assert len(call_times) == 2
    assert call_times[1] - call_times[0] >= 0.4  # Binance 限流 0.5s


# ─── 缓存测试 ────────────────────────────────────────────────────────────────

def test_ticker_cache(client):
    """相同请求应命中缓存"""
    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = BINANCE_TICKER_MOCK
        return resp

    with patch.object(crypto_module.requests.Session, "get", side_effect=mock_get):
        with patch.object(crypto_module, "_cache_get", return_value={"cached": True}) as mock_cache_get:
            result = client.get_ticker_24hr(crypto_module.Exchange.BINANCE, symbol="BTCUSDT")
            assert result.get("cached") is True
            mock_cache_get.assert_called_once()
            assert call_count == 0  # 不应发起网络请求


def test_klines_cache(client):
    """K 线相同请求应命中缓存"""
    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = BINANCE_KLINES_MOCK
        return resp

    with patch.object(crypto_module.requests.Session, "get", side_effect=mock_get):
        with patch.object(crypto_module, "_cache_get", return_value={"cached": True}) as mock_cache_get:
            result = client.get_klines(crypto_module.Exchange.BINANCE, symbol="BTCUSDT")
            assert result.get("cached") is True
            mock_cache_get.assert_called_once()
            assert call_count == 0


# ─── 市场聚合测试 ───────────────────────────────────────────────────────────

def test_market_overview_has_no_symbol_list(client, patched_cache):
    """市场概览只聚合计数/成交额，不返回交易对名单或排名。"""
    multi_ticker = [BINANCE_TICKER_MOCK, {**BINANCE_TICKER_MOCK, "symbol": "ETHUSDT", "quoteVolume": "500000000.00"}]

    with patch.object(crypto_module.requests.Session, "get") as mock_get:
        mock_get.return_value.json.return_value = multi_ticker
        mock_get.return_value.status_code = 200

        result = client.get_market_overview(crypto_module.Exchange.BINANCE)

        assert result["exchange"] == "binance"
        assert result["compliance_note"] == crypto_module.COMPLIANCE_NOTE
        assert result["instrument_count"] == 2
        assert result["total_quote_volume_24h"] == 1300000000.0
        assert "symbols" not in result


# ─── 便捷函数测试 ────────────────────────────────────────────────────────────

def test_get_crypto_ticker_wrapper():
    """便捷函数应正确路由到客户端"""
    with patch.object(crypto_module.CryptoDataClient, "get_ticker_24hr") as mock_method:
        mock_method.return_value = {"exchange": "binance", "data": [], "compliance_note": crypto_module.COMPLIANCE_NOTE}

        result = crypto_module.get_crypto_ticker("binance", symbol="BTCUSDT")

        mock_method.assert_called_once()
        assert "compliance_note" in result


def test_get_crypto_klines_wrapper():
    """便捷函数应正确路由到客户端"""
    with patch.object(crypto_module.CryptoDataClient, "get_klines") as mock_method:
        mock_method.return_value = {"exchange": "okx", "klines": [], "compliance_note": crypto_module.COMPLIANCE_NOTE}

        result = crypto_module.get_crypto_klines("okx", symbol="BTC-USDT", interval="1H")

        mock_method.assert_called_once()
        assert "compliance_note" in result


# ─── 异常处理测试 ────────────────────────────────────────────────────────────

def test_invalid_exchange(client):
    """不支持的交易所应抛出 ValueError"""
    with pytest.raises(ValueError, match="Unsupported exchange"):
        client.get_ticker_24hr("unknown_exchange")  # type: ignore


def test_network_error_handling(client, patched_cache):
    """网络错误应抛出异常而非静默失败"""
    import requests as req

    with patch.object(crypto_module.requests.Session, "get") as mock_get:
        mock_get.side_effect = req.exceptions.ConnectionError("Connection refused")

        with pytest.raises((req.exceptions.ConnectionError, Exception)):
            client.get_ticker_24hr(crypto_module.Exchange.BINANCE, symbol="BTCUSDT")

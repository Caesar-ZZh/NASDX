"""加密货币行情模块（Binance / OKX 公开行情）

仅做行情展示，不做交易。
所有数据来自境外交易所公开 API，仅供研究使用。
遵守「零标的」合规红线：只呈现客观数据，不推荐、不预测、不排名。

API 限频说明：
  - Binance: 公开接口约 10-20 req/min，需退避
  - OKX: 公开接口约 20-60 req/min，相对宽松
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import requests

from quant.data import _cache_get, _cache_set, _http_get

logger = logging.getLogger(__name__)

# 合规标注：统一附加到所有返回数据
COMPLIANCE_NOTE = "境外交易所，仅供研究"


class Exchange(str, Enum):
    BINANCE = "binance"
    OKX = "okx"


@dataclass
class RateLimiter:
    """简单令牌桶限流器，用于 Binance/OKX 公开 API 限频退避"""

    min_interval: float = 1.0  # 默认最小请求间隔（秒）
    last_request_time: float = field(default=0.0, repr=False)

    def acquire(self, interval: float | None = None) -> None:
        """等待直到满足最小间隔"""
        wait = (interval or self.min_interval) - (time.time() - self.last_request_time)
        if wait > 0:
            time.sleep(wait)
        self.last_request_time = time.time()


class CryptoDataClient:
    """加密货币公开行情客户端

    支持 Binance 与 OKX 两个交易所的公开行情接口（无需 API Key）。
    复用 quant.data 的请求/缓存范式。
    """

    def __init__(self) -> None:
        self._binance_limiter = RateLimiter(min_interval=0.5)
        self._okx_limiter = RateLimiter(min_interval=0.2)
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "NASDX-Crypto/1.0 (research-only)",
            "Accept": "application/json",
        })

    def _get(self, url: str, params: dict[str, Any] | None = None, limiter: RateLimiter | None = None) -> dict[str, Any]:
        """带限流的 HTTP GET，复用 _http_get 缓存"""
        if limiter:
            limiter.acquire()
        return _http_get(url, params=params, session=self._session)

    def get_ticker_24hr(
        self,
        exchange: Exchange,
        symbol: str | None = None,
        cache_ttl: int = 300,
    ) -> dict[str, Any]:
        """获取 24h 行情

        Args:
            exchange: 交易所
            symbol: 交易对，如 BTCUSDT；None 返回全部
            cache_ttl: 缓存 TTL（秒），默认 5 分钟

        Returns:
            行情数据 dict，包含合规标注
        """
        cache_key = f"crypto_ticker_{exchange.value}_{symbol or 'all'}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        if exchange == Exchange.BINANCE:
            base_url = "https://api.binance.com/api/v3/ticker/24hr"
            params: dict[str, Any] = {"symbol": symbol} if symbol else {}
            data = self._get(base_url, params=params, limiter=self._binance_limiter)
            result = data if isinstance(data, list) else [data]
        elif exchange == Exchange.OKX:
            base_url = "https://www.okx.com/api/v5/market/tickers"
            params = {"instType": "SPOT"}
            if symbol:
                # OKX symbol 格式：BTC-USDT
                inst_id = symbol.replace("USDT", "-USDT").replace("BTC", "BTC").replace("ETH", "ETH")
                params["instId"] = inst_id
            data = self._get(base_url, params=params, limiter=self._okx_limiter)
            result = data.get("data", []) if isinstance(data, dict) else []
        else:
            raise ValueError(f"Unsupported exchange: {exchange}")

        payload: dict[str, Any] = {
            "exchange": exchange.value,
            "compliance_note": COMPLIANCE_NOTE,
            "data": result,
            "fetched_at": time.time(),
        }
        _cache_set(cache_key, payload, ttl=cache_ttl)
        return payload

    def get_klines(
        self,
        exchange: Exchange,
        symbol: str,
        interval: str = "1h",
        limit: int = 100,
        cache_ttl: int = 600,
    ) -> dict[str, Any]:
        """获取 K 线数据

        Args:
            exchange: 交易所
            symbol: 交易对，如 BTCUSDT
            interval: K 线周期，Binance: 1m/5m/1h/1d；OKX: 1m/5m/1H/1D
            limit: 返回根数，默认 100
            cache_ttl: 缓存 TTL（秒），默认 10 分钟

        Returns:
            K 线数据 dict，包含合规标注
        """
        cache_key = f"crypto_kline_{exchange.value}_{symbol}_{interval}_{limit}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        if exchange == Exchange.BINANCE:
            base_url = "https://api.binance.com/api/v3/klines"
            params = {"symbol": symbol, "interval": interval, "limit": limit}
            data = self._get(base_url, params=params, limiter=self._binance_limiter)
            # Binance klines 返回扁平列表，每条 7 个字段
            klines = []
            if isinstance(data, list):
                for row in data:
                    if len(row) >= 6:
                        klines.append({
                            "open_time": row[0],
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                        })
            result = klines
        elif exchange == Exchange.OKX:
            base_url = "https://www.okx.com/api/v5/market/candles"
            okx_symbol = symbol.replace("USDT", "-USDT")
            okx_interval = interval.replace("m", "m").replace("h", "H").replace("d", "D")
            params = {"instId": okx_symbol, "bar": okx_interval, "limit": str(limit)}
            data = self._get(base_url, params=params, limiter=self._okx_limiter)
            rows = data.get("data", []) if isinstance(data, dict) else []
            result = []
            for row in reversed(rows):  # OKX 返回最新在前，反转
                if len(row) >= 8:
                    result.append({
                        "open_time": row[0],
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    })
        else:
            raise ValueError(f"Unsupported exchange: {exchange}")

        payload: dict[str, Any] = {
            "exchange": exchange.value,
            "symbol": symbol,
            "interval": interval,
            "compliance_note": COMPLIANCE_NOTE,
            "klines": result,
            "fetched_at": time.time(),
        }
        _cache_set(cache_key, payload, ttl=cache_ttl)
        return payload

    def get_top_symbols(
        self,
        exchange: Exchange = Exchange.BINANCE,
        cache_ttl: int = 300,
    ) -> dict[str, Any]:
        """获取交易量 Top 交易对（客观数据展示，不排名推荐）

        Returns:
            按 24h 成交量排序的交易对列表，包含合规标注
        """
        ticker_data = self.get_ticker_24hr(exchange, cache_ttl=cache_ttl)
        symbols = ticker_data.get("data", [])

        # 过滤 USDT 交易对，按成交量降序（仅展示，非推荐）
        filtered = [
            s for s in symbols
            if isinstance(s, dict) and s.get("symbol", "").endswith("USDT")
        ]
        sorted_symbols = sorted(filtered, key=lambda x: float(x.get("quoteVolume", 0) or 0), reverse=True)[:20]

        return {
            "exchange": exchange.value,
            "compliance_note": COMPLIANCE_NOTE,
            "symbols": sorted_symbols,
            "note": "按 24h 成交量排序展示，不构成任何投资建议",
        }


def get_crypto_ticker(exchange: str, symbol: str | None = None) -> dict[str, Any]:
    """便捷函数：获取加密货币 24h 行情

    Args:
        exchange: 交易所名称（binance / okx）
        symbol: 交易对

    Returns:
        行情数据 dict
    """
    client = CryptoDataClient()
    ex = Exchange.BINANCE if exchange.lower() == "binance" else Exchange.OKX
    return client.get_ticker_24hr(ex, symbol=symbol)


def get_crypto_klines(exchange: str, symbol: str, interval: str = "1h", limit: int = 100) -> dict[str, Any]:
    """便捷函数：获取加密货币 K 线

    Args:
        exchange: 交易所名称（binance / okx）
        symbol: 交易对
        interval: K 线周期
        limit: 根数

    Returns:
        K 线数据 dict
    """
    client = CryptoDataClient()
    ex = Exchange.BINANCE if exchange.lower() == "binance" else Exchange.OKX
    return client.get_klines(ex, symbol=symbol, interval=interval, limit=limit)

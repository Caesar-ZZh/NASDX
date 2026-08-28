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
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 合规标注：统一附加到所有返回数据
COMPLIANCE_NOTE = "境外交易所，仅供研究"
_CACHE_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(key: str) -> Any | None:
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() >= expires_at:
            _CACHE.pop(key, None)
            return None
        return value


def _cache_set(key: str, value: Any, ttl: int = _CACHE_TTL_SECONDS) -> None:
    if value in (None, [], {}):
        return
    effective_ttl = min(max(int(ttl), 1), _CACHE_TTL_SECONDS)
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + effective_ttl, value)


def _http_get(
    url: str,
    *,
    params: dict[str, Any] | None,
    session: requests.Session,
    timeout: int = 15,
) -> Any:
    response = session.get(url, params=params or {}, timeout=timeout)
    response.raise_for_status()
    return response.json()


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
        effective_interval = self.min_interval if interval is None else interval
        wait = effective_interval - (time.monotonic() - self.last_request_time)
        if wait > 0:
            time.sleep(wait)
        self.last_request_time = time.monotonic()


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
        exchange: Exchange | str,
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
        exchange = _coerce_exchange(exchange)
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
            base_url = "https://www.okx.com/api/v5/market/ticker" if symbol else "https://www.okx.com/api/v5/market/tickers"
            params = {"instType": "SPOT"} if not symbol else {}
            if symbol:
                params["instId"] = _okx_symbol(symbol)
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
        if result:
            _cache_set(cache_key, payload, ttl=cache_ttl)
        return payload

    def get_klines(
        self,
        exchange: Exchange | str,
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
        exchange = _coerce_exchange(exchange)
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
            okx_symbol = _okx_symbol(symbol)
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
        if result:
            _cache_set(cache_key, payload, ttl=cache_ttl)
        return payload

    def get_market_overview(
        self,
        exchange: Exchange | str = Exchange.BINANCE,
        cache_ttl: int = 300,
    ) -> dict[str, Any]:
        """聚合 24h 市场概览，不返回交易对名单或排名。"""
        exchange = _coerce_exchange(exchange)
        ticker_data = self.get_ticker_24hr(exchange, cache_ttl=cache_ttl)
        symbols = ticker_data.get("data", [])
        filtered = [item for item in symbols if isinstance(item, dict)]
        quote_volume = sum(
            _safe_float(item.get("quoteVolume") or item.get("volCcy24h")) for item in filtered
        )
        return {
            "exchange": exchange.value,
            "compliance_note": COMPLIANCE_NOTE,
            "instrument_count": len(filtered),
            "total_quote_volume_24h": round(quote_volume, 4),
            "note": "仅为公开行情聚合，不含交易对名单或排名",
        }


def _okx_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().upper()
    if "-" in normalized:
        return normalized
    return f"{normalized[:-4]}-USDT" if normalized.endswith("USDT") else normalized


def _coerce_exchange(exchange: Exchange | str) -> Exchange:
    try:
        return exchange if isinstance(exchange, Exchange) else Exchange(str(exchange).lower())
    except ValueError:
        raise ValueError(f"Unsupported exchange: {exchange}") from None


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def get_crypto_ticker(exchange: str, symbol: str | None = None) -> dict[str, Any]:
    """便捷函数：获取加密货币 24h 行情

    Args:
        exchange: 交易所名称（binance / okx）
        symbol: 交易对

    Returns:
        行情数据 dict
    """
    client = CryptoDataClient()
    ex = Exchange(exchange.lower())
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
    ex = Exchange(exchange.lower())
    return client.get_klines(ex, symbol=symbol, interval=interval, limit=limit)

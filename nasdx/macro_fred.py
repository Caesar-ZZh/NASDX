# -*- coding: utf-8 -*-
"""FRED (Federal Reserve Economic Data) 宏观指标拉取模块。

仅呈现客观数据（GDP、CPI、失业率、利率等），不提供任何买卖推荐、
预测或个股/指数排名。

API 文档: https://fred.stlouisfed.org/docs/api/api_key.html
申请免费 Key: https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
_CONFIGURED_CACHE = os.environ.get("NASDX_DATA_CACHE")
if _CONFIGURED_CACHE:
    _CACHE_DIR = Path(_CONFIGURED_CACHE).expanduser() / "fred"
elif os.environ.get("LOCALAPPDATA"):
    _CACHE_DIR = Path(os.environ["LOCALAPPDATA"]) / "NASDX" / "cache" / "fred"
else:
    _CACHE_DIR = Path.home() / ".cache" / "nasdx" / "fred"
_DEFAULT_TTL_SEC = 300
_USER_AGENT = "NASDX-MacroFred/1.0"

# 常用宏观指标系列 ID（可按需扩展）
SERIES_UIDS = {
    "us_gdp": "GDP",
    "us_cpi": "CPIAUCSL",          # 消费者物价指数（全美城市）
    "us_cpi_core": "CPILFESL",     # 核心 CPI
    "us_unemployment": "UNRATE",   # 失业率
    "us_fed_funds": "FEDFUNDS",    # 联邦基金利率
    "us_10y_treasury": "DGS10",   # 10 年期美债收益率
    "us_2y_treasury": "DGS2",
    "us_30y_treasury": "DGS30",
    "us_m2": "M2SL",               # M2 货币供应量
    "us_pce": "PCEPI",             # PCE 物价指数
    "us_trade_balance": "BOPGSTB",
    "us_consumer_confidence": "UMCSENT",
    "us_manufacturing_pmi": "MANEMP",
    "us_housing_starts": "HOUST",
    "us_new_home_sales": "NHPIS",
    "us_initial_claims": "ICSA",
    "us_nonfarm_payrolls": "PAYEMS",
}

# ---------------------------------------------------------------------------
# 缓存工具
# ---------------------------------------------------------------------------

def _read_cache(key: str) -> Optional[Any]:
    """读取本地 JSON 缓存（含 TTL 检查）。"""
    p = _CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8")
        obj = json.loads(raw)
        if obj.get("expires_at", 0) < time.time():
            p.unlink(missing_ok=True)
            return None
        return obj.get("value")
    except Exception:
        return None


def _write_cache(key: str, value: Any, ttl_sec: int = _DEFAULT_TTL_SEC) -> None:
    """写入本地 JSON 缓存。"""
    if value in (None, [], {}):
        return
    ttl_sec = min(max(int(ttl_sec), 1), _DEFAULT_TTL_SEC)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _CACHE_DIR / f"{key}.json"
    payload = {
        "value": value,
        "written_at": datetime.utcnow().isoformat(timespec="seconds"),
        "expires_at": time.time() + ttl_sec,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------------------------------------------------------------------
# API 客户端
# ---------------------------------------------------------------------------

class FredClient:
    """FRED API 客户端。

    零标的红线：本类仅负责查询与返回原始数据；不衍生任何交易信号。
    """

    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.environ.get("FRED_API_KEY", "").strip()

    # -- 属性辅助 ----------------------------------------------------------

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    @property
    def missing_key_notice(self) -> str:
        if self.is_available:
            return ""
        return (
            "[FRED] FRED_API_KEY 未设置，宏观数据拉取已降级。"
            "\n  申请免费 Key: https://fred.stlouisfed.org/docs/api/api_key.html"
            "\n  设置示例: export FRED_API_KEY='YOUR_KEY'"
        )

    # -- 观测值查询 --------------------------------------------------------

    def get_observations(
        self,
        series_id: str,
        *,
        file_type: str = "json",
        sort_order: str = "asc",
        limit: int = 0,  # 0 = 不限
        low: Optional[str] = None,
        high: Optional[str] = None,
        cache_ttl: int = _DEFAULT_TTL_SEC,
    ) -> dict[str, Any]:
        """拉取单系列观测值。

        返回结构（与 FRED API 一致）:
        {
            "observations": [{"date": ..., "value": ...}, ...],
            "series_id": str,
            "request_time": float,
        }
        失败或 key 缺失时返回带 error 字段的降级结果。
        """
        cache_key = f"obs_{series_id}_{limit}_{low or ''}_{high or ''}"
        cached = _read_cache(cache_key)
        if cached is not None:
            return cached

        if not self.is_available:
            result = self._degraded_result(series_id)
            return result

        params = [
            ("series_id", series_id),
            ("api_key", self.api_key),
            ("file_type", file_type),
            ("sort_order", sort_order),
            ("sort_type", "asc"),
        ]
        if limit:
            params.append(("limit", str(limit)))
        if low:
            params.append(("low", low))
        if high:
            params.append(("high", high))

        qs = "&".join(f"{k}={v}" for k, v in params)
        url = f"{self.BASE_URL}/series/observations?{qs}"

        try:
            req = Request(url, headers={"User-Agent": _USER_AGENT})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result = {
                "observations": data.get("observations", []),
                "series_id": series_id,
                "request_time": time.time(),
                "error_code": data.get("error_code"),
                "error_message": data.get("error_message"),
            }
            if result["observations"]:
                _write_cache(cache_key, result, ttl_sec=cache_ttl)
            return result
        except (URLError, HTTPError, TimeoutError) as exc:
            return {
                "observations": [],
                "series_id": series_id,
                "request_time": time.time(),
                "error_code": -1,
                "error_message": str(exc),
            }

    # -- 系列元数据 --------------------------------------------------------

    def get_series_info(
        self,
        series_id: str,
        *,
        file_type: str = "json",
        cache_ttl: int = _DEFAULT_TTL_SEC,
    ) -> dict[str, Any]:
        """获取系列元数据（标题、频次、单位、季节性等）。"""
        cache_key = f"info_{series_id}"
        cached = _read_cache(cache_key)
        if cached is not None:
            return cached

        if not self.is_available:
            return self._degraded_result(series_id)

        params = [
            ("series_id", series_id),
            ("api_key", self.api_key),
            ("file_type", file_type),
        ]
        qs = "&".join(f"{k}={v}" for k, v in params)
        url = f"{self.BASE_URL}/series/info?{qs}"

        try:
            req = Request(url, headers={"User-Agent": _USER_AGENT})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            series_items = data.get("series") or []
            series = series_items[0] if series_items else {}
            result = {
                "series_id": series_id,
                "title": series.get("title"),
                "frequency": series.get("frequency"),
                "seasonal_adjustment": series.get("seasonal_adjustment"),
                "units": series.get("units"),
                "last_updated": series.get("last_updated"),
                "request_time": time.time(),
                "error_code": data.get("error_code"),
                "error_message": data.get("error_message"),
            }
            if series:
                _write_cache(cache_key, result, ttl_sec=cache_ttl)
            return result
        except Exception as exc:
            return {
                "series_id": series_id,
                "error": True,
                "error_message": str(exc),
                "request_time": time.time(),
            }

    # -- 批量查询 ----------------------------------------------------------

    def get_batch_observations(
        self,
        series_ids: list[str],
        *,
        cache_ttl: int = _DEFAULT_TTL_SEC,
    ) -> dict[str, dict[str, Any]]:
        """批量获取多个系列观测值（串行，避免限流）。"""
        results: dict[str, dict[str, Any]] = {}
        for sid in series_ids:
            results[sid] = self.get_observations(sid, cache_ttl=cache_ttl)
            time.sleep(0.25)  # 友好限流：≥0.25s/次
        return results

    def get_popular_series(self, count: int = 10) -> list[dict[str, Any]]:
        """拉取 FRED 热门系列列表（公开端点，无需 key 可访问部分信息）。"""
        if not self.is_available:
            return []
        params = [
            ("api_key", self.api_key),
            ("file_type", "json"),
            ("orthography", "display"),
            ("limit", str(count)),
        ]
        qs = "&".join(f"{k}={v}" for k, v in params)
        url = f"{self.BASE_URL}/series/popular?{qs}"
        try:
            req = Request(url, headers={"User-Agent": _USER_AGENT})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("series", [])
        except Exception:
            return []

    # -- 辅助 --------------------------------------------------------------

    @staticmethod
    def _degraded_result(series_id: str) -> dict[str, Any]:
        return {
            "observations": [],
            "series_id": series_id,
            "request_time": time.time(),
            "error": True,
            "error_code": -99,
            "error_message": "FRED_API_KEY 缺失，数据已降级。请设置环境变量后重试。",
            "degraded": True,
        }

    @classmethod
    def uid_map(cls) -> dict[str, str]:
        return dict(SERIES_UIDS)


# ---------------------------------------------------------------------------
# 模块级便捷函数（面向脚本/回测预处理）
# ---------------------------------------------------------------------------

_fred_instance: Optional[FredClient] = None


def get_fred() -> FredClient:
    global _fred_instance
    if _fred_instance is None:
        _fred_instance = FredClient()
    return _fred_instance


def fred_observations(
    series_id: str,
    *,
    cache_ttl: int = _DEFAULT_TTL_SEC,
    limit: int = 0,
    low: Optional[str] = None,
    high: Optional[str] = None,
) -> dict[str, Any]:
    """便捷拉取观测值。"""
    return get_fred().get_observations(
        series_id,
        limit=limit,
        low=low,
        high=high,
        cache_ttl=cache_ttl,
    )


def fred_series_info(series_id: str, *, cache_ttl: int = _DEFAULT_TTL_SEC) -> dict[str, Any]:
    """便捷拉取系列元数据。"""
    return get_fred().get_series_info(series_id, cache_ttl=cache_ttl)


def fred_status() -> dict[str, Any]:
    """返回当前客户端可用性状态（供诊断/UI 展示）。"""
    c = get_fred()
    return {
        "available": c.is_available,
        "notice": c.missing_key_notice,
        "cached_count": len(list(_CACHE_DIR.glob("*.json"))) if _CACHE_DIR.exists() else 0,
    }

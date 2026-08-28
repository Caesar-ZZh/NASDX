# -*- coding: utf-8 -*-
"""nasdx.macro_fred 合约测试。

覆盖：
- key 缺失时优雅降级
- 观测值查询
- 系列元数据查询
- 缓存读写
- 批量查询限流
- 热门系列端点

不联网：全部使用 unittest.mock。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 确保 nasdx 在 path 中
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nasdx.macro_fred import (
    FredClient,
    _CACHE_DIR,
    _DEFAULT_TTL_SEC,
    _read_cache,
    _write_cache,
    fred_observations,
    fred_series_info,
    fred_status,
    get_fred,
    SERIES_UIDS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_fred_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nasdx.macro_fred as module

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(module, "_CACHE_DIR", tmp_path / "fred")
    monkeypatch.setattr(module, "_fred_instance", None)


@pytest.fixture
def sample_observations() -> list[dict]:
    return [
        {"date": "2023-01-01", "value": "100.5", "realtime_start": "2024-01-01", "realtime_end": "2024-01-01"},
        {"date": "2023-02-01", "value": "101.2", "realtime_start": "2024-01-01", "realtime_end": "2024-01-01"},
    ]


@pytest.fixture
def sample_series_info() -> dict:
    return {
        "id": "CPIAUCSL",
        "series_id": "CPIAUCSL",
        "title": "Consumer Price Index for All Urban Consumers: All Items",
        "frequency": "Monthly",
        "seasonal_adjustment": "Not Seasonally Adjusted",
        "units": "Index 1982-84=100",
        "last_updated": "2024-01-15",
    }


@pytest.fixture
def valid_client() -> FredClient:
    test_value = "_".join(("TEST", "VALUE", "123"))
    with patch.dict(os.environ, {"FRED_API_KEY": test_value}):
        client = FredClient()
        yield client


# ---------------------------------------------------------------------------
# 1. Key 缺失降级
# ---------------------------------------------------------------------------

class TestKeyMissingDegradation:

    def test_client_unavailable_when_no_key(self) -> None:
        c = FredClient()
        assert c.is_available is False
        assert "FRED_API_KEY" in c.missing_key_notice

    def test_degraded_result_structure(self) -> None:
        c = FredClient()
        result = c.get_observations("GDP")
        assert result["series_id"] == "GDP"
        assert result["observations"] == []
        assert result["error_code"] == -99
        assert result["degraded"] is True

    def test_series_info_degraded(self) -> None:
        c = FredClient()
        result = c.get_series_info("UNRATE")
        assert result["series_id"] == "UNRATE"
        assert "error" in result

    def test_fred_status_shows_unavailable(self) -> None:
        status = fred_status()
        assert status["available"] is False
        assert status["notice"] != ""


# ---------------------------------------------------------------------------
# 2. 观测值查询（mock HTTP）
# ---------------------------------------------------------------------------

class TestGetObservations:

    def _make_resp(self, body: dict) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_success_parse(self, valid_client: FredClient, sample_observations: list) -> None:
        api_resp = {
            "observations": sample_observations,
            "series_id": "CPIAUCSL",
            "error_code": None,
            "error_message": None,
        }
        with patch("nasdx.macro_fred.urlopen", return_value=self._make_resp(api_resp)):
            result = valid_client.get_observations("CPIAUCSL")
        assert result["series_id"] == "CPIAUCSL"
        assert len(result["observations"]) == 2
        assert result["observations"][0]["date"] == "2023-01-01"
        assert result["error_code"] is None

    def test_limit_parameter_passthrough(self, valid_client: FredClient) -> None:
        captured_url: list[str] = []
        def fake_urlopen(req, timeout=15):
            captured_url.append(req.full_url)
            return self._make_resp({"observations": [], "series_id": "GDP", "error_code": None, "error_message": None})

        with patch("nasdx.macro_fred.urlopen", side_effect=fake_urlopen):
            valid_client.get_observations("GDP", limit=100)
        assert any("limit=100" in u for u in captured_url)

    def test_low_high_filters(self, valid_client: FredClient) -> None:
        captured_url: list[str] = []
        def fake_urlopen(req, timeout=15):
            captured_url.append(req.full_url)
            return self._make_resp({"observations": [], "series_id": "X", "error_code": None, "error_message": None})

        with patch("nasdx.macro_fred.urlopen", side_effect=fake_urlopen):
            valid_client.get_observations("X", low="2020-01-01", high="2023-12-31")
        qs = captured_url[0]
        assert "low=2020-01-01" in qs
        assert "high=2023-12-31" in qs

    def test_http_error_returns_empty(self, valid_client: FredClient) -> None:
        from urllib.error import HTTPError
        with patch("nasdx.macro_fred.urlopen", side_effect=HTTPError("u", 500, "err", None, None)):
            result = valid_client.get_observations("GDP")
        assert result["observations"] == []
        assert result["error_code"] == -1

    def test_network_error_returns_empty(self, valid_client: FredClient) -> None:
        from urllib.error import URLError
        with patch("nasdx.macro_fred.urlopen", side_effect=URLError("no internet")):
            result = valid_client.get_observations("GDP")
        assert result["observations"] == []
        assert result["error_code"] == -1


# ---------------------------------------------------------------------------
# 3. 系列元数据查询
# ---------------------------------------------------------------------------

class TestGetSeriesInfo:

    def _make_resp(self, body: dict) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_success_parse(self, valid_client: FredClient, sample_series_info: dict) -> None:
        api_resp = {"series": [sample_series_info], "error_code": None, "error_message": None}
        with patch("nasdx.macro_fred.urlopen", return_value=self._make_resp(api_resp)):
            result = valid_client.get_series_info("CPIAUCSL")
        assert result["series_id"] == "CPIAUCSL"
        assert result["title"] == sample_series_info["title"]
        assert result["frequency"] == "Monthly"
        assert result["units"] == "Index 1982-84=100"

    def test_missing_key_returns_degraded(self, valid_client: FredClient) -> None:
        # 临时清除 key
        original = valid_client.api_key
        valid_client.api_key = ""
        try:
            result = valid_client.get_series_info("GDP")
            assert result.get("error") is not None
        finally:
            valid_client.api_key = original


# ---------------------------------------------------------------------------
# 4. 缓存读写
# ---------------------------------------------------------------------------

class TestCache:

    def test_write_and_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("nasdx.macro_fred._CACHE_DIR", tmp_path)
        _write_cache("test_key", {"a": 1}, ttl_sec=60)
        val = _read_cache("test_key")
        assert val == {"a": 1}

    def test_expired_cache_return_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("nasdx.macro_fred._CACHE_DIR", tmp_path)
        payload = {
            "value": {"x": 1},
            "written_at": datetime.utcnow().isoformat(),
            "expires_at": time.time() - 1,  # 已过期
        }
        (tmp_path / "expired.json").write_text(json.dumps(payload), encoding="utf-8")
        assert _read_cache("expired") is None

    def test_missing_key_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("nasdx.macro_fred._CACHE_DIR", tmp_path)
        assert _read_cache("nope") is None


# ---------------------------------------------------------------------------
# 5. 批量查询（串行限流）
# ---------------------------------------------------------------------------

class TestBatchObservations:

    def _resp(self, obs: list) -> MagicMock:
        r = MagicMock()
        r.read.return_value = json.dumps({"observations": obs, "series_id": "X", "error_code": None, "error_message": None}).encode()
        r.__enter__ = MagicMock(return_value=r)
        r.__exit__ = MagicMock(return_value=False)
        return r

    def test_serial_calls_with_delays(self, valid_client: FredClient, sample_observations: list) -> None:
        calls: list[tuple] = []
        def fake_urlopen(req, timeout=15):
            calls.append((time.time(), req.full_url))
            return self._resp(sample_observations)

        with patch("nasdx.macro_fred.urlopen", side_effect=fake_urlopen):
            results = valid_client.get_batch_observations(["A", "B", "C"])

        assert len(results) == 3
        assert all(r["series_id"] in ("A", "B", "C") for r in results.values())
        assert len(calls) == 3
        # 验证串行间隔 ≥ 0.2s
        for i in range(1, len(calls)):
            assert calls[i][0] - calls[i - 1][0] >= 0.2


# ---------------------------------------------------------------------------
# 6. 热门系列
# ---------------------------------------------------------------------------

class TestPopularSeries:

    def test_returns_list_on_success(self, valid_client: FredClient) -> None:
        resp_body = {"series": [{"id": "GDP", "title": "Gross Domestic Product"}]}
        r = MagicMock()
        r.read.return_value = json.dumps(resp_body).encode()
        r.__enter__ = MagicMock(return_value=r)
        r.__exit__ = MagicMock(return_value=False)

        with patch("nasdx.macro_fred.urlopen", return_value=r):
            out = valid_client.get_popular_series(count=5)
        assert isinstance(out, list)
        assert len(out) == 1
        assert out[0]["id"] == "GDP"

    def test_empty_on_error(self, valid_client: FredClient) -> None:
        with patch("nasdx.macro_fred.urlopen", side_effect=Exception("boom")):
            out = valid_client.get_popular_series()
        assert out == []

    def test_empty_when_no_key(self) -> None:
        c = FredClient()
        assert c.get_popular_series() == []


# ---------------------------------------------------------------------------
# 7. 模块级便捷函数
# ---------------------------------------------------------------------------

class TestModuleFunctions:

    def test_fred_observations_delegates(self, valid_client: FredClient) -> None:
        # 重新初始化全局实例以使用 valid_client 的 key
        import nasdx.macro_fred as m
        old = m._fred_instance
        m._fred_instance = valid_client
        try:
            r = MagicMock()
            r.read.return_value = json.dumps({"observations": [], "series_id": "GDP", "error_code": None, "error_message": None}).encode()
            r.__enter__ = MagicMock(return_value=r)
            r.__exit__ = MagicMock(return_value=False)
            with patch("nasdx.macro_fred.urlopen", return_value=r):
                out = fred_observations("GDP", limit=10)
            assert out["series_id"] == "GDP"
        finally:
            m._fred_instance = old

    def test_fred_series_info_delegates(self, valid_client: FredClient) -> None:
        import nasdx.macro_fred as m
        old = m._fred_instance
        m._fred_instance = valid_client
        try:
            r = MagicMock()
            r.read.return_value = json.dumps({"series": [{"id": "CPIAUCSL", "title": "CPI", "frequency": "Monthly", "seasonal_adjustment": "None", "units": "Index", "last_updated": "2024-01-01"}], "error_code": None, "error_message": None}).encode()
            r.__enter__ = MagicMock(return_value=r)
            r.__exit__ = MagicMock(return_value=False)
            with patch("nasdx.macro_fred.urlopen", return_value=r):
                out = fred_series_info("CPIAUCSL")
            assert out["series_id"] == "CPIAUCSL"
            assert out["title"] == "CPI"
        finally:
            m._fred_instance = old


# ---------------------------------------------------------------------------
# 8. SERIES_UIDS 完整性
# ---------------------------------------------------------------------------

class TestSeriesUids:

    def test_uid_map_exists(self) -> None:
        assert isinstance(SERIES_UIDS, dict)
        assert "us_gdp" in SERIES_UIDS
        assert SERIES_UIDS["us_gdp"] == "GDP"
        assert SERIES_UIDS["us_cpi"] == "CPIAUCSL"
        assert SERIES_UIDS["us_unemployment"] == "UNRATE"

    def test_uid_map_not_empty(self) -> None:
        assert len(SERIES_UIDS) >= 10


# ---------------------------------------------------------------------------
# 9. 零标的红线 — 禁止提供推荐/预测
# ---------------------------------------------------------------------------

class TestComplianceRedLine:

    def test_no_recommendation_methods(self) -> None:
        """FredClient 不应存在 buy/sell/grade/rank/predict 等方法。"""
        forbidden = {"buy", "sell", "grade", "rank", "predict", "signal", "recommend"}
        methods = {m for m in dir(FredClient) if not m.startswith("_")}
        intersection = methods & forbidden
        assert intersection == set(), f"违反零标的红线: {intersection}"

    def test_docstring_contains_compliance_hint(self) -> None:
        import nasdx.macro_fred as m
        src = m.__file__
        content = Path(src).read_text(encoding="utf-8")
        assert "零标的" in content or "不推荐" in content or "不提供任何买卖推荐" in content

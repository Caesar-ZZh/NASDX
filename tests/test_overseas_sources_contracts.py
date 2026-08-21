"""nasdx.overseas_sources 合约测试（mock，不联网）。

验证：
  1. 各源解析契约（key 字段存在）
  2. SEC 限流器线程安全
  3. SEC_CONTACT env 变量生效
  4. 合规级别速查
  5. 港股拦截
"""
from __future__ import annotations

import os
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

import nasdx.overseas_sources as ov


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ov, "_CACHE", {})
    monkeypatch.setattr(ov, "_YAHOO_STATE", {"session": None})


class TestComplianceMap:
    def test_keys_cover_all_sources(self):
        expected = {
            "sec_edgar", "treasury_yield", "cftc_cot",
            "finra_sho", "cboe_option", "yahoo_finance",
        }
        assert set(ov.COMPLIANCE_MAP.keys()) == expected

    @pytest.mark.parametrize("source", list(ov.COMPLIANCE_MAP))
    def test_each_has_s_b_or_c(self, source):
        level, _ = ov.COMPLIANCE_MAP[source]
        assert level in ("S", "B", "C")

    def test_edgar_is_s(self):
        level, _ = ov.COMPLIANCE_MAP["sec_edgar"]
        assert level == "S"

    def test_finra_is_b(self):
        level, _ = ov.COMPLIANCE_MAP["finra_sho"]
        assert level == "B"

    def test_cboe_is_c(self):
        level, _ = ov.COMPLIANCE_MAP["cboe_option"]
        assert level == "C"


class TestRateLimiter:
    def test_concurrent_does_not_exceed_limit(self):
        limiter = ov._RateLimiter(10)  # 10 req/s
        times = []

        def hit():
            t0 = time.monotonic()
            limiter.wait()
            times.append(time.monotonic() - t0)

        threads = [threading.Thread(target=hit) for _ in range(20)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # 第一个等待应接近 0；后续应彼此错开 ≥ 100ms
        assert all(t >= 0.08 for t in times[1:])


class TestOfficialGet:
    def test_sec_ua_injected(self):
        with patch("nasdx.overseas_sources._get_sec_contact", return_value="TestName test@example.com"):
            with patch("nasdx.overseas_sources.requests.get") as mock_get:
                resp = MagicMock()
                resp.status_code = 200
                resp.headers = {}
                resp.text = "{}"
                resp.json.return_value = {}
                mock_get.return_value = resp
                ov._official_get("https://efts.sec.gov/LATEST/search-index?q=AAPL")
                call = mock_get.call_args
                assert "test@example.com" in call.kwargs.get("headers", {}).get("User-Agent", "")

    def test_non_sec_ua_different(self):
        with patch("nasdx.overseas_sources.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            resp.text = "[]"
            resp.json.return_value = {}
            mock_get.return_value = resp
            ov._official_get("https://www.cftc.gov/dea/newcot/NewCots.csv")
            ua = mock_get.call_args.kwargs["headers"]["User-Agent"]
            assert "test@example.com" not in ua

    def test_undeclared_ua_raises_runtime_error(self):
        with patch("nasdx.overseas_sources._get_sec_contact", return_value="your-name your-email@example.com"):
            with patch("nasdx.overseas_sources.requests.get") as mock_get:
                resp = MagicMock()
                resp.status_code = 403
                resp.headers = {"Content-Type": "text/html"}
                resp.text = "<html>Undeclared Automated Tool</html>"
                import requests
                exc = requests.HTTPError("forbidden", response=resp)
                resp.raise_for_status.side_effect = exc
                mock_get.return_value = resp
                with pytest.raises(RuntimeError, match="User-Agent 未被识别"):
                    ov._official_get("https://www.sec.gov/cgi-bin/browse-edgar/...")


class TestGetSecContact:
    def test_env_var(self):
        with patch.dict(os.environ, {"SEC_CONTACT": "MyOrg contact@my.org"}):
            assert ov._get_sec_contact() == "MyOrg contact@my.org"

    def test_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="SEC_CONTACT 未配置"):
                ov._get_sec_contact()


class TestUsTickerCheck:
    def test_us_ticker_pass(self):
        assert ov._check_us_ticker("AAPL") == "AAPL"

    def test_hk_ticker_rejected(self):
        with pytest.raises(ValueError, match="港股"):
            ov._check_us_ticker("0700.HK")

    def test_invalid_ticker_rejected(self):
        with pytest.raises(ValueError, match="无效"):
            ov._check_us_ticker("!!BAD!!")


class TestTreasuryYieldCurve:
    def test_returns_list_of_dicts(self):
        fake = [
            {"effective_date": "2026-01-15", "term_to_maturity": "1 Month", "rate": 4.2},
            {"effective_date": "2026-01-15", "term_to_maturity": "10 Year", "rate": 4.5},
        ]
        with patch("nasdx.overseas_sources._official_get") as mock_get:
            mock_get.return_value = {"data": fake}
            result = ov.treasury_yield_curve("2026-01-15")
        assert isinstance(result, list)
        assert result[0]["term_to_maturity"] == "1 Month"
        assert result[1]["rate"] == 4.5

    def test_rejects_invalid_date(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            ov.treasury_yield_curve("2026/01/15")


class TestFinraSHO:
    def test_daily_returns_aggregate_without_stock_list(self):
        csv_text = """trading_symbol,short_volume,total_volume
AAPL,12000000,50000000
TSLA,8000000,40000000
"""
        with patch("nasdx.overseas_sources._official_get") as mock_get:
            resp = MagicMock()
            resp.text = csv_text
            mock_get.return_value = resp
            rows = ov.finra_sho_daily("2026-01-15")
        assert rows["record_count"] == 2
        assert rows["short_volume"] == 20000000.0
        assert rows["total_volume"] == 90000000.0
        assert rows["date"] == "2026-01-15"
        assert "trading_symbol" not in rows

    def test_short_ratio_computes(self):
        csv_text = "trading_symbol,short_volume,total_volume\nAAPL,2000,8000"
        with patch("nasdx.overseas_sources._official_get") as mock_get:
            resp = MagicMock()
            resp.text = csv_text
            mock_get.return_value = resp
            ratio = ov.finra_sho_short_ratio("AAPL", "2026-01-15")
        assert ratio == 0.25

    def test_missing_ticker_returns_none(self):
        with patch("nasdx.overseas_sources._official_get") as mock_get:
            resp = MagicMock()
            resp.text = "trading_symbol,short_volume,total_volume\nGOOG,100,200"
            mock_get.return_value = resp
            assert ov.finra_sho_short_ratio("AAPL", "2026-01-15") is None


class TestYahooSession:
    def test_session_caches_crumb(self):
        with patch("nasdx.overseas_sources.requests.Session") as MockSession:
            s = MagicMock()
            MockSession.return_value = s
            ov._get_yahoo_session()
            ov._get_yahoo_session()
            # getcrumb 只应被调用一次（会话复用）
            assert s.get.call_count == 2  # fc + crumb（首次）；第二次命中缓存


class TestEdgarSubmissions:
    def test_returns_list_of_dataclass(self):
        fake = {"results": [{
            "form": "10-K",
            "ticker": "AAPL",
            "cik_str": "0000320192",
            "filedDate": "2025-10-31",
            "accessionNumber": "0001628280-25-044631",
            "filingUrl": "https://www.sec.gov/cgi-bin/browse-edgar/?company=AAPL",
        }]}
        with patch("nasdx.overseas_sources._official_get") as mock_get:
            resp = MagicMock()
            resp.json.return_value = fake
            mock_get.return_value = resp
            subs = ov.edgar_submissions("AAPL", form_types=["10-K"], limit=5)
        assert len(subs) == 1
        assert isinstance(subs[0], ov.EdgartechSubmission)
        assert subs[0].form == "10-K"
        assert subs[0].cik == "0000320192"

    def test_invalid_ticker_raises(self):
        with pytest.raises(ValueError, match="无效"):
            ov.edgar_submissions("!!BAD!!")

    def test_xbrl_companyfacts_are_normalized(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "NetIncomeLoss": {
                        "units": {
                            "USD": [{
                                "end": "2025-12-31", "fy": 2025, "fp": "FY",
                                "form": "10-K", "filed": "2026-02-01", "val": 123,
                            }]
                        }
                    }
                }
            }
        }
        with patch("nasdx.overseas_sources.edgar_cik_lookup", return_value="0000320192"), patch(
            "nasdx.overseas_sources._official_get", return_value=facts
        ):
            result = ov.edgar_xbrl_indicators("AAPL", tags=["NetIncomeLoss"], period="FY", years=3)
        row = result["indicators"]["NetIncomeLoss"][0]
        assert row["value"] == 123
        assert row["unit"] == "USD"


class TestCboeOptionChain:
    def test_calls_api_with_ticker(self):
        with patch("nasdx.overseas_sources._official_get") as mock_get:
            mock_get.return_value = {"ticker": "AAPL", "calls": [], "puts": []}
            result = ov.cboe_option_chain("AAPL", expiration="2026-02-21")
        assert result["ticker"] == "AAPL"
        call_url = mock_get.call_args[0][0]
        assert "AAPL" in call_url

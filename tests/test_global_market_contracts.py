"""nasdx.global_market 的离线契约测试。"""

from __future__ import annotations

import inspect
from unittest import mock

import pytest

import nasdx.global_market as gm


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    gm.clear_cache()
    gm._HTTP_SESSIONS.clear()
    yield
    gm.clear_cache()
    gm._HTTP_SESSIONS.clear()


def _quote(
    code: str,
    name: str = "测试证券",
    *,
    price: int = 12345,
    decimals: int = 2,
) -> dict:
    return {
        "f43": price,
        "f44": price + 100,
        "f45": price - 100,
        "f46": price - 50,
        "f48": 1_000_000,
        "f57": code,
        "f58": name,
        "f59": decimals,
        "f60": price - 25,
        "f116": 2_000_000,
        "f170": 125,
    }


def test_global_indices_use_exact_secid_mapping() -> None:
    calls: list[str] = []

    def load(secid: str) -> dict:
        calls.append(secid)
        return _quote(secid.split(".", 1)[1], price=10_000)

    with mock.patch.object(gm, "_push2_stock_get", side_effect=load):
        result = gm.global_indices()

    assert calls == ["100.DJIA", "100.SPX", "100.NDX", "100.HSI", "124.HSTECH"]
    assert [item["key"] for item in result] == ["dji", "spx", "ndx", "hsi", "hstech"]
    assert [item["price"] for item in result] == [100.0] * 5
    assert [item["change_pct"] for item in result] == [1.25] * 5


def test_global_indices_skip_only_missing_source_rows() -> None:
    with mock.patch.object(
        gm,
        "_push2_stock_get",
        side_effect=lambda secid: None if secid == "100.NDX" else _quote("x"),
    ):
        result = gm.global_indices()
    assert [item["key"] for item in result] == ["dji", "spx", "hsi", "hstech"]


def test_price_respects_zero_decimal_markets() -> None:
    assert gm._price({"f43": 73_500, "f59": 0}, "f43") == 73_500
    assert gm._price({"f43": 12_345, "f59": 2}, "f43") == 123.45
    assert gm._price({"f43": "-", "f59": 2}, "f43") is None


def test_quote_missing_fields_are_none_not_errors() -> None:
    quote = gm._quote_from({})
    assert set(quote) == {
        "code",
        "name",
        "price",
        "open",
        "high",
        "low",
        "prev_close",
        "amount",
        "market_cap",
        "change_pct",
    }
    assert all(value is None for value in quote.values())


def test_push2_falls_back_then_latches_delay_host() -> None:
    urls: list[str] = []

    def request(url: str, *, params: dict, timeout: float = 5.0) -> dict:
        urls.append(url)
        if "push2delay" in url:
            return {"data": _quote(str(params["secid"]))}
        return {}

    with mock.patch.object(gm, "_request_json", side_effect=request):
        assert gm._push2_stock_get("100.DJIA")
        gm.clear_cache(reset_host=False)
        assert gm._push2_stock_get("100.SPX")

    assert "push2.eastmoney.com" in urls[0]
    assert "push2delay.eastmoney.com" in urls[1]
    assert len(urls) == 3
    assert "push2delay.eastmoney.com" in urls[2]


def test_push2_empty_result_is_not_cached() -> None:
    with mock.patch.object(gm, "_request_json", return_value={}) as request:
        assert gm._push2_stock_get("100.DJIA") is None
        assert gm._push2_stock_get("100.DJIA") is None
    assert request.call_count == 4


def test_request_json_tries_direct_then_environment_proxy() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"ok": True}}

    direct = mock.Mock()
    direct.get.side_effect = OSError("direct unavailable")
    proxied = mock.Mock()
    proxied.get.return_value = Response()

    with mock.patch.object(
        gm,
        "_http_session",
        side_effect=lambda *, direct: direct_session if direct else proxied,
    ):
        direct_session = direct
        result = gm._request_json("https://example.invalid", params={"a": 1})

    assert result == {"data": {"ok": True}}
    direct.get.assert_called_once()
    proxied.get.assert_called_once()


@pytest.mark.parametrize(
    ("query", "expected_secid", "expected_secucode", "expected_market"),
    [
        ("AAPL.O", "105.AAPL", "AAPL.O", "NASDAQ"),
        ("BABA.N", "106.BABA", "BABA.N", "NYSE"),
        ("700", "116.00700", "00700.HK", "HK"),
        ("00700.HK", "116.00700", "00700.HK", "HK"),
        ("005930.KS", "177.005930", "005930.KS", "KR"),
        ("000660.KQ", "177.000660", "000660.KQ", "KR"),
    ],
)
def test_resolve_symbol_parses_explicit_markets(
    query: str,
    expected_secid: str,
    expected_secucode: str,
    expected_market: str,
) -> None:
    with mock.patch.object(gm, "_push2_stock_get", return_value=_quote(expected_secid)):
        result = gm.resolve_symbol(query)
    assert result is not None
    assert result["secid"] == expected_secid
    assert result["secucode"] == expected_secucode
    assert result["market"] == expected_market


def test_korean_suffix_survives_when_quote_source_is_down() -> None:
    with mock.patch.object(gm, "_push2_stock_get", return_value=None) as quote:
        result = gm.resolve_symbol("005930.KS")
    quote.assert_not_called()
    assert result == {
        "code": "005930",
        "secid_prefix": 177,
        "secid": "177.005930",
        "secucode": "005930.KS",
        "market": "KR",
        "name": "",
    }


def test_plain_us_symbol_probes_supported_markets() -> None:
    calls: list[str] = []

    def load(secid: str) -> dict | None:
        calls.append(secid)
        return _quote("BABA", "阿里巴巴") if secid == "106.BABA" else None

    with mock.patch.object(gm, "_push2_stock_get", side_effect=load):
        result = gm.resolve_symbol("baba")
    assert calls == ["105.BABA", "106.BABA"]
    assert result is not None
    assert result["market"] == "NYSE"
    assert result["secucode"] == "BABA.N"


@pytest.mark.parametrize("query", ["", "   ", "600519", "00700.BAD", "../AAPL"])
def test_resolve_symbol_rejects_ambiguous_or_invalid_input(query: str) -> None:
    with mock.patch.object(gm, "_push2_stock_get") as quote:
        assert gm.resolve_symbol(query) is None
    quote.assert_not_called()


def test_us_hk_stock_returns_quote_and_key_metrics() -> None:
    info = {
        "code": "AAPL",
        "name": "Apple",
        "secid_prefix": 105,
        "secid": "105.AAPL",
        "secucode": "AAPL.O",
        "market": "NASDAQ",
    }
    metrics = {"report_date": "2026-06-30", "revenue": 100.0}
    with (
        mock.patch.object(gm, "resolve_symbol", return_value=info),
        mock.patch.object(gm, "_push2_stock_get", return_value=_quote("AAPL", "Apple")),
        mock.patch.object(gm, "_key_metrics", return_value=metrics),
    ):
        result = gm.us_hk_stock("AAPL.O")

    assert result["code"] == "AAPL"
    assert result["market"] == "NASDAQ"
    assert result["quote"]["price"] == 123.45
    assert result["quote"]["change_pct"] == 1.25
    assert result["metrics"] == metrics
    assert result["data_as_of"]


def test_korean_stock_never_requests_unavailable_f10_metrics() -> None:
    info = {
        "code": "005930",
        "name": "Samsung",
        "secid_prefix": 177,
        "secid": "177.005930",
        "secucode": "005930.KS",
        "market": "KR",
    }
    with (
        mock.patch.object(gm, "resolve_symbol", return_value=info),
        mock.patch.object(gm, "_push2_stock_get", return_value=_quote("005930")),
        mock.patch.object(gm, "_key_metrics") as metrics,
    ):
        result = gm.us_hk_stock("005930.KS")
    assert result["metrics"] is None
    metrics.assert_not_called()


def test_explicit_symbol_returns_null_quote_shape_during_outage() -> None:
    with (
        mock.patch.object(gm, "_push2_stock_get", return_value=None),
        mock.patch.object(gm, "_key_metrics", return_value=None),
    ):
        result = gm.us_hk_stock("00700.HK")
    assert result["code"] == "00700"
    assert result["market"] == "HK"
    assert all(value is None for value in result["quote"].values())
    assert result["metrics"] is None


def test_key_metrics_parse_missing_and_zero_values() -> None:
    rows = [
        {
            "REPORT_DATE": "2026-06-30 00:00:00",
            "OPERATE_INCOME": 100,
            "OPERATE_INCOME_YOY": None,
            "PARENT_HOLDER_NETPROFIT": 0,
            "HOLDER_PROFIT": 9,
            "BASIC_EPS": "-",
            "ROE_AVG": 12.5,
        }
    ]
    with mock.patch.object(gm, "_datacenter_rows", return_value=rows) as request:
        result = gm._key_metrics("AAPL.O")
    request.assert_called_once_with(
        "RPT_USF10_FN_GMAININDICATOR",
        filter_text='(SECUCODE="AAPL.O")',
        page_size=1,
    )
    assert result is not None
    assert result["report_date"] == "2026-06-30"
    assert result["net_profit"] == 0.0
    assert result["eps"] is None
    assert result["gross_margin"] is None


def test_datacenter_parser_returns_rows_or_empty() -> None:
    payload = {"result": {"data": [{"REPORT_DATE": "2026-06-30"}]}}
    with mock.patch.object(gm, "_request_json", return_value=payload):
        assert gm._datacenter_rows("REPORT", filter_text="(X=1)") == [
            {"REPORT_DATE": "2026-06-30"}
        ]

    gm.clear_cache()
    with mock.patch.object(gm, "_request_json", return_value={}):
        assert gm._datacenter_rows("REPORT", filter_text="(X=1)") == []


def test_hk_cashflow_groups_and_limits_periods() -> None:
    info = {
        "code": "00700",
        "name": "腾讯",
        "secid_prefix": 116,
        "secid": "116.00700",
        "secucode": "00700.HK",
        "market": "HK",
    }
    rows = [
        {
            "REPORT_DATE": "2026-06-30",
            "STD_ITEM_CODE": "003999",
            "AMOUNT": 100,
            "YOY_RATIO": 2.5,
            "CURRENCY": "CNY",
        },
        {
            "REPORT_DATE": "2026-06-30",
            "STD_ITEM_CODE": "005999",
            "AMOUNT": -20,
            "YOY_RATIO": None,
            "CURRENCY": "CNY",
        },
        {
            "REPORT_DATE": "2025-12-31",
            "STD_ITEM_CODE": "003999",
            "AMOUNT": 80,
            "YOY_RATIO": 1.0,
            "CURRENCY": "CNY",
        },
    ]
    with (
        mock.patch.object(gm, "resolve_symbol", return_value=info),
        mock.patch.object(gm, "_datacenter_rows", return_value=rows),
    ):
        result = gm.hk_cashflow("00700.HK", periods=1)

    assert result["code"] == "00700"
    assert result["currency"] == "CNY"
    assert len(result["periods"]) == 1
    period = result["periods"][0]
    assert period["report_date"] == "2026-06-30"
    assert period["items"]["经营活动现金流净额"] == {"amount": 100.0, "yoy": 2.5}
    assert period["items"]["投资活动现金流净额"] == {"amount": -20.0, "yoy": None}


@pytest.mark.parametrize("periods", [0, -1, 1.5, True, "8"])
def test_hk_cashflow_periods_must_be_positive_integer(periods: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        gm.hk_cashflow("00700.HK", periods)  # type: ignore[arg-type]


def test_cache_ttl_and_empty_retry_contract() -> None:
    calls = 0

    def load() -> dict:
        nonlocal calls
        calls += 1
        return {"call": calls}

    with mock.patch.object(gm, "_monotonic", side_effect=[100.0, 399.0, 400.0]):
        first = gm._cached("key", load)
        hit = gm._cached("key", load)
        expired = gm._cached("key", load)
    assert first == hit == {"call": 1}
    assert expired == {"call": 2}
    assert gm.CACHE_TTL_SECONDS == 300.0

    assert gm._cached("empty", lambda: {}) == {}
    assert "empty" not in gm._CACHE


def test_module_contains_no_hardcoded_credentials() -> None:
    source = inspect.getsource(gm).lower()
    assert "d43bf722" not in source
    assert "api_key" not in source
    assert '"token"' not in source

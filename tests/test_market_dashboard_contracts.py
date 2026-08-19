"""nasdx.market_dashboard 的离线契约测试。"""

from __future__ import annotations

import inspect
import sys
import types
from unittest import mock

import pytest

import nasdx.market_dashboard as dashboard


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    dashboard.clear_dashboard_cache()
    yield
    dashboard.clear_dashboard_cache()


def _ready_snapshot() -> dict:
    data = {
        "domestic_indices": [
            {"key": "sh000001", "name": "上证指数", "price": 3500, "change_pct": 0.5}
        ],
        "global_indices": [
            {"key": "spx", "name": "标普500", "price": 6500, "change_pct": -0.2}
        ],
        "market_breadth": {"up": 2500, "down": 1800, "breadth": "偏强"},
        "sector_funds": [{"sector": "计算机", "net": 1000}],
        "turnover": {
            "sample_size": 20,
            "total_amount": 1_000_000,
            "market_amount_share": 0.25,
        },
        "flash_news": [
            {"published_at": "10:00", "source": "媒体", "title": "宏观数据发布"}
        ],
        "commodities": {
            "count": 30,
            "up": 12,
            "down": 10,
            "flat": 8,
            "average_change_pct": 0.1,
        },
        "treasury_curve": [
            {"effective_date": "2026-08-18", "term": "10Y", "rate": 4.1}
        ],
        "industry_chains": [
            {"theme": "人工智能", "stage": "上游", "scope": "算力芯片、服务器、光通信"}
        ],
    }
    return {
        "schema_version": "nasdx_market_dashboard.v1",
        "status": "complete",
        "updated": "2026-08-19 10:00:00",
        "ready_panels": 9,
        "total_panels": 9,
        "panels": {
            name: {
                "label": dashboard.PANEL_LABELS[name],
                "status": "ready",
                "data": data[name],
                "detail": "",
            }
            for name in dashboard.PANEL_ORDER
        },
        "compliance": {
            "individual_rankings": False,
            "recommendations": False,
            "predictions": False,
        },
    }


def test_snapshot_contains_all_panels_in_stable_order() -> None:
    loaders = {name: (lambda name=name: {"panel": name}) for name in dashboard.PANEL_ORDER}
    snapshot = dashboard.build_dashboard_snapshot(loaders)
    assert list(snapshot["panels"]) == list(dashboard.PANEL_ORDER)
    assert snapshot["ready_panels"] == len(dashboard.PANEL_ORDER)
    assert snapshot["status"] == "complete"
    assert snapshot["compliance"] == {
        "individual_rankings": False,
        "recommendations": False,
        "predictions": False,
    }


def test_one_panel_failure_does_not_block_others() -> None:
    def missing_dependency() -> None:
        raise ModuleNotFoundError("not merged", name="nasdx.global_market")

    loaders = {name: (lambda: {"ok": True}) for name in dashboard.PANEL_ORDER}
    loaders["global_indices"] = missing_dependency
    loaders["flash_news"] = lambda: []

    snapshot = dashboard.build_dashboard_snapshot(loaders)
    assert snapshot["status"] == "partial"
    assert snapshot["ready_panels"] == len(dashboard.PANEL_ORDER) - 2
    assert snapshot["panels"]["global_indices"] == {
        "label": "全球关键指数",
        "status": "dependency_pending",
        "data": None,
        "detail": "nasdx.global_market",
    }
    assert snapshot["panels"]["flash_news"]["status"] == "unavailable"
    assert snapshot["panels"]["domestic_indices"]["status"] == "ready"


def test_domestic_indices_reuse_tencent_adapter() -> None:
    quotes = {
        "000001": {"close": 3500, "change_pct": 0.5, "quote_time": "20260819100000"},
        "399001": {"close": 11000, "change_pct": -0.2, "quote_time": "20260819100000"},
        "399006": {"close": 2200, "change_pct": 1.1, "quote_time": "20260819100000"},
        "000300": {"close": 4100, "change_pct": 0.1, "quote_time": "20260819100000"},
    }
    with mock.patch("nasdx.fast_market.fetch_tencent_quotes", return_value=quotes) as fetch:
        result = dashboard._load_domestic_indices()
    fetch.assert_called_once_with(
        ["sh000001", "sz399001", "sz399006", "sh000300"],
        request_timeout=4.0,
        max_workers=1,
    )
    assert [item["name"] for item in result] == [
        "上证指数",
        "深证成指",
        "创业板指",
        "沪深300",
    ]
    assert result[0]["price"] == 3500.0


def test_flash_news_parser_keeps_only_metadata_and_title() -> None:
    payload = {
        "data": {
            "items": [
                {
                    "id": 1,
                    "display_time": 1787100000,
                    "title": "宏观数据发布",
                    "content": "不应在 title 存在时覆盖标题",
                    "source": "测试媒体",
                    "body": "长正文不应进入输出",
                    "stocks": [{"code": "600000"}],
                }
            ]
        }
    }
    result = dashboard._parse_flash_news(payload, 10)
    assert result == [
        {
            "id": "1",
            "published_at": "1787100000",
            "title": "宏观数据发布",
            "source": "测试媒体",
        }
    ]
    assert "600000" not in str(result)
    assert "长正文" not in str(result)


def test_flash_news_failure_is_empty_and_not_cached() -> None:
    with mock.patch("requests.get", side_effect=TimeoutError) as request:
        assert dashboard._load_flash_news() == []
        assert dashboard._load_flash_news() == []
    assert request.call_count == 2


def test_flash_news_success_uses_five_minute_cache() -> None:
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": {"items": [{"id": "1", "title": "快讯", "display_time": "10:00"}]}
    }
    with mock.patch("requests.get", return_value=response) as request:
        first = dashboard._load_flash_news()
        second = dashboard._load_flash_news()
    assert first == second
    assert request.call_count == 1
    assert dashboard.CACHE_TTL_SECONDS == 300.0


def test_commodity_panel_is_aggregate_only() -> None:
    module = types.ModuleType("nasdx.commodity_100ppi")
    module.fetch_list = lambda: [
        {"name": "品种甲", "change_pct": 2.0},
        {"name": "品种乙", "change_pct": -1.0},
        {"name": "品种丙", "change_pct": 0.0},
    ]
    with mock.patch.dict(sys.modules, {"nasdx.commodity_100ppi": module}):
        result = dashboard._load_commodities()
    assert result == {
        "count": 3,
        "up": 1,
        "down": 1,
        "flat": 1,
        "average_change_pct": 0.3333,
    }
    assert "品种甲" not in str(result)
    assert "top_gainers" not in result


def test_treasury_curve_normalizes_fields() -> None:
    module = types.ModuleType("nasdx.overseas_sources")
    module.treasury_yield_curve = lambda: [
        {
            "effective_date": "2026-08-18",
            "term_to_maturity": "10Y",
            "rate": "4.10",
            "unused": "x",
        }
    ]
    with mock.patch.dict(sys.modules, {"nasdx.overseas_sources": module}):
        result = dashboard._load_treasury_curve()
    assert result == [
        {"effective_date": "2026-08-18", "term": "10Y", "rate": 4.1}
    ]


def test_industry_chains_have_no_security_lists() -> None:
    rows = dashboard._load_industry_chains()
    assert rows
    assert {row["stage"] for row in rows} == {"上游", "中游", "下游"}
    assert all(set(row) == {"theme", "stage", "scope"} for row in rows)
    encoded = str(rows).lower()
    assert "stock" not in encoded
    assert "code" not in encoded


def test_cache_expires_at_five_minutes() -> None:
    calls = 0

    def load() -> dict:
        nonlocal calls
        calls += 1
        return {"call": calls}

    with mock.patch.object(dashboard, "_monotonic", side_effect=[100.0, 399.0, 400.0]):
        first = dashboard._cached("key", load)
        hit = dashboard._cached("key", load)
        expired = dashboard._cached("key", load)
    assert first == hit == {"call": 1}
    assert expired == {"call": 2}


class FakeColumn:
    def __init__(self, owner: "FakeStreamlit") -> None:
        self.owner = owner

    def __enter__(self) -> "FakeColumn":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def metric(self, *args: object, **kwargs: object) -> None:
        self.owner.calls.append(("metric", args, kwargs))


class FakeStreamlit:
    def __init__(self, *, refresh: bool = False) -> None:
        self.refresh = refresh
        self.calls: list[tuple] = []

    def markdown(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("markdown", args, kwargs))

    def title(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("title", args, kwargs))

    def caption(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("caption", args, kwargs))

    def button(self, *args: object, **kwargs: object) -> bool:
        self.calls.append(("button", args, kwargs))
        return self.refresh

    def subheader(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("subheader", args, kwargs))

    def columns(self, count: int, **kwargs: object) -> list[FakeColumn]:
        self.calls.append(("columns", (count,), kwargs))
        return [FakeColumn(self) for _ in range(count)]

    def dataframe(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("dataframe", args, kwargs))

    def write(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("write", args, kwargs))

    def info(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("info", args, kwargs))


def test_streamlit_component_renders_all_ready_panels() -> None:
    st = FakeStreamlit()
    snapshot = _ready_snapshot()
    result = dashboard.render_market_dashboard(snapshot, st_module=st)
    assert result == snapshot
    call_names = [call[0] for call in st.calls]
    assert "markdown" in call_names
    assert "metric" in call_names
    assert "dataframe" in call_names
    assert "write" in call_names
    assert "info" not in call_names


def test_streamlit_component_surfaces_unavailable_panel() -> None:
    st = FakeStreamlit()
    snapshot = _ready_snapshot()
    snapshot["panels"]["global_indices"] = {
        "label": "全球关键指数",
        "status": "dependency_pending",
        "data": None,
        "detail": "nasdx.global_market",
    }
    dashboard.render_market_dashboard(snapshot, st_module=st)
    assert any(call[0] == "info" and "dependency_pending" in call[1][0] for call in st.calls)


def test_refresh_button_rebuilds_snapshot() -> None:
    st = FakeStreamlit(refresh=True)
    rebuilt = _ready_snapshot()
    with (
        mock.patch.object(dashboard, "clear_dashboard_cache") as clear,
        mock.patch.object(dashboard, "build_dashboard_snapshot", return_value=rebuilt) as build,
    ):
        result = dashboard.render_market_dashboard(_ready_snapshot(), st_module=st)
    clear.assert_called_once_with()
    build.assert_called_once_with()
    assert result == rebuilt


def test_component_has_responsive_css_and_no_frontend_build_chain() -> None:
    source = inspect.getsource(dashboard)
    assert "@media (max-width: 720px)" in source
    assert "st.columns" in source
    lowered = source.lower()
    assert "import react" not in lowered
    assert "from react" not in lowered
    assert "npm " not in lowered
    assert "node_modules" not in lowered


def test_standalone_entry_uses_wide_streamlit_page() -> None:
    source = inspect.getsource(dashboard.main)
    assert 'layout="wide"' in source
    assert "render_market_dashboard" in source

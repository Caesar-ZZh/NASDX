"""nasdx.daily_review 的离线契约测试。"""

from __future__ import annotations

import json
from datetime import date
from unittest import mock

import pytest

import nasdx.analysis_cache as analysis_cache
import nasdx.daily_review as dr


class FakeFrame:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def to_dict(self, orient: str) -> list[dict]:
        assert orient == "records"
        return list(self._rows)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    dr.clear_cache()
    yield
    dr.clear_cache()


def test_existing_analysis_cache_contract_is_preserved() -> None:
    """每日复盘不能用通用小缓存覆盖已有的分层分析缓存内核。"""
    assert analysis_cache.CACHE_CONTRACT == "nasdx_analysis_cache.v1"
    assert analysis_cache.AGENT_CONFIG_VERSION
    assert analysis_cache.PROMPT_SCHEMA_VERSION
    assert callable(analysis_cache.plan_reuse)


def test_emotion_ladder_and_rates_are_aggregated_without_symbols() -> None:
    limit_up = [
        {"代码": f"{index:06d}", "名称": f"证券{index}", "连板数": boards}
        for index, boards in enumerate([1, 2, 2, 3, 3, 3, 4, 5, 6, 1])
    ]
    pools = {
        "limit_up": limit_up,
        "broken": [{"代码": "x"}, {"代码": "y"}],
        "limit_down": [{"代码": "z"}] * 3,
        "previous_limit_up": [{"代码": str(index)} for index in range(8)],
    }

    with (
        mock.patch.object(dr, "_today", return_value=date(2024, 1, 1)),
        mock.patch.object(
            dr,
            "_pool_rows",
            side_effect=lambda kind, date_text: pools[kind],
        ),
    ):
        result = dr.get_short_term_emotion()

    assert result == {
        "date": "2024-01-01",
        "limit_up_count": 10,
        "limit_down_count": 3,
        "broken_count": 2,
        "max_boards": 6,
        "consecutive_count": 8,
        "ladder": [
            {"boards": 2, "count": 2, "plus": False},
            {"boards": 3, "count": 3, "plus": False},
            {"boards": 4, "count": 1, "plus": False},
            {"boards": 5, "count": 2, "plus": True},
        ],
        "seal_rate": 0.833,
        "break_rate": 0.167,
        "promotion_rate": 1.0,
        "previous_limit_up_count": 8,
    }
    encoded = json.dumps(result, ensure_ascii=False)
    assert "证券" not in encoded
    assert "代码" not in encoded
    assert "名称" not in encoded


def test_emotion_resolves_recent_trading_date_once() -> None:
    requested: list[tuple[str, str]] = []

    def load(kind: str, date_text: str) -> list[dict]:
        requested.append((kind, date_text))
        if kind == "limit_up":
            return [{"连板数": 1}] if date_text == "20240106" else []
        return []

    with (
        mock.patch.object(dr, "_today", return_value=date(2024, 1, 8)),
        mock.patch.object(dr, "_pool_rows", side_effect=load),
    ):
        result = dr.get_short_term_emotion()

    assert result["date"] == "2024-01-06"
    assert requested[:3] == [
        ("limit_up", "20240108"),
        ("limit_up", "20240107"),
        ("limit_up", "20240106"),
    ]
    assert requested[3:] == [
        ("broken", "20240106"),
        ("limit_down", "20240106"),
        ("previous_limit_up", "20240106"),
    ]


def test_emotion_empty_result_is_not_cached() -> None:
    calls = 0

    def empty(kind: str, date_text: str) -> list[dict]:
        nonlocal calls
        calls += 1
        return []

    with mock.patch.object(dr, "_pool_rows", side_effect=empty):
        assert dr.get_short_term_emotion() == {}
        assert dr.get_short_term_emotion() == {}

    assert calls == 16


def test_cache_ttl_expires_at_five_minutes() -> None:
    calls = 0

    def load() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"call": calls}

    with mock.patch.object(dr, "_monotonic", side_effect=[100.0, 399.0, 400.0]):
        first = dr._cached("key", load)
        hit = dr._cached("key", load)
        expired = dr._cached("key", load)

    assert first == hit == {"call": 1}
    assert expired == {"call": 2}
    assert dr.CACHE_TTL_SECONDS == 300.0


def test_cache_empty_result_and_selective_clear() -> None:
    calls = 0

    def empty() -> dict:
        nonlocal calls
        calls += 1
        return {}

    assert dr._cached("empty", empty) == {}
    assert dr._cached("empty", empty) == {}
    assert calls == 2

    dr._CACHE["keep"] = (0.0, {"value": 1})
    dr._CACHE["drop"] = (0.0, {"value": 2})
    dr.clear_cache(["drop"])
    assert "keep" in dr._CACHE
    assert "drop" not in dr._CACHE


def test_overview_normalizes_sentiment_and_sorts_sector_funds() -> None:
    class FakeAkshare:
        @staticmethod
        def stock_market_activity_legu() -> FakeFrame:
            return FakeFrame(
                [
                    {"item": "上涨", "value": 2500},
                    {"item": "下跌", "value": 1800},
                    {"item": "平盘", "value": 200},
                    {"item": "涨停", "value": 85},
                    {"item": "真实涨停", "value": 72},
                    {"item": "跌停", "value": 5},
                    {"item": "真实跌停", "value": 4},
                    {"item": "活跃度", "value": "高"},
                    {"item": "统计日期", "value": "2024-01-01"},
                ]
            )

        @staticmethod
        def stock_fund_flow_industry(*, symbol: str) -> FakeFrame:
            assert symbol == "即时"
            return FakeFrame(
                [
                    {
                        "行业": "计算机",
                        "行业-涨跌幅": 1.2,
                        "净额": 2_000.0,
                        "流入资金": 4_000.0,
                        "流出资金": 2_000.0,
                        "公司家数": 50,
                    },
                    {
                        "行业": "半导体",
                        "行业-涨跌幅": 2.1,
                        "净额": 5_200.0,
                        "流入资金": 8_000.0,
                        "流出资金": 2_800.0,
                        "公司家数": 42,
                    },
                ]
            )

    with mock.patch.object(dr, "_load_akshare", return_value=FakeAkshare):
        result = dr.get_overview()

    assert result["sentiment"]["up"] == 2500
    assert result["sentiment"]["down"] == 1800
    assert result["sentiment"]["breadth"] == "偏强"
    assert result["sentiment"]["speculation"] == "活跃"
    assert [item["sector"] for item in result["sectors"]] == ["半导体", "计算机"]
    assert result["sectors"][0]["net"] == 5_200.0


def test_overview_source_failure_is_an_empty_retryable_result() -> None:
    class BrokenAkshare:
        @staticmethod
        def stock_market_activity_legu() -> None:
            raise RuntimeError("offline")

        @staticmethod
        def stock_fund_flow_industry(*, symbol: str) -> None:
            raise RuntimeError("offline")

    with mock.patch.object(dr, "_load_akshare", return_value=BrokenAkshare):
        assert dr.get_overview() == {}
    assert "daily_review:overview" not in dr._CACHE


def test_turnover_top_returns_only_market_aggregate() -> None:
    raw_rows = [
        {"代码": "000001", "名称": "甲", "成交额": 1_000.0, "涨跌幅": 1.0},
        {"代码": "000002", "名称": "乙", "成交额": 3_000.0, "涨跌幅": -2.0},
        {"代码": "000003", "名称": "丙", "成交额": 2_000.0, "涨跌幅": 3.0},
        {"代码": "000004", "名称": "丁", "成交额": 500.0, "涨跌幅": 0.0},
    ]

    class FakeAkshare:
        @staticmethod
        def stock_zh_a_spot_em() -> FakeFrame:
            return FakeFrame(raw_rows)

    with mock.patch.object(dr, "_load_akshare", return_value=FakeAkshare):
        result = dr.get_turnover_top(2)

    assert result["requested_top_n"] == 2
    assert result["sample_size"] == 2
    assert result["total_amount"] == 5_000.0
    assert result["median_amount"] == 2_500.0
    assert result["market_amount_share"] == 0.7692
    assert result["up_count"] == 1
    assert result["down_count"] == 1
    assert result["flat_count"] == 0
    assert result["mean_change_pct"] == 0.5

    encoded = json.dumps(result, ensure_ascii=False)
    for forbidden in ("000001", "000002", "000003", "000004", "甲", "乙", "丙", "丁"):
        assert forbidden not in encoded
    assert "stocks" not in result
    assert "ranking" not in result


@pytest.mark.parametrize("limit", [0, -1, 1.5, True, "20"])
def test_turnover_limit_must_be_a_positive_integer(limit: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        dr.get_turnover_top(limit)  # type: ignore[arg-type]


def test_turnover_empty_result_is_not_cached() -> None:
    calls = 0

    class FakeAkshare:
        @staticmethod
        def stock_zh_a_spot_em() -> FakeFrame:
            nonlocal calls
            calls += 1
            return FakeFrame([])

    with mock.patch.object(dr, "_load_akshare", return_value=FakeAkshare):
        assert dr.get_turnover_top() == {}
        assert dr.get_turnover_top() == {}
    assert calls == 2


def test_daily_review_combines_independently_cached_sections() -> None:
    with (
        mock.patch.object(dr, "get_overview", return_value={"sentiment": {}}),
        mock.patch.object(dr, "get_short_term_emotion", return_value={"limit_up_count": 1}),
        mock.patch.object(dr, "get_turnover_top", return_value={"sample_size": 20}) as turnover,
    ):
        result = dr.get_daily_review(20)

    turnover.assert_called_once_with(20)
    assert result["overview"] == {"sentiment": {}}
    assert result["short_term_emotion"] == {"limit_up_count": 1}
    assert result["turnover"] == {"sample_size": 20}
    assert result["updated"]

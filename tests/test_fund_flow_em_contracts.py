"""fund_flow_eastmoney 的合同/解析契约测试。

设计原则：
  - 不联网，全部用 unittest.mock 伪造 requests 响应。
  - 覆盖每个 reportName 端点与 fund flow / 互动易等补充端点。
  - 验证字段归一、类型、空结果降级与缓存命中。
  - 保持幂等：多次 mock 相同 fixture 返回一致结果。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


FAKE_CACHE_ROOT = Path(__file__).resolve().parent / ".tmp_cache_fund_flow_em"


def _setup_mocked_module() -> tuple:
    # 强制注入缓存目录与限流，避免真实网络与串行等待影响单测速度
    env_patch = {
        "NASDX_DATA_CACHE": str(FAKE_CACHE_ROOT),
        "EM_GET_RATE_SEC": "0.0",
    }
    mod = __import__("nasdx.fund_flow_eastmoney", fromlist=[
        "margin_trading", "block_trade", "holder_num_change",
        "dividend_history", "stock_fund_flow_120d",
        "dragon_tiger_board", "lockup_expiry",
        "sector_stock_list", "hot_concepts", "investor_qa",
        "get_margin_trading_summary", "get_fund_flow_summary",
        "get_dividend_yield_summary", "get_all_fundamentals",
        "em_get",
    ])
    return mod, env_patch


def _fake_em_get_response(rows: list[dict]) -> dict:
    return {
        "success": True,
        "result": rows,
        "data": rows,
        "totalCount": len(rows),
    }


def _fixture_margin_trading() -> list[dict]:
    return [
        {
            "SECURITY_CODE": "600519",
            "TRADE_DATE": "2025-06-01",
            "FIN_BALANCE": "1200000000",
            "RTO_BALANCE": "30000000",
            "FIN_NET_BUY": "50000000",
            "RTO_NET_BUY": "-2000000",
        },
        {
            "SECURITY_CODE": "600519",
            "TRADE_DATE": "2025-05-31",
            "FIN_BALANCE": "1150000000",
            "RTO_BALANCE": "32000000",
            "FIN_NET_BUY": "30000000",
            "RTO_NET_BUY": "-1000000",
        },
    ]


def _fixture_block_trade() -> list[dict]:
    return [
        {
            "SECURITY_CODE": "000001",
            "TRADE_DATE": "2025-05-20",
            "BUYER_NAME": "机构专用",
            "SELLER_NAME": "华泰证券",
            "TRADE_PRICE": "12.5",
            "PRECEIVE_PRICE": "12.0",
            "RATIO": "-4.17",
        },
    ]


def _fixture_holder_num() -> list[dict]:
    return [
        {
            "SECURITY_CODE": "600036",
            "REPORT_DATE": "2025-03-31",
            "HOLDER_NUM": "180000",
            "HOLDER_CHANGE_RATIO": "-2.3",
        },
    ]


def _fixture_dividend() -> list[dict]:
    return [
        {
            "SECURITY_CODE": "600519",
            "PUBLIC_DATE": "2025-04-10",
            "IMPLEMENT_DATE": "2025-05-01",
            "PER_SHARE_BONUS": "0.0",
            "PER_SHARE_TRANSFER": "0.0",
            "PER_SHARE_DIVIDEND": "1.8888",
        },
    ]


def _fixture_fund_flow() -> list[str]:
    return [
        "2025-06-01,100000000,20000000,-5000000,-80000000,-10000000,45.2,8.5,-2.1,-36.1,-4.5",
        "2025-05-31,80000000,10000000,-10000000,-70000000,-20000000,40.1,5.0,-5.0,-35.0,-10.0",
    ]


def _fixture_dragon_tiger() -> list[dict]:
    return [
        {
            "SECURITY_CODE": "002594",
            "TRADE_DATE": "2025-05-28",
            "BUY_AMOUNT": "200000000",
            "SELL_AMOUNT": "150000000",
            "NET_BUY_AMOUNT": "50000000",
            "REASON": "日振幅达15%的前5只",
            "SEAT_BUY": "机构专用",
            "SEAT_SELL": "华泰证券南京西路",
        },
    ]


def _fixture_lockup() -> list[dict]:
    return [
        {
            "SECURITY_CODE": "300750",
            "LIMITED_SHARES_DATE": "2025-07-01",
            "LIMITED_SHARES_NUM": "50000000",
            "LIMITED_SHARES_VALUE": "800000000",
            "LIMITED_SHARES_RATIO": "5.2",
        },
    ]


def _fixture_hot_concepts() -> list[dict]:
    return [
        {
            "name": "人工智能",
            "changePercent": "3.2",
            "leaderName": "中科曙光",
            "count": "88",
        },
    ]


def _fixture_interactive_qa() -> list[dict]:
    return [
        {
            "stockCode": "600519",
            "stockName": "贵州茅台",
            "date": "2025-05-15",
            "question": "请问公司Q1业绩情况？",
            "answer": "公司2025年一季度实现营业收入XX亿元...",
        },
    ]


@pytest.fixture(autouse=True)
def _clean_fake_cache():
    FAKE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    yield
    import shutil
    shutil.rmtree(FAKE_CACHE_ROOT, ignore_errors=True)


class TestEmGet:
    def test_em_get_calls_correct_endpoint(self):
        mod, _ = _setup_mocked_module()
        fake = _fake_em_get_response([])
        with patch("nasdx.fund_flow_eastmoney.requests.get") as m:
            resp = MagicMock()
            resp.json.return_value = fake
            resp.raise_for_status.return_value = None
            m.return_value = resp
            mod.em_get({"reportName": "RPT_FAKE"})
        call = m.call_args
        assert call[0][0] == "https://datacenter-web.eastmoney.com/api/data/v1/get"
        assert call[1]["params"]["reportName"] == "RPT_FAKE"


class TestMarginTrading:
    def test_returns_normalized_rows(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_margin_trading()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = _fake_em_get_response(fake)
            out = mod.margin_trading("600519", days=10)
        assert len(out) == 2
        assert out[0]["code"] == "600519"
        assert out[0]["date"] == "2025-06-01"
        assert abs(out[0]["fin_balance"] - 1200000000.0) < 0.01
        assert abs(out[0]["rto_balance"] - 30000000.0) < 0.01
        assert abs(out[0]["fin_net_buy"] - 50000000.0) < 0.01
        assert abs(out[0]["rto_net_buy"] - (-2000000.0)) < 0.01

    def test_empty_result(self):
        mod, _ = _setup_mocked_module()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = {"success": True, "result": [], "data": []}
            out = mod.margin_trading("999999")
        assert out == []

    def test_em_get_exception_degrades(self):
        mod, _ = _setup_mocked_module()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.side_effect = RuntimeError("boom")
            out = mod.margin_trading("600519")
        assert out == []


class TestBlockTrade:
    def test_returns_normalized(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_block_trade()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = _fake_em_get_response(fake)
            out = mod.block_trade("000001")
        assert len(out) == 1
        assert out[0]["code"] == "000001"
        assert out[0]["buyer"] == "机构专用"
        assert out[0]["price"] == 12.5
        assert abs(out[0]["discount_pct"] - (-4.17)) < 0.01


class TestHolderNumChange:
    def test_returns_normalized(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_holder_num()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = _fake_em_get_response(fake)
            out = mod.holder_num_change("600036")
        assert len(out) == 1
        assert out[0]["code"] == "600036"
        assert out[0]["holder_num"] == 180000
        assert abs(out[0]["change_ratio"] - (-2.3)) < 0.01


class TestDividendHistory:
    def test_returns_normalized(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_dividend()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = _fake_em_get_response(fake)
            out = mod.dividend_history("600519")
        assert len(out) == 1
        assert out[0]["code"] == "600519"
        assert out[0]["public_date"] == "2025-04-10"
        assert out[0]["implement_date"] == "2025-05-01"
        assert abs(out[0]["per_share_dividend"] - 1.8888) < 0.001


class TestStockFundFlow:
    def test_parses_kline_columns(self):
        mod, _ = _setup_mocked_module()
        rows = _fixture_fund_flow()
        fake = {"data": {"klines": rows}}
        with patch("nasdx.fund_flow_eastmoney.requests.get") as m:
            resp = MagicMock()
            resp.json.return_value = fake
            resp.raise_for_status.return_value = None
            m.return_value = resp
            out = mod.stock_fund_flow_120d("600519")
        assert len(out) == 2
        assert out[0]["code"] == "600519"
        assert out[0]["date"] == "2025-06-01"
        assert abs(out[0]["main_net_inflow"] - 100000000.0) < 0.01
        assert abs(out[0]["super_large_net_inflow"] - (-10000000.0)) < 0.01

    def test_network_failure_degrades(self):
        mod, _ = _setup_mocked_module()
        with patch("nasdx.fund_flow_eastmoney.requests.get") as m:
            m.side_effect = RuntimeError("boom")
            out = mod.stock_fund_flow_120d("000001")
        assert out == []


class TestDragonTigerBoard:
    def test_returns_normalized(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_dragon_tiger()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = _fake_em_get_response(fake)
            out = mod.dragon_tiger_board("002594")
        assert len(out) == 1
        assert out[0]["code"] == "002594"
        assert out[0]["date"] == "2025-05-28"
        assert out[0]["reason"] == "日振幅达15%的前5只"
        assert abs(out[0]["net_buy_amount"] - 50000000.0) < 0.01


class TestLockupExpiry:
    def test_returns_normalized(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_lockup()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = _fake_em_get_response(fake)
            out = mod.lockup_expiry("300750")
        assert len(out) == 1
        assert out[0]["code"] == "300750"
        assert out[0]["date"] == "2025-07-01"
        assert abs(out[0]["value"] - 800000000.0) < 0.01
        assert abs(out[0]["ratio"] - 5.2) < 0.01


class TestSectorStockList:
    def test_filters_by_sector_keyword(self):
        mod, _ = _setup_mocked_module()
        fake = _fake_em_get_response([])
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = fake
            out = mod.sector_stock_list("白酒")
        call_params = m.call_args[1]["params"]
        assert "白酒" in call_params["filter"]
        assert out == []


class TestHotConcepts:
    def test_returns_rank(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_hot_concepts()
        with patch("nasdx.fund_flow_eastmoney.requests.get") as m:
            resp = MagicMock()
            resp.json.return_value = {"data": fake, "list": fake}
            resp.raise_for_status.return_value = None
            m.return_value = resp
            out = mod.hot_concepts(days=5, limit=10)
        assert len(out) == 1
        assert out[0]["name"] == "人工智能"
        assert abs(out[0]["change_pct"] - 3.2) < 0.01
        assert out[0]["lead_stock"] == "中科曙光"


class TestInvestorQa:
    def test_filters_by_code(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_interactive_qa()
        with patch("nasdx.fund_flow_eastmoney.requests.get") as m:
            resp = MagicMock()
            resp.json.return_value = {"data": fake, "list": fake}
            resp.raise_for_status.return_value = None
            m.return_value = resp
            out = mod.investor_qa("code:600519 业绩")
        assert len(out) == 1
        assert out[0]["code"] == "600519"
        assert out[0]["name"] == "贵州茅台"
        assert "业绩" in out[0]["question"] or "业绩" in out[0]["answer"]

    def test_text_only_query(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_interactive_qa()
        with patch("nasdx.fund_flow_eastmoney.requests.get") as m:
            resp = MagicMock()
            resp.json.return_value = {"data": fake}
            resp.raise_for_status.return_value = None
            m.return_value = resp
            out = mod.investor_qa("业绩情况")
        call_params = m.call_args[1]["params"]
        assert call_params["keyword"] == "业绩情况"
        assert call_params["stockCode"] == ""


class TestSummaries:
    def test_margin_trading_summary_structure(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_margin_trading()
        with patch.object(mod, "margin_trading", return_value=fake):
            out = mod.get_margin_trading_summary("600519", days=10)
        assert out["code"] == "600519"
        assert out["days"] == 10
        assert out["rows"] == 2
        assert out["latest_date"] == "2025-06-01"
        assert out["total_net_buy"] == 80000000.0
        assert out["net_buy_trend_up"] is True

    def test_fund_flow_summary_structure(self):
        mod, _ = _setup_mocked_module()
        rows = [{
            "date": "2025-06-01",
            "code": "600519",
            "main_net_inflow": 1000.0,
            "small_net_inflow": 100.0,
            "mid_net_inflow": -50.0,
            "large_net_inflow": -800.0,
            "super_large_net_inflow": -250.0,
        }]
        with patch.object(mod, "stock_fund_flow_120d", return_value=rows):
            out = mod.get_fund_flow_summary("600519", days=1)
        assert out["rows"] == 1
        assert out["total_main_net_inflow"] == 1000.0
        assert out["main_trend_up"] is True

    def test_dividend_summary_structure(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_dividend()
        with patch.object(mod, "dividend_history", return_value=fake):
            out = mod.get_dividend_yield_summary("600519", years=5)
        assert out["rows"] == 1
        assert out["dividend_count"] == 1
        assert abs(out["total_cash_per_share"] - 1.8888) < 0.001

    def test_get_all_fundamentals_aggregates(self):
        mod, _ = _setup_mocked_module()
        with patch.object(mod, "margin_trading", return_value=[]), \
             patch.object(mod, "block_trade", return_value=[]), \
             patch.object(mod, "holder_num_change", return_value=[]), \
             patch.object(mod, "dividend_history", return_value=[]), \
             patch.object(mod, "stock_fund_flow_120d", return_value=[]), \
             patch.object(mod, "dragon_tiger_board", return_value=[]), \
             patch.object(mod, "lockup_expiry", return_value=[]):
            out = mod.get_all_fundamentals("600519")
        assert out["code"] == "600519"
        assert "margin_trading" in out
        assert "dividend_summary" in out
        assert "fund_flow_summary" in out


class TestCacheBehavior:
    def test_em_get_cached(self):
        mod, _ = _setup_mocked_module()
        fake = _fixture_margin_trading()
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.return_value = _fake_em_get_response(fake)
            first = mod.margin_trading("600519")
            second = mod.margin_trading("600519")
        assert m.call_count == 1
        assert first == second
        # 再次调用应命中缓存，不再发请求
        third = mod.margin_trading("600519")
        assert m.call_count == 1
        assert third == first

    def test_cache_key_different_codes(self):
        mod, _ = _setup_mocked_module()
        fake_a = [{"SECURITY_CODE": "600519", "TRADE_DATE": "2025-06-01", "FIN_BALANCE": "1", "RTO_BALANCE": "0", "FIN_NET_BUY": "0", "RTO_NET_BUY": "0"}]
        fake_b = [{"SECURITY_CODE": "000001", "TRADE_DATE": "2025-06-01", "FIN_BALANCE": "2", "RTO_BALANCE": "0", "FIN_NET_BUY": "0", "RTO_NET_BUY": "0"}]
        with patch("nasdx.fund_flow_eastmoney.em_get") as m:
            m.side_effect = [_fake_em_get_response(fake_a), _fake_em_get_response(fake_b)]
            out_a = mod.margin_trading("600519")
            out_b = mod.margin_trading("000001")
        assert m.call_count == 2
        assert out_a[0]["code"] == "600519"
        assert out_b[0]["code"] == "000001"

"""nasdx.debate 的离线结构化契约测试。"""

from __future__ import annotations

import copy
import json
import threading
from unittest import mock

import pytest

import nasdx.debate as debate


@pytest.fixture
def stock_data() -> dict:
    return {
        "name": "示例公司",
        "sector_name": "软件",
        "current_price": 12.5,
        "change_pct": 1.2,
        "amount": 100_000_000,
        "indicators": {
            "ma5": 12.2,
            "ma20": 11.8,
            "rsi": 58,
            "macd_bar": 0.12,
            "boll_upper": 13.5,
            "boll_lower": 10.2,
            "vol_ratio": 1.1,
        },
        "fund_flow": [{"日期": "2026-08-18", "主力净流入-净额": 1_000_000}],
        "pe_ttm": 24.5,
        "pb": 3.2,
        "financials": {"revenue_yoy": 8.5, "net_profit_yoy": 6.2},
        "concepts": ["工业软件"],
        "announcements": [{"date": "2026-08-01", "title": "半年度报告"}],
        "data_source": "fixture",
        "recommendation": "这条非事实字段不得进入底稿",
        "signal": "bullish",
    }


def _bull_payload() -> dict:
    return {
        "thesis": "收入增长的持续性可以通过后续报告验证",
        "evidence": [
            {"claim": "已披露收入同比为正", "fact_refs": ["financials"]},
            {"claim": "短期均线高于中期均线", "fact_refs": ["trend"]},
        ],
        "assumptions": ["财务口径保持可比"],
        "data_gaps": ["在手订单未披露"],
    }


def _bear_payload() -> dict:
    return {
        "thesis": "估值与利润增速的匹配度仍需验证",
        "evidence": [
            {"claim": "现有估值倍数高于利润增速数值", "fact_refs": ["valuation", "financials"]},
            {"claim": "资金流样本只覆盖单日", "fact_refs": ["fund_flow"]},
        ],
        "assumptions": ["估值口径与报告期一致"],
        "data_gaps": ["同业可比估值缺失"],
    }


def _moderator_payload() -> dict:
    return {
        "consensus": ["双方都认可已披露财务数据是当前主要依据"],
        "disagreements": [
            {
                "topic": "增长质量",
                "bull_view": "收入与趋势数据可形成较强解释",
                "bear_view": "利润增速和样本长度不足以确认持续性",
                "cause": "interpretation",
            }
        ],
        "verification_checklist": [
            {
                "question": "收入增长能否延续",
                "data": "下一期收入、利润与订单",
                "source": "公司定期报告",
                "timing": "下一报告期",
            }
        ],
        "data_gaps": ["同业可比数据"],
    }


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def ask_json(self, messages: list[dict], *, system: str) -> dict:
        with self.lock:
            self.calls.append({"messages": copy.deepcopy(messages), "system": system})
        if "多方研究员" in system:
            return _bull_payload()
        if "空方研究员" in system:
            return _bear_payload()
        if "中立主持" in system:
            return _moderator_payload()
        raise AssertionError("unknown role")


def _all_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_dossier_has_thirteen_whitelisted_sections(stock_data: dict) -> None:
    dossier = debate.build_fact_dossier(
        "600000",
        stock_data,
        data_as_of="2026-08-19T10:00:00+08:00",
    )
    assert dossier["schema_version"] == debate.DOSSIER_SCHEMA_VERSION
    assert list(dossier["sections"]) == list(debate._SECTION_NAMES)
    assert len(dossier["sections"]) == 13
    assert dossier["code"] == "600000"
    assert dossier["data_as_of"] == "2026-08-19T10:00:00+08:00"
    encoded = _all_text(dossier)
    assert "recommendation" not in encoded
    assert "bullish" not in encoded
    assert "这条非事实字段" not in encoded


def test_dossier_preserves_zero_as_objective_fact() -> None:
    dossier = debate.build_fact_dossier(
        "600000",
        {"name": "示例", "current_price": 0, "pe_ttm": 0},
    )
    assert dossier["sections"]["quote"]["current_price"] == 0
    assert dossier["sections"]["valuation"]["pe_ttm"] == 0
    assert "quote" not in dossier["missing_sections"]
    assert "valuation" not in dossier["missing_sections"]


@pytest.mark.parametrize("code", ["", "600000;DROP", "../600000", "代码 600000"])
def test_dossier_rejects_unsafe_stock_code(code: str, stock_data: dict) -> None:
    with pytest.raises(ValueError, match="unsupported characters"):
        debate.build_fact_dossier(code, stock_data)


def test_build_dossier_from_latest_reuses_existing_data_loader(stock_data: dict) -> None:
    loaded = {"date": "20260819", "sectors": []}
    with (
        mock.patch.object(debate, "load_latest_data", return_value=loaded) as load,
        mock.patch.object(debate, "get_stock_data", return_value=stock_data) as get,
    ):
        dossier = debate.build_fact_dossier_from_latest("600000")
    load.assert_called_once_with()
    get.assert_called_once_with(loaded, "600000")
    assert dossier["data_as_of"] == "20260819"


def test_three_roles_share_one_dossier_and_moderator_sees_both(
    stock_data: dict,
) -> None:
    client = FakeLLM()
    result = debate.run_debate("600000", stock_data, llm_client=client)

    assert result["schema_version"] == debate.DEBATE_SCHEMA_VERSION
    assert result["status"] == "complete"
    assert result["roles"]["bull"]["thesis"] == _bull_payload()["thesis"]
    assert result["roles"]["bear"]["thesis"] == _bear_payload()["thesis"]
    assert result["moderator"]["consensus"] == _moderator_payload()["consensus"]
    assert len(client.calls) == 3

    bull_call = next(call for call in client.calls if "多方研究员" in call["system"])
    bear_call = next(call for call in client.calls if "空方研究员" in call["system"])
    moderator_call = next(call for call in client.calls if "中立主持" in call["system"])
    assert bull_call["messages"][0]["content"] == bear_call["messages"][0]["content"]
    moderator_context = moderator_call["messages"][0]["content"]
    assert _bull_payload()["thesis"] in moderator_context
    assert _bear_payload()["thesis"] in moderator_context


def test_stream_event_order_is_stable(stock_data: dict) -> None:
    events = list(
        debate.run_debate_stream("600000", stock_data, llm_client=FakeLLM())
    )
    assert [event["type"] for event in events] == [
        "dossier",
        "stage_done",
        "stage_done",
        "stage_done",
        "done",
    ]
    assert [event.get("role") for event in events[1:4]] == [
        "bull",
        "bear",
        "moderator",
    ]


def test_no_action_advice_or_direction_fields_in_result(stock_data: dict) -> None:
    result = debate.run_debate("600000", stock_data, llm_client=FakeLLM())
    encoded = _all_text(result)
    for forbidden in (
        "买入",
        "卖出",
        "加仓",
        "减仓",
        "目标价",
        "止损",
        "止盈",
        '"signal"',
        '"rating"',
        '"recommendation"',
        '"verdict"',
    ):
        assert forbidden not in encoded


def test_illegal_json_shape_triggers_one_self_repair(stock_data: dict) -> None:
    class RepairingLLM(FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.bull_attempts = 0

        def ask_json(self, messages: list[dict], *, system: str) -> dict:
            if "多方研究员" in system:
                self.bull_attempts += 1
                with self.lock:
                    self.calls.append({"messages": copy.deepcopy(messages), "system": system})
                if self.bull_attempts == 1:
                    return {"raw": "not valid structured output"}
                return _bull_payload()
            return super().ask_json(messages, system=system)

    client = RepairingLLM()
    result = debate.run_debate("600000", stock_data, llm_client=client)
    assert result["status"] == "complete"
    assert client.bull_attempts == 2
    bull_calls = [call for call in client.calls if "多方研究员" in call["system"]]
    assert len(bull_calls[1]["messages"]) == 2
    assert "重新读取系统 schema" in bull_calls[1]["messages"][-1]["content"]


def test_prohibited_advice_triggers_repair(stock_data: dict) -> None:
    class AdviceThenSafe(FakeLLM):
        def __init__(self) -> None:
            super().__init__()
            self.bull_attempts = 0

        def ask_json(self, messages: list[dict], *, system: str) -> dict:
            if "多方研究员" in system:
                self.bull_attempts += 1
                if self.bull_attempts == 1:
                    payload = _bull_payload()
                    payload["thesis"] = "建议买入并设置目标价"
                    return payload
                return _bull_payload()
            return super().ask_json(messages, system=system)

    client = AdviceThenSafe()
    result = debate.run_debate("600000", stock_data, llm_client=client)
    assert result["status"] == "complete"
    assert client.bull_attempts == 2
    assert "建议买入" not in _all_text(result)


def test_forbidden_direction_key_is_rejected() -> None:
    payload = _bull_payload()
    payload["signal"] = "bullish"
    with pytest.raises(debate.DebateContractError, match="forbidden fields"):
        debate._assert_no_advice(payload)


def test_unknown_fact_reference_is_rejected() -> None:
    payload = _bull_payload()
    payload["evidence"][0]["fact_refs"] = ["invented_source"]
    with pytest.raises(debate.DebateContractError, match="known dossier"):
        debate._validate_researcher(payload, dossier_sections=set(debate._SECTION_NAMES))


def test_one_failed_researcher_degrades_without_poisoning_moderator(
    stock_data: dict,
) -> None:
    class BearFails(FakeLLM):
        def ask_json(self, messages: list[dict], *, system: str) -> dict:
            if "空方研究员" in system:
                raise RuntimeError("provider unavailable")
            return super().ask_json(messages, system=system)

    result = debate.run_debate("600000", stock_data, llm_client=BearFails())
    assert result["status"] == "partial"
    assert result["roles"]["bull"]["status"] == "complete"
    assert result["roles"]["bear"]["status"] == "unavailable"
    assert result["roles"]["bear"]["evidence"] == []
    assert result["moderator"]["status"] == "complete"
    assert "provider unavailable" not in _all_text(result)


def test_failed_moderator_returns_neutral_structural_fallback(
    stock_data: dict,
) -> None:
    class ModeratorFails(FakeLLM):
        def ask_json(self, messages: list[dict], *, system: str) -> dict:
            if "中立主持" in system:
                return {"recommendation": "invalid"}
            return super().ask_json(messages, system=system)

    result = debate.run_debate("600000", stock_data, llm_client=ModeratorFails())
    assert result["status"] == "partial"
    assert result["moderator"]["status"] == "unavailable"
    assert result["moderator"]["consensus"] == []
    assert result["moderator"]["disagreements"] == []
    assert result["moderator"]["verification_checklist"] == []


def test_empty_objective_dossier_stops_before_llm() -> None:
    client = mock.Mock()
    with pytest.raises(ValueError, match="底稿为空"):
        debate.run_debate("600000", {"name": "只有名称"}, llm_client=client)
    client.ask_json.assert_not_called()


def test_moderator_cause_enum_is_strict() -> None:
    payload = _moderator_payload()
    payload["disagreements"][0]["cause"] = "bull_wins"
    with pytest.raises(debate.DebateContractError, match="incomplete"):
        debate._validate_moderator(payload)

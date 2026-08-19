"""基于同一份客观底稿的结构化多空调论。

该模块与现有交易型 Battle 环境用途不同：这里只沉淀多空证据、共识、分歧和
后续验证路径，不产生方向信号、目标价、仓位或操作建议。输入事实采用白名单
抽取，LLM 只能消费底稿，不能把自身知识冒充项目数据。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from nasdx.data_loader import get_stock_data, load_latest_data
from nasdx.llm import llm


BEIJING = timezone(timedelta(hours=8))
DOSSIER_SCHEMA_VERSION = "nasdx_debate_dossier.v1"
DEBATE_SCHEMA_VERSION = "nasdx_structured_debate.v1"
MAX_ROLE_ATTEMPTS = 2

ROLE_BULL = "bull"
ROLE_BEAR = "bear"
ROLE_MODERATOR = "moderator"

_ROLE_LABELS = {
    ROLE_BULL: "多方研究员",
    ROLE_BEAR: "空方研究员",
    ROLE_MODERATOR: "中立主持",
}

_FORBIDDEN_CONTENT = re.compile(
    r"(买入|卖出|加仓|减仓|建仓|清仓|止损|止盈|目标价|仓位建议|"
    r"推荐.{0,6}(股票|标的|操作)|预计股价|预测股价|将会上涨|将会下跌)",
    re.IGNORECASE,
)
_FORBIDDEN_KEYS = {
    "signal",
    "rating",
    "recommendation",
    "action",
    "target_price",
    "position",
    "verdict",
    "winner",
}

_SECTION_NAMES = (
    "identity",
    "quote",
    "trend",
    "momentum",
    "volatility",
    "volume",
    "fund_flow",
    "valuation",
    "financials",
    "sector",
    "concepts",
    "events",
    "data_context",
)


class DebateContractError(ValueError):
    """结构化角色输出违反 schema 或产品边界。"""


class DebateGenerationError(RuntimeError):
    """角色在有界重试后仍无法生成合规结构。"""


def _now_text() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _normalize_code(stock_code: Any) -> str:
    code = str(stock_code or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9._-]{1,32}", code):
        raise ValueError("stock_code contains unsupported characters")
    return code


def _meaningful(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, Mapping):
        return any(_meaningful(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_meaningful(item) for item in value)
    return True


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """把事实压缩成可 JSON 序列化的有界结构。"""
    if depth >= 5:
        return str(value)[:500]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            result[str(key)] = _bounded(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[-10:]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _pick(source: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        if key in source and _meaningful(source.get(key)):
            result[key] = _bounded(source.get(key))
    return result


def _indicators(stock_data: Mapping[str, Any]) -> Mapping[str, Any]:
    value = stock_data.get("indicators")
    return value if isinstance(value, Mapping) else {}


def build_fact_dossier(
    stock_code: Any,
    stock_data: Mapping[str, Any],
    *,
    data_as_of: Optional[str] = None,
) -> dict[str, Any]:
    """从现有 NASDX 行情对象白名单抽取 13 个客观事实分区。"""
    if not isinstance(stock_data, Mapping):
        raise TypeError("stock_data must be a mapping")
    code = _normalize_code(stock_code)
    indicators = _indicators(stock_data)

    sections: dict[str, Any] = {
        "identity": _pick(
            stock_data,
            ("name", "sector_name", "industry", "listing_date"),
        ),
        "quote": _pick(
            stock_data,
            (
                "current_price",
                "price",
                "close",
                "open",
                "high",
                "low",
                "pre_close",
                "change",
                "change_pct",
            ),
        ),
        "trend": _pick(indicators, ("ma5", "ma10", "ma20", "ma60")),
        "momentum": _pick(
            indicators,
            ("rsi", "rsi14", "macd_bar", "dif", "dea", "kdj_k", "kdj_d"),
        ),
        "volatility": _pick(
            indicators,
            ("boll_upper", "boll_middle", "boll_lower", "atr", "volatility"),
        ),
        "volume": {
            **_pick(stock_data, ("volume", "amount", "turnover", "turnover_rate")),
            **_pick(indicators, ("vol_ratio", "up_days_20")),
        },
        "fund_flow": _pick(
            stock_data,
            (
                "fund_flow",
                "main_net_inflow",
                "super_large_net",
                "large_net",
            ),
        ),
        "valuation": _pick(
            stock_data,
            (
                "pe",
                "pe_ttm",
                "pb",
                "ps",
                "market_cap",
                "total_market_cap",
                "circulating_market_cap",
            ),
        ),
        "financials": _pick(
            stock_data,
            ("financials", "fundamentals", "financial_indicators"),
        ),
        "sector": _pick(
            stock_data,
            (
                "sector_name",
                "sector_signal",
                "sector_change_pct",
                "sector_strength",
                "board",
            ),
        ),
        "concepts": _pick(stock_data, ("concepts", "tags")),
        "events": _pick(stock_data, ("announcements", "news")),
        "data_context": _pick(
            stock_data,
            (
                "source",
                "data_source",
                "date",
                "data_as_of",
                "fetched_at",
                "quality",
                "status",
            ),
        ),
    }
    sections = {name: _bounded(sections.get(name, {})) for name in _SECTION_NAMES}
    missing = [name for name in _SECTION_NAMES if not _meaningful(sections[name])]
    return {
        "schema_version": DOSSIER_SCHEMA_VERSION,
        "code": code,
        "name": str(stock_data.get("name") or ""),
        "data_as_of": str(data_as_of or stock_data.get("data_as_of") or _now_text()),
        "sections": sections,
        "missing_sections": missing,
    }


def build_fact_dossier_from_latest(
    stock_code: Any,
    *,
    data: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """复用现有 data_loader，从本地最新行情快照构建底稿。"""
    code = _normalize_code(stock_code)
    loaded = dict(data) if isinstance(data, Mapping) else load_latest_data()
    stock_data = get_stock_data(loaded, code)
    if not stock_data:
        raise ValueError(f"股票 {code} 不在本地行情快照中")
    return build_fact_dossier(
        code,
        stock_data,
        data_as_of=str(loaded.get("date") or stock_data.get("data_as_of") or ""),
    )


def _dossier_text(dossier: Mapping[str, Any]) -> str:
    lines = [
        f"【客观事实底稿 {dossier.get('code', '')}】",
        f"数据时间：{dossier.get('data_as_of', '')}",
        "以下是唯一允许引用的项目数据；空分区必须明确视为数据缺口。",
    ]
    sections = dossier.get("sections")
    if isinstance(sections, Mapping):
        for name in _SECTION_NAMES:
            payload = sections.get(name, {})
            rendered = json.dumps(payload, ensure_ascii=False, default=str)
            lines.append(f"## {name}\n{rendered[:1800]}")
    missing = dossier.get("missing_sections") or []
    lines.append("## missing_sections\n" + json.dumps(missing, ensure_ascii=False))
    return "\n".join(lines)


_COMMON_SYSTEM = """你在 NASDX 结构化多空调论模块中工作。
只能引用用户消息中的客观事实底稿；底稿没有的数据必须列入 data_gaps。
不得使用外部记忆补数字，不得输出方向信号、评级、操作建议、目标价、仓位、
止损止盈或对未来股价的预测。只输出一个 JSON 对象，不要 Markdown。"""

_ROLE_SYSTEMS = {
    ROLE_BULL: _COMMON_SYSTEM
    + """
你是多方研究员，只负责整理“支持基本面或经营质量较强解释”的证据。
JSON 字段严格为：
{"thesis":"一句可证伪的研究命题","evidence":[{"claim":"论点","fact_refs":["分区名"]}],
"assumptions":["成立前提"],"data_gaps":["缺失数据"]}""",
    ROLE_BEAR: _COMMON_SYSTEM
    + """
你是空方研究员，只负责整理“支持风险或不确定性解释”的证据。
JSON 字段严格为：
{"thesis":"一句可证伪的研究命题","evidence":[{"claim":"疑点","fact_refs":["分区名"]}],
"assumptions":["成立前提"],"data_gaps":["缺失数据"]}""",
    ROLE_MODERATOR: _COMMON_SYSTEM
    + """
你是中立主持，不裁决哪一方正确。只归纳共识、真正分歧与验证路径。
JSON 字段严格为：
{"consensus":["共同认可的事实"],
"disagreements":[{"topic":"主题","bull_view":"多方解释","bear_view":"空方解释",
"cause":"data_gap|interpretation"}],
"verification_checklist":[{"question":"待验证问题","data":"所需数据","source":"去哪里看",
"timing":"何时能看到"}],"data_gaps":["关键缺口"]}""",
}


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _assert_no_advice(payload: Mapping[str, Any]) -> None:
    lowered_keys = {str(key).lower() for key in payload}
    forbidden = lowered_keys & _FORBIDDEN_KEYS
    if forbidden:
        raise DebateContractError(f"forbidden fields: {sorted(forbidden)}")
    for text in _strings(payload):
        if _FORBIDDEN_CONTENT.search(text):
            raise DebateContractError("role output contains prohibited advice or prediction")


def _clean_text_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise DebateContractError(f"{field} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _validate_researcher(
    payload: Mapping[str, Any],
    *,
    dossier_sections: set[str],
) -> dict[str, Any]:
    _assert_no_advice(payload)
    required = {"thesis", "evidence", "assumptions", "data_gaps"}
    if not required.issubset(payload):
        raise DebateContractError("researcher output missing required fields")
    thesis = str(payload.get("thesis") or "").strip()
    evidence_raw = payload.get("evidence")
    if not thesis or not isinstance(evidence_raw, list):
        raise DebateContractError("researcher thesis/evidence invalid")
    evidence: list[dict[str, Any]] = []
    for item in evidence_raw:
        if not isinstance(item, Mapping):
            raise DebateContractError("evidence item must be an object")
        claim = str(item.get("claim") or "").strip()
        refs = _clean_text_list(item.get("fact_refs"), "fact_refs")
        if not claim or not refs or any(ref not in dossier_sections for ref in refs):
            raise DebateContractError("evidence must cite known dossier sections")
        evidence.append({"claim": claim, "fact_refs": refs})
    if not evidence:
        raise DebateContractError("researcher evidence must not be empty")
    result = {
        "thesis": thesis,
        "evidence": evidence,
        "assumptions": _clean_text_list(payload.get("assumptions"), "assumptions"),
        "data_gaps": _clean_text_list(payload.get("data_gaps"), "data_gaps"),
    }
    _assert_no_advice(result)
    return result


def _validate_moderator(payload: Mapping[str, Any]) -> dict[str, Any]:
    _assert_no_advice(payload)
    required = {
        "consensus",
        "disagreements",
        "verification_checklist",
        "data_gaps",
    }
    if not required.issubset(payload):
        raise DebateContractError("moderator output missing required fields")
    disagreements_raw = payload.get("disagreements")
    checklist_raw = payload.get("verification_checklist")
    if not isinstance(disagreements_raw, list) or not isinstance(checklist_raw, list):
        raise DebateContractError("moderator collections must be lists")

    disagreements: list[dict[str, str]] = []
    for item in disagreements_raw:
        if not isinstance(item, Mapping):
            raise DebateContractError("disagreement item must be an object")
        normalized = {
            "topic": str(item.get("topic") or "").strip(),
            "bull_view": str(item.get("bull_view") or "").strip(),
            "bear_view": str(item.get("bear_view") or "").strip(),
            "cause": str(item.get("cause") or "").strip(),
        }
        if (
            not all(normalized.values())
            or normalized["cause"] not in {"data_gap", "interpretation"}
        ):
            raise DebateContractError("disagreement item is incomplete")
        disagreements.append(normalized)

    checklist: list[dict[str, str]] = []
    for item in checklist_raw:
        if not isinstance(item, Mapping):
            raise DebateContractError("verification item must be an object")
        normalized = {
            "question": str(item.get("question") or "").strip(),
            "data": str(item.get("data") or "").strip(),
            "source": str(item.get("source") or "").strip(),
            "timing": str(item.get("timing") or "").strip(),
        }
        if not all(normalized.values()):
            raise DebateContractError("verification item is incomplete")
        checklist.append(normalized)

    result = {
        "consensus": _clean_text_list(payload.get("consensus"), "consensus"),
        "disagreements": disagreements,
        "verification_checklist": checklist,
        "data_gaps": _clean_text_list(payload.get("data_gaps"), "data_gaps"),
    }
    _assert_no_advice(result)
    return result


def _call_role(
    role: str,
    user_content: str,
    *,
    llm_client: Any,
    dossier_sections: set[str],
) -> dict[str, Any]:
    last_error: Optional[Exception] = None
    messages = [{"role": "user", "content": user_content}]
    for attempt in range(1, MAX_ROLE_ATTEMPTS + 1):
        try:
            payload = llm_client.ask_json(messages, system=_ROLE_SYSTEMS[role])
            if not isinstance(payload, Mapping):
                raise DebateContractError("role output must be an object")
            if role == ROLE_MODERATOR:
                return _validate_moderator(payload)
            return _validate_researcher(payload, dossier_sections=dossier_sections)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_ROLE_ATTEMPTS:
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "上次输出未通过结构或产品边界校验。"
                            "请重新读取系统 schema，只输出合规 JSON。"
                        ),
                    },
                ]
    raise DebateGenerationError(
        f"{_ROLE_LABELS[role]} failed after {MAX_ROLE_ATTEMPTS} attempts: "
        f"{type(last_error).__name__ if last_error else 'unknown'}"
    )


def _unavailable_role(role: str, exc: Exception) -> dict[str, Any]:
    return {
        "role": role,
        "label": _ROLE_LABELS[role],
        "status": "unavailable",
        "thesis": "",
        "evidence": [],
        "assumptions": [],
        "data_gaps": [f"{_ROLE_LABELS[role]}生成失败：{type(exc).__name__}"],
    }


def _moderator_fallback(role_results: Mapping[str, Any], dossier: Mapping[str, Any]) -> dict[str, Any]:
    missing_roles = [
        _ROLE_LABELS[role]
        for role in (ROLE_BULL, ROLE_BEAR)
        if (role_results.get(role) or {}).get("status") != "complete"
    ]
    gaps = list(dossier.get("missing_sections") or [])
    gaps.extend(f"{label}观点不可用" for label in missing_roles)
    return {
        "consensus": [],
        "disagreements": [],
        "verification_checklist": [],
        "data_gaps": gaps,
    }


def run_debate_stream(
    stock_code: Any,
    stock_data: Mapping[str, Any],
    *,
    data_as_of: Optional[str] = None,
    llm_client: Any = llm,
):
    """生成结构化阶段事件：dossier → bull/bear → moderator → done。"""
    dossier = build_fact_dossier(stock_code, stock_data, data_as_of=data_as_of)
    nonempty = [
        name
        for name, payload in dossier["sections"].items()
        if name not in {"identity", "data_context"} and _meaningful(payload)
    ]
    if not nonempty:
        raise ValueError("客观事实底稿为空，无法开始调论")
    yield {"type": "dossier", "dossier": dossier}

    dossier_text = _dossier_text(dossier)
    dossier_sections = set(dossier["sections"])
    roles: dict[str, dict[str, Any]] = {}

    def generate(role: str) -> dict[str, Any]:
        try:
            content = _call_role(
                role,
                dossier_text,
                llm_client=llm_client,
                dossier_sections=dossier_sections,
            )
            return {
                "role": role,
                "label": _ROLE_LABELS[role],
                "status": "complete",
                **content,
            }
        except Exception as exc:
            return _unavailable_role(role, exc)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="nasdx-debate") as executor:
        futures = {
            role: executor.submit(generate, role)
            for role in (ROLE_BULL, ROLE_BEAR)
        }
        for role in (ROLE_BULL, ROLE_BEAR):
            roles[role] = futures[role].result()
            yield {"type": "stage_done", **roles[role]}

    moderator_context = (
        dossier_text
        + "\n\n【多方结构化观点】\n"
        + json.dumps(roles[ROLE_BULL], ensure_ascii=False)
        + "\n\n【空方结构化观点】\n"
        + json.dumps(roles[ROLE_BEAR], ensure_ascii=False)
    )
    try:
        moderator = _call_role(
            ROLE_MODERATOR,
            moderator_context,
            llm_client=llm_client,
            dossier_sections=dossier_sections,
        )
        moderator_status = "complete"
    except Exception:
        moderator = _moderator_fallback(roles, dossier)
        moderator_status = "unavailable"
    moderator_result = {
        "role": ROLE_MODERATOR,
        "label": _ROLE_LABELS[ROLE_MODERATOR],
        "status": moderator_status,
        **moderator,
    }
    yield {"type": "stage_done", **moderator_result}

    status = (
        "complete"
        if moderator_status == "complete"
        and all(item["status"] == "complete" for item in roles.values())
        else "partial"
    )
    result = {
        "schema_version": DEBATE_SCHEMA_VERSION,
        "code": dossier["code"],
        "name": dossier["name"],
        "data_as_of": dossier["data_as_of"],
        "status": status,
        "dossier": dossier,
        "roles": roles,
        "moderator": moderator_result,
        "disclaimer": "仅用于整理研究分歧与验证路径，不构成投资建议。",
    }
    yield {"type": "done", "result": result}


def run_debate(
    stock_code: Any,
    stock_data: Mapping[str, Any],
    *,
    data_as_of: Optional[str] = None,
    llm_client: Any = llm,
) -> dict[str, Any]:
    """同步执行一轮三角色调论并返回最终结构。"""
    result: Optional[dict[str, Any]] = None
    for event in run_debate_stream(
        stock_code,
        stock_data,
        data_as_of=data_as_of,
        llm_client=llm_client,
    ):
        if event.get("type") == "done":
            result = event["result"]
    if result is None:
        raise DebateGenerationError("debate did not produce a terminal result")
    return result

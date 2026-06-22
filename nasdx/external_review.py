"""
External review pack for NASDX candidates.

The project does not have authoritative announcement, account, or order-book
connectors. This module makes that boundary explicit by generating source
entry points and pass/fail criteria that must be checked manually before a
candidate can move from research to a real trade decision.
"""
from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import quote


def build_external_review_pack(audits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build manual external review requirements for audited candidates."""
    pack: List[Dict[str, Any]] = []
    for audit in audits:
        candidate = audit.get("candidate", "")
        status_code = audit.get("status_code", "")
        code = str(audit.get("code") or "")
        asset_type = audit.get("type", "")
        pack.append(
            {
                "candidate": candidate,
                "code": code,
                "type": asset_type,
                "review_status": "pending_manual_review",
                "review_gate": _review_gate(status_code),
                "must_pass_before": _must_pass_before(status_code),
                "required_checks": _required_checks(asset_type, status_code),
                "source_links": _source_links(code, asset_type),
                "failure_action": _failure_action(status_code),
            }
        )
    return pack


def _review_gate(status_code: str) -> str:
    if status_code == "trial_candidate":
        return "试错前必须人工复核"
    if status_code == "needs_report":
        return "补深度报告前先查重大事项"
    if status_code == "watch":
        return "观察期复核"
    if status_code == "avoid":
        return "只做风险复核"
    return "数据修复后复核"


def _must_pass_before(status_code: str) -> str:
    if status_code == "trial_candidate":
        return "任何真实下单或加仓前"
    if status_code == "needs_report":
        return "从观察升级为试错前"
    if status_code == "watch":
        return "重新进入试错池前"
    if status_code == "avoid":
        return "重新进入观察池前"
    return "重新生成路线前"


def _required_checks(asset_type: str, status_code: str) -> List[str]:
    checks = [
        "最近公告/财报/停复牌/重大事项没有新增风险红灯",
        "最新行情未放量破位，且没有异常波动或流动性骤降",
        "账户现金、总仓位、单票上限和交易成本满足当前风险画像",
    ]
    if asset_type == "ETF":
        checks.insert(1, "ETF成交额、折溢价、跟踪指数和基金规模没有异常")
    else:
        checks.insert(1, "个股主营逻辑、订单/客户/产能证据与报告假设一致")
    if status_code == "needs_report":
        checks.append("补跑深度报告后 final_signal 转为 bullish，且报告动作不是回避/减仓")
    if status_code == "avoid":
        checks.append("风险红灯解除前不进入试错或观察升级")
    return checks


def _source_links(code: str, asset_type: str) -> List[Dict[str, str]]:
    market = _market_prefix(code)
    links = [
        {
            "label": "巨潮资讯全文搜索",
            "url": f"https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord={quote(code)}",
            "usage": "查公告、财报、停复牌和重大事项",
            "authority": "official_or_regulatory_entry",
        },
        {
            "label": "东方财富行情页",
            "url": f"https://quote.eastmoney.com/{market}{code}.html" if market else f"https://quote.eastmoney.com/search.html?keyword={quote(code)}",
            "usage": "查最新行情、成交额和盘口流动性",
            "authority": "market_data_entry",
        },
    ]
    if asset_type == "ETF":
        links.append(
            {
                "label": "交易所基金信息入口",
                "url": _exchange_fund_url(code),
                "usage": "查基金公告、跟踪标的和交易所披露信息",
                "authority": "exchange_entry",
            }
        )
    else:
        links.append(
            {
                "label": "交易所上市公司公告入口",
                "url": _exchange_stock_url(code),
                "usage": "查交易所公告和上市公司披露",
                "authority": "exchange_entry",
            }
        )
    return links


def _market_prefix(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return "sh"
    if code.startswith(("0", "1", "2", "3")):
        return "sz"
    return ""


def _exchange_fund_url(code: str) -> str:
    if code.startswith(("5", "6")):
        return "https://www.sse.com.cn/assortment/fund/list/"
    if code.startswith(("1", "0", "2", "3")):
        return "https://www.szse.cn/disclosure/fund/notice/index.html"
    return "https://www.cninfo.com.cn/new/fulltextSearch"


def _exchange_stock_url(code: str) -> str:
    if code.startswith(("6", "9")):
        return "https://www.sse.com.cn/disclosure/listedinfo/announcement/"
    if code.startswith(("0", "2", "3")):
        return "https://www.szse.cn/disclosure/listed/bulletin/index.html"
    return "https://www.cninfo.com.cn/new/fulltextSearch"


def _failure_action(status_code: str) -> str:
    if status_code == "trial_candidate":
        return "任一复核失败则保持观察，不执行试错。"
    if status_code == "needs_report":
        return "复核发现风险红灯则不补报告或补完后也不升级。"
    if status_code == "avoid":
        return "继续回避，等待下一轮报告和数据修复。"
    return "保持观察，等待证据增强。"

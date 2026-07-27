"""
quant 事实校验层（TradingAgents 借鉴：事实校验层抵御幻觉）

核心职责：让 LLM 只做「解读」，绝不自算数值。
- 约束注入：enforce_quant_constraints() 在 system prompt 加硬性约束
- 一致性校验：从文本抽取数值声明，与 quant 真相源比较，超阈值告警
"""
from __future__ import annotations

import re
from typing import Dict, Optional

_FACT_CONSTRAINT = (
    "【硬性约束】你不得自行计算任何数值指标（价格、涨跌幅、PE/PB/ROE、"
    "收益率、成交量等）。所有数字必须直接引用提供的 quant 分析结果，"
    "不得臆造、不得改写、不得无依据的四舍五入。"
)

# 需要校验的关键指标（与 quant 输出字段对应；轻量启发式匹配）
_TRACKED_KEYS = ("pe", "pb", "roe", "涨跌幅", "收益率", "收盘", "价格", "close")


def quant_constraint_prompt() -> str:
    """返回可直接拼接的约束文本。"""
    return _FACT_CONSTRAINT


def enforce_quant_constraints(system_prompt: str) -> str:
    """在 system prompt 末尾注入事实约束（已含则幂等）。"""
    if _FACT_CONSTRAINT in system_prompt:
        return system_prompt
    return system_prompt + "\n\n" + _FACT_CONSTRAINT


def _coerce_text(value) -> str:
    """统一为文本：接受 str / list[str] / 其他。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(str(x) for x in value)
    return str(value)


def extract_numeric_claims(text: str) -> Dict[str, float]:
    """从文本抽取带关键指标的数值声明，返回 {指标: 值}。"""
    text = _coerce_text(text)
    claims: Dict[str, float] = {}
    for key in _TRACKED_KEYS:
        m = re.search(rf"{key}\D*?(-?\d+(?:\.\d+)?)\s*%?", text, re.IGNORECASE)
        if m:
            claims[key] = float(m.group(1))
    return claims


def diff_claims(
    claims: Dict[str, float],
    ground_truth: Dict[str, float],
    tolerance: float = 0.05,
) -> list:
    """比较声明与真相源，返回不一致告警列表（空=通过）。"""
    warnings: list = []
    for key, val in claims.items():
        truth = ground_truth.get(key)
        if truth is None:
            continue
        if truth == 0:
            if abs(val) > 1e-9:
                warnings.append(f"{key}: 文本声明 {val}，事实源为 0")
            continue
        rel = abs(val - truth) / abs(truth)
        if rel > tolerance:
            warnings.append(
                f"{key}: 文本声明 {val} 与事实源 {truth} 偏差 {rel:.1%}（> {tolerance:.0%}）"
            )
    return warnings


def check_consistency(
    text: str,
    ground_truth: Dict[str, float],
    tolerance: float = 0.05,
) -> list:
    """一站式：抽取 + 比较。返回告警（空=通过）。"""
    claims = extract_numeric_claims(text)
    if not claims:
        return []
    return diff_claims(claims, ground_truth, tolerance)

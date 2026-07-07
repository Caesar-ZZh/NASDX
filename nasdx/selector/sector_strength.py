"""
sector_strength.py — 行业 / 概念板块强度计算

计算每个板块的 5 日涨幅、20 日涨幅、成交额放大倍数、
板块内强势股比例、涨停数量等指标。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import akshare as ak
import pandas as pd


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def compute_sector_strength() -> List[Dict[str, Any]]:
    """
    计算所有行业 / 概念板块的强度排名。

    Returns:
        板块强度列表，按 strength_score 降序排列。
        每项包含: board_code, board_name, type, strength_score,
                 change_5d, change_20d, volume_ratio, strong_stock_ratio, limit_up_count
    """
    industries = _compute_industry_sectors()
    concepts = _compute_concept_sectors()
    all_sectors = industries + concepts

    # 去重（同名板块取最高的）
    seen = {}
    for s in all_sectors:
        key = s["board_name"]
        if key not in seen or s["strength_score"] > seen[key]["strength_score"]:
            seen[key] = s

    result = list(seen.values())
    result.sort(key=lambda x: x["strength_score"], reverse=True)
    return result


def _compute_industry_sectors() -> List[Dict[str, Any]]:
    """计算行业板块强度。"""
    df = _safe(ak.stock_board_industry_name_em)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    results: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            name = str(row.get("板块名称", ""))
            code = str(row.get("板块代码", ""))
            change_5d = _parse_float(row.get("5日涨跌幅"))
            change_20d = _parse_float(row.get("20日涨跌幅"))
            amount = _parse_float(row.get("成交额"))
            amount_prev = _parse_float(row.get("5日平均成交额"))
            rise_count = _parse_int(row.get("上涨家数"))
            fall_count = _parse_int(row.get("下跌家数"))

            # 成交额放大
            vol_ratio = amount / amount_prev if amount_prev and amount_prev > 0 else 1.0

            # 强势股比例
            total = rise_count + fall_count or 1
            strong_ratio = rise_count / total

            # 综合得分（加权）
            score = 0
            if change_5d is not None:
                score += change_5d * 3.0  # 5日涨幅权重最高
            if change_20d is not None:
                score += change_20d * 1.5  # 20日涨幅权重减半
            score += vol_ratio * 5  # 成交额放大加分
            score += strong_ratio * 20  # 强势股比例加分

            results.append({
                "board_code": code,
                "board_name": name,
                "type": "industry",
                "change_5d": round(change_5d, 2) if change_5d is not None else None,
                "change_20d": round(change_20d, 2) if change_20d is not None else None,
                "amount": round(amount, 0) if amount else None,
                "volume_ratio": round(vol_ratio, 2),
                "strong_stock_ratio": round(strong_ratio, 3),
                "strength_score": round(score, 2),
            })
        except Exception:
            continue

    return results


def _compute_concept_sectors() -> List[Dict[str, Any]]:
    """计算概念板块强度。"""
    df = _safe(ak.stock_board_concept_name_em)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    results: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            name = str(row.get("板块名称", ""))
            code = str(row.get("板块代码", ""))
            change_5d = _parse_float(row.get("5日涨跌幅"))
            change_20d = _parse_float(row.get("20日涨跌幅"))
            amount = _parse_float(row.get("成交额"))
            amount_prev = _parse_float(row.get("5日平均成交额"))
            rise_count = _parse_int(row.get("上涨家数"))
            fall_count = _parse_int(row.get("下跌家数"))

            vol_ratio = amount / amount_prev if amount_prev and amount_prev > 0 else 1.0
            total = rise_count + fall_count or 1
            strong_ratio = rise_count / total

            score = 0
            if change_5d is not None:
                score += change_5d * 3.0
            if change_20d is not None:
                score += change_20d * 1.5
            score += vol_ratio * 5
            score += strong_ratio * 20

            results.append({
                "board_code": code,
                "board_name": name,
                "type": "concept",
                "change_5d": round(change_5d, 2) if change_5d is not None else None,
                "change_20d": round(change_20d, 2) if change_20d is not None else None,
                "amount": round(amount, 0) if amount else None,
                "volume_ratio": round(vol_ratio, 2),
                "strong_stock_ratio": round(strong_ratio, 3),
                "strength_score": round(score, 2),
            })
        except Exception:
            continue

    return results


def get_top_sectors(n: int = 20) -> List[Dict[str, Any]]:
    """获取强度排名前 N 的板块。"""
    all_sectors = compute_sector_strength()
    return all_sectors[:n]


def _parse_float(val) -> float | None:
    if val is None or val == "" or val == "-":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_int(val) -> int:
    if val is None or val == "" or val == "-":
        return 0
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0

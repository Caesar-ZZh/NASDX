"""NASDX 统一 LLM 配置桥（融合阶段3）。

base 的 AI 功能（chat / debate / reflect）原本要求前端在「接入 AI」页填好
baseURL / apiKey / model 随请求传入；本模块提供服务端统一配置作为兜底，
使 AI 功能无需前端逐项填写即可通过 NASDX 统一凭证运行。

配置优先级：请求体（前端自填）> NASDX_* 环境变量（统一凭证）> VR_*（base 原有）> 未配置。
未配置任何凭证时返回 None，由上层保持原有的 400 降级提示，不改变 base 行为。
"""

from __future__ import annotations

import os
from typing import Optional

_UNIFIED_PREFIX = "NASDX"
_LEGACY_PREFIX = "VR"


def default_llm_cfg() -> Optional[dict]:
    """返回 {provider, baseURL, apiKey, model}；任一必填项缺失返回 None。"""
    api_key = (
        os.environ.get(f"{_UNIFIED_PREFIX}_API_KEY", "").strip()
        or os.environ.get(f"{_LEGACY_PREFIX}_API_KEY", "").strip()
    )
    base_url = (
        os.environ.get(f"{_UNIFIED_PREFIX}_BASE_URL", "").strip()
        or os.environ.get(f"{_LEGACY_PREFIX}_BASE_URL", "").strip()
    )
    model = (
        os.environ.get(f"{_UNIFIED_PREFIX}_MODEL", "").strip()
        or os.environ.get(f"{_LEGACY_PREFIX}_MODEL", "").strip()
    )
    if not (api_key and base_url and model):
        return None
    return {"provider": "", "baseURL": base_url, "apiKey": api_key, "model": model}


def merge_llm_cfg(req_cfg: dict, server_cfg: Optional[dict]) -> dict:
    """请求体字段优先，缺口用服务端统一配置补齐。"""
    if not server_cfg:
        return req_cfg
    merged = dict(server_cfg)
    merged.update({k: v for k, v in req_cfg.items() if v not in (None, "")})
    return merged

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


class LlmConfigError(ValueError):
    """请求自带配置与服务端 key 锁定策略冲突（前端应引导填写完整凭证）。"""


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
    """服务端 key 锁定策略：

    - 服务端配置存在（NASDX_*/config.toml，如 Agnes）时：
      * 前端未指定 baseURL（或与服务端一致）→ **apiKey 无条件锁定为服务端值**，
        前端无论传什么都忽略——key 只存服务端，不可见、不可改。
        前端什么都不填（含整份配置为空）也走这条：系统始终有默认 LLM 可用。
      * 前端显式换成其它 provider 并自带 key → 尊重前端（自带 key 场景）。
      * 前端换了 baseURL 但没带 key → 抛 LlmConfigError（上层转 HTTP 400）。
        绝不把服务端 key 发往非服务端 URL——否则任意请求方都能借 baseURL 把
        内置 key 定向送到自己的服务器窃取。
    """
    if not server_cfg:
        return req_cfg
    merged = dict(server_cfg)
    req_clean = {k: v for k, v in req_cfg.items() if v not in (None, "")}
    server_base = (server_cfg.get("baseURL") or "").strip().rstrip("/")
    req_base = (req_clean.get("baseURL") or "").strip().rstrip("/")
    if req_base and req_base != server_base:
        if not req_clean.get("apiKey"):
            raise LlmConfigError(
                "使用自定义 Base URL 时必须同时填写对应的 API Key；"
                "留空则不填 Base URL，直接使用服务端统一模型"
            )
        # 前端换了别的 provider 且自带 key：全套采用前端配置
        merged.update(req_clean)
    else:
        # 未指定或仍是服务端 provider：apiKey 强制服务端，其余字段前端可覆盖
        merged["apiKey"] = server_cfg["apiKey"]
        merged.update({k: v for k, v in req_clean.items() if k != "apiKey"})
    return merged

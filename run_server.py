"""NASDX 后端启动器（带内置 key 注入）。

问题背景：server/stock/llm_cfg.py 的 default_llm_cfg() 只认 NASDX_* 环境变量，
不读 config.toml。这些变量本应由桌面 launcher 从
  C:/Users/11561/AppData/Roaming/NASDX/config.toml 的 [llm] 段注入。
直接 `uvicorn server.main:app` 裸起 -> 无 env -> 内置 Agnes key 为空 ->
前端「接入 AI」留空 API Key 时报「缺少 Base URL 或 API Key」。

本脚本：读 config.toml -> 注入 NASDX_* 环境变量 -> 起 uvicorn，确保内置 key 生效。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))


def main() -> None:
    from desktop.config import load_desktop_config

    cfg = load_desktop_config(APP_ROOT)
    injected: list[str] = []
    for k, v in cfg.values.items():
        # setdefault：若进程已带 env 则不覆盖，否则以 config.toml 补齐（内置 key）。
        os.environ.setdefault(k, v)
        injected.append(k)

    print(f"[startup] config: {cfg.path} (exists={cfg.exists})")
    print(f"[startup] injected NASDX_* env keys: {injected}")

    if not cfg.values.get("NASDX_API_KEY") or not cfg.values.get("NASDX_BASE_URL"):
        print("[startup] ⚠ 未检测到内置 LLM 凭证（config.toml [llm] 缺失/占位），"
              "前端留空 API Key 时将报「缺少 Base URL 或 API Key」")

    port = int(os.environ.get("NASDX_PORT", "8901"))
    host = os.environ.get("NASDX_HOST", "127.0.0.1")

    import uvicorn
    print(f"[startup] starting uvicorn on {host}:{port}")
    uvicorn.run("server.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()

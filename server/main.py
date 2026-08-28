"""NASDX FastAPI 入口（阶段1+2）。

阶段1：把 base 的股票/AI 接口模块挂载为 NASDX 后端层。
阶段2：同一进程同时托管构建后的前端（frontend/dist），实现单命令启动整套产品。

base 模块来自 Vibe-Research（MIT，已更名为 Cosmos），仅做最小包装：加 SPA 静态托管（带路径
穿越防护）、统一由本模块导出 app；CORS 与鉴权由 base_app 统一配置。LLM 统一接入将在阶段3 切换到 nasdx/llm.py。
"""
import os
import sys
from pathlib import Path

# 让 server/stock 内的平级 import（import astock 等）可解析
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stock"))

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

from base_app import app

# CORS 由 base_app 统一配置（VR_ALLOW_ORIGINS 白名单，默认放开）；
# 此处不再叠加第二层通配 CORS——两层互相架空会让白名单收紧失效。

# 前端构建产物目录（NASDX/frontend/dist）
_DIST = (Path(__file__).resolve().parent.parent / "frontend" / "dist").resolve()


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """SPA 静态托管 + 深链兜底。

    /api/* 由 base_app 中更早注册的路由优先匹配，不会落到这里；
    仅未知 /api/* 走下面的 404 守卫。
    """
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="not found")

    if not _DIST.exists():
        return JSONResponse(
            status_code=200,
            content={
                "detail": "NASDX 后端已就绪，但前端尚未构建。",
                "hint": "在 frontend/ 目录执行 `npm install && npm run build`。",
            },
        )

    # 路径归一化防穿越：URL 里的 ../（含 %2e%2e 编码变体）与绝对路径注入，
    # 解析后必须仍落在 dist 内；越界一律 404，绝不回退 index.html 掩盖攻击面。
    try:
        candidate = (_DIST / full_path).resolve()
    except OSError:
        candidate = None
    if candidate is None or not candidate.is_relative_to(_DIST):
        raise HTTPException(status_code=404, detail="not found")
    if candidate.is_file():
        return FileResponse(str(candidate))
    # 非文件请求（如 /stock-data/600519）回退到 index.html，交由 React Router 处理
    return FileResponse(str(_DIST / "index.html"))

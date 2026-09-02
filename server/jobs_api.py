"""后台任务的 HTTP 层：提交 → 轮询 → 取消。

为什么要有这一层
----------------
``/api/analysis/{code}`` 和 ``/api/debate`` 都是「一次 HTTP 请求 = 一次完整分析」，
前端必须撑着连接等到结束，切走页面就把结果丢了。这里把它们包成任务：

    POST   /api/jobs/analysis        → {job_id}        （立刻返回，<1s）
    POST   /api/jobs/debate          → {job_id}
    GET    /api/jobs/{id}?cursor=N   → 快照 + 增量事件
    DELETE /api/jobs/{id}            → 请求取消

老的同步端点保留不动（CLI / iOS 契约 / 既有测试都在用），这一层是叠加而非替换。

鉴权沿用 base_app 的中间件（/api/* 统一走 VR_API_KEY 校验），这里不再重复。
"""
from __future__ import annotations

import os
import sys
import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# 让 server/stock 里的平级 import（import debate / analysis 等）可解析。
# 与 server/main.py 的做法一致；带存在性判断，避免重复插入污染 sys.path。
_STOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock")
if _STOCK_DIR not in sys.path:
    sys.path.insert(0, _STOCK_DIR)

from .jobhub import JobHandle, hub  # noqa: E402

from base_app import LLMConfig, _check_llm, _validate  # noqa: E402

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---- 请求体 -------------------------------------------------------------


class AnalysisJobReq(BaseModel):
    code: str
    risk_profile: str = "balanced"
    depth: str = "full"  # full | intraday | refresh


class DebateJobReq(BaseModel):
    code: str
    rounds: int = 1
    llm: LLMConfig


# ---- runner -------------------------------------------------------------


def _analysis_runner(code: str, risk_profile: str, depth: str):
    """深度分析 runner：拉数据 → 5 Agent → 辩论 → 综合研判。

    LLM 瞬时故障重试 1 次（沿用同步端点的策略）；重试前先问取消，
    免得用户已经中止了还在傻等 2 秒。
    """

    def run(handle: JobHandle):
        from analysis import load_data_for_analysis
        from nasdx.analyzer import NasdxAnalyzer

        handle.progress("正在拉取行情与基本面数据…", step=1, total=4)
        try:
            data = load_data_for_analysis(code)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"深度分析失败：{e}") from e

        last_err: Exception | None = None
        for attempt in (1, 2):
            if handle.cancelled:
                return None
            try:
                handle.progress(
                    f"5 个 Agent 并行研究 + 多空辩论 + 综合研判…（第 {attempt} 次）",
                    step=2,
                    total=4,
                )
                analyzer = NasdxAnalyzer(risk_profile=risk_profile, depth=depth, use_cache=True)
                report = analyzer.analyze(code, data=data)
                handle.progress("分析完成", step=4, total=4)
                return {"report": report.model_dump()}
            except Exception as e:  # noqa: BLE001
                last_err = e
                print(f"[job:analysis] {code} 第 {attempt} 次失败：{e}", flush=True)
                if attempt == 1:
                    # 分段 sleep：取消请求最多等 0.5s 就能生效，不用干等 2 秒
                    for _ in range(4):
                        if handle.cancelled:
                            return None
                        time.sleep(0.5)

        raise RuntimeError(f"深度分析失败（已重试 1 次）：{last_err}。LLM 服务可能瞬时不可用。")

    return run


def _debate_runner(cfg: dict, code: str, rounds: int):
    """多空辩论 runner：直接把 ``run_debate_stream`` 的生成器交给 hub 逐条 publish。

    返回生成器而非跑完——这样前端轮询时能拿到增量 delta，
    「角色一个一个出结论」的观感和原来的流式一致。
    """

    def run(_handle: JobHandle):
        import debate as debate_layer

        return debate_layer.run_debate_stream(cfg, code, rounds)

    return run


# ---- 端点 ---------------------------------------------------------------


@router.post("/analysis")
def start_analysis(req: AnalysisJobReq):
    """提交一次深度分析，立刻返回 job_id（不等待分析完成）。"""
    code = _validate(req.code)
    job = hub.submit(
        kind="analysis",
        runner=_analysis_runner(code, req.risk_profile, req.depth),
        params={"code": code, "risk_profile": req.risk_profile, "depth": req.depth},
        title=f"深度分析 · {code}",
    )
    return {"job_id": job.id, **hub.snapshot(job.id)}


@router.post("/debate")
def start_debate(req: DebateJobReq):
    """提交一场多空辩论，立刻返回 job_id。LLM 配置校验同步做（配置错要马上告诉用户）。"""
    code = _validate(req.code)
    cfg = _check_llm(req.llm)
    rounds = 2 if req.rounds >= 2 else 1
    job = hub.submit(
        kind="debate",
        runner=_debate_runner(cfg, code, rounds),
        params={"code": code, "rounds": rounds},
        title=f"多空辩论 · {code}",
    )
    return {"job_id": job.id, **hub.snapshot(job.id)}


@router.get("/{job_id}")
def get_job(job_id: str, cursor: int = Query(0, ge=0)):
    """取任务快照。``cursor`` 传上次拿到的值就只返回新增事件。

    任务不存在 → 404（前端据此清掉本地残留的 job_id，比如服务重启过）。
    """
    snap = hub.snapshot(job_id, cursor=cursor)
    if snap is None:
        raise HTTPException(404, "任务不存在或已过期（服务可能重启过）")
    return snap


@router.delete("/{job_id}")
def cancel_job(job_id: str):
    """请求取消。已在跑的 LLM 调用不会被强杀，当前阶段结束后即停。"""
    if not hub.cancel(job_id):
        snap = hub.snapshot(job_id)
        if snap is None:
            raise HTTPException(404, "任务不存在或已过期")
        # 已终态的任务谈不上取消，如实回现状而不是报错
        return {"ok": False, "status": snap["status"]}
    return {"ok": True, **hub.snapshot(job_id)}


@router.get("")
def list_jobs():
    """任务中心概况（调试用）：各状态计数 + worker 上限。"""
    return hub.stats()

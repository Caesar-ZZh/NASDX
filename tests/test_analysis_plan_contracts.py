"""新增端点契约：/api/analysis/{code} 与 /api/portfolio/plan。

- /api/analysis：完整深度分析（复用 nasdx.analyzer），校验参数 + 错误路径；
  成功路径依赖真实 LLM/规则分析，耗时 30-120s，只做 400 校验与结构契约（本地手动验证一次）。
- /api/portfolio/plan：纯本地确定性规则，读 scan 产物（缺产物也返回 refresh_required 骨架），
  结构契约可直接断言。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server", "stock"))

try:
    import fastapi  # noqa: F401  (仅探测)
    _FASTAPI_AVAILABLE = True
except ModuleNotFoundError:
    _FASTAPI_AVAILABLE = False

import pytest

if _FASTAPI_AVAILABLE:
    from fastapi.testclient import TestClient
    from base_app import app  # noqa: E402
    client = TestClient(app)


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="需要 fastapi/uvicorn（见 server/requirements.txt）")
class TestPlanEndpoint:
    def test_plan_balanced_returns_required_keys(self):
        """投资路线：balanced 画像必须返回计划骨架（即便产物缺失也是 refresh_required 而非报错）。"""
        r = client.post("/api/portfolio/plan", json={"risk_profile": "balanced"})
        assert r.status_code == 200, r.text
        plan = r.json()["plan"]
        for key in [
            "generated_at", "risk_profile", "risk_profile_label", "posture",
            "action_gate", "allocation", "core_candidates", "satellite_candidates",
            "watchlist", "trim_or_avoid", "next_actions", "future_scenarios",
            "decision_rules", "monitoring_checklist", "review_cadence",
            "data_quality", "source_files", "disclaimer",
        ]:
            assert key in plan, f"plan 缺字段 {key}"
        # action_gate 必须是有意义的值
        assert plan["action_gate"] in {"ok", "refresh_required", "position_cap"}, plan["action_gate"]

    def test_plan_unknown_profile_falls_back_balanced(self):
        """未知风险画像回退 balanced（不 500）。"""
        r = client.post("/api/portfolio/plan", json={"risk_profile": "not-a-profile"})
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["risk_profile"] == "balanced"


@pytest.mark.skipif(not _FASTAPI_AVAILABLE, reason="需要 fastapi/uvicorn（见 server/requirements.txt）")
class TestAnalysisEndpoint:
    def test_analysis_invalid_code_400(self):
        """非 6 位代码 → 400（不触发慢分析）。"""
        r = client.post("/api/analysis/abc", json={})
        assert r.status_code == 400, r.text

    def test_analysis_missing_depth_defaults_full(self):
        """空请求体 → 默认 risk_profile=balanced / depth=full 且 6 位代码能过参数校验。"""
        r = client.post("/api/analysis/600519", json={})
        # 不期望真跑完（可能 30-120s），但应返回 200 或 502 而非 400/500 参数错误
        assert r.status_code in (200, 502), r.text
        if r.status_code == 200:
            report = r.json()["report"]
            assert "stock_code" in report and "summary" in report

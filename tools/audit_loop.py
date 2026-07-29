# -*- coding: utf-8 -*-
"""
NASDX 自主功能审查循环 harness

流程（可续跑，状态存 .audit_state.json）：
  1. execute : 遍历 manifest 中的功能模块，subprocess 隔离执行探针，采集 预期 vs 实际。
  2. analyze : 用 LLM（复用 nasdx.llm）做产品层分析，产出结构化 findings。
  3. report  : 生成详细使用报告（功能描述/步骤/预期实际对比 + 产品分析）。
  4. issues  : 为每个 finding 建 GitHub Issue（gh）。
  5. fix     : 对每个开放 issue 生成补丁→测→PR（有界批次）。仅记录 PR 已开，不关闭 Issue。
  6. verify  : 核实 PR 已合并且合并提交可达默认分支后，才标记 fixed 并关闭 Issue；
               未合并即关闭的 PR 会清除状态使 finding 可重试，误关的 Issue 会被重开。

用法：
  python tools/audit_loop.py                 # 全循环（execute→analyze→report→issues→fix）
  python tools/audit_loop.py --phase execute
  python tools/audit_loop.py --phase analyze
  python tools/audit_loop.py --phase report
  python tools/audit_loop.py --phase issues
  python tools/audit_loop.py --phase fix --max-fix 3
  python tools/audit_loop.py --phase verify   # 核实 PR 合并状态并推进 Issue 生命周期
  python tools/audit_loop.py --no-net        # 跳过网络依赖探针
  python tools/audit_loop.py --limit-modules 8
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".audit_state.json"
REPORT_DIR = ROOT / "deliverables" / "gstack"
REPO = "Caesar-ZZh/NASDX"
DATE = time.strftime("%Y-%m-%d")

PY = sys.executable
DEFAULT_TIMEOUT = 180
NET_TIMEOUT = 240


# --------------------------------------------------------------------------
# 状态
# --------------------------------------------------------------------------
def _default_state() -> Dict[str, Any]:
    return {
        "executed": {},          # module_id -> result dict
        "findings": [],          # 分析结果
        "issues": {},            # finding_id -> issue_number
        "prs": {},               # finding_id -> pr_number（PR 已开，尚未验证合并到默认分支）
        "fixed": {},             # finding_id -> pr_number（已验证：PR 合并且提交在默认分支上）
        "skipped_modules": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _migrate_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """旧 schema 迁移（#61）。

    旧版把「PR 已创建」直接写进 ``fixed``，导致未合并的 PR 也被视为已完成、
    finding 永远不可重试。新 schema 下 ``fixed`` 仅表示「已验证合并进默认分支」。
    因此把缺少 ``prs`` 键的旧状态中的 ``fixed`` 条目整体降级到 ``prs``，
    交由 phase_verify 重新核实。
    """
    if "prs" not in state:
        state["prs"] = dict(state.get("fixed") or {})
        state["fixed"] = {}
    state.setdefault("fixed", {})
    return state


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return _migrate_state(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return _default_state()


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# 功能模块清单（manifest）
# 每个 probe:
#   kind=snippet -> 在 subprocess 中执行 code（ROOT 已在 sys.path），print RESULT_OK/RESULT_ERR
#   kind=shell   -> 执行 cmd
#   kind=audit   -> 复用 run_final_audit 的内置检查（shell 调用）
# needs_net=True 的探针在 --no-net 时跳过
# --------------------------------------------------------------------------
def _snip(code: str, needs_net: bool = False) -> Dict[str, Any]:
    return {"kind": "snippet", "code": code.replace("__ROOT__", repr(str(ROOT))), "needs_net": needs_net}


MANIFEST: List[Dict[str, Any]] = [
    # ---------------- quant 层 ----------------
    {
        "id": "quant-data", "title": "行情数据层 quant/data", "layer": "quant",
        "description": "统一 OHLCV 数据获取（mootdx/AkShare/tdxrs 适配），含并发批量与缓存。",
        "expected": "导入成功；get_ohlcv 对已知代码返回含 open/high/low/close/volume 的 DataFrame。",
        "test": "tests/test_quant_data_batch_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from quant.data import get_ohlcv, get_batch_ohlcv\n"
            "try:\n"
            "    df = get_ohlcv('600519', days=20)\n"
            "    if df is None or len(df) == 0:\n"
            "        print('RESULT_WARN empty df (可能为离线/无数据)'); sys.exit(0)\n"
            "    cols = [c for c in ['open','high','low','close','volume'] if c in df.columns]\n"
            "    print('RESULT_OK rows=%d cols=%s' % (len(df), cols))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n",
            needs_net=True,
        ),
    },
    {
        "id": "quant-factors", "title": "因子计算 quant/factors", "layer": "quant",
        "description": "技术指标与因子计算（均线/RSI/MACD/布林等）。",
        "expected": "对合成 OHLCV 计算因子，返回含因子列的非空 DataFrame，无 NaN 崩溃。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import numpy as np, pandas as pd\n"
            "from quant.factors import compute_alpha158, multi_factor_score\n"
            "try:\n"
            "    n=120; idx=pd.date_range('2024-01-01', periods=n, freq='h')\n"
            "    df=pd.DataFrame({'close':100+np.cumsum(np.random.default_rng(1).normal(0,1,n)),'open':100,'high':101,'low':99,'volume':1000}, index=idx)\n"
            "    fac=compute_alpha158(df)\n"
            "    fac_cols=[c for c in fac.columns if c not in df.columns]\n"
            "    scored=multi_factor_score({'600519': fac})\n"
            "    print('RESULT_OK factor_cols=%d scored_rows=%d' % (len(fac_cols), len(scored)))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "quant-backtest", "title": "回测引擎 quant/backtest", "layer": "quant",
        "description": "调仓/清仓/收益基准/权重校验/闭环 PnL 的回测引擎。",
        "expected": "用合成价格+权重跑回测，返回权益曲线与非空 metrics，无异常。",
        "test": "tests/test_backtest_correctness_p1.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import pandas as pd, numpy as np\n"
            "from quant.backtest import Backtester\n"
            "try:\n"
            "    dates=pd.date_range('2024-01-01', periods=60, freq='D')\n"
            "    close=100+np.cumsum(np.random.default_rng(2).normal(0,1,60))\n"
            "    price_data={'600519': pd.DataFrame({'close':close,'open':close,'high':close+1,'low':close-1,'volume':1000}, index=dates)}\n"
            "    bt=Backtester(initial_capital=100000)\n"
            "    def signal(date, past):\n"
            "        return {'600519':1.0}\n"
            "    res=bt.run(price_data, signal, rebalance_freq='W')\n"
            "    print('RESULT_OK equity_len=%d total_return=%.4f' % (len(res.equity_curve), res.total_return))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "quant-portfolio", "title": "组合权重 quant/portfolio", "layer": "quant",
        "description": "组合构建与权重分配。",
        "expected": "导入成功，组合权重之和合理（<=1 或已归一）。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import pandas as pd, numpy as np\n"
            "from quant.portfolio import build_portfolio\n"
            "try:\n"
            "    fs=pd.DataFrame({'code':['600519','000001'],'factor_score':[0.8,0.5]})\n"
            "    rets=pd.DataFrame(np.random.default_rng(3).normal(0,0.01,(30,2)), columns=['600519','000001'])\n"
            "    w=build_portfolio(fs, rets, method='factor', top_n=2)\n"
            "    print('RESULT_OK weights_sum=%.3f n=%d' % (w.sum(), len(w)))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "quant-signal", "title": "信号引擎 quant/signal_engine", "layer": "quant",
        "description": "多因子信号合成与打分。",
        "expected": "对合成因子产出信号（buy/sell/hold）与分数。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import pandas as pd, numpy as np\n"
            "from quant.signal_engine import SignalEngine\n"
            "try:\n"
            "    rng=np.random.default_rng(5); n=60; idx=pd.date_range('2024-01-01', periods=n, freq='D')\n"
            "    c={'open':100+rng.normal(0,1,n),'close':100+rng.normal(0,1,n),'high':101+rng.normal(0,1,n),'low':99+rng.normal(0,1,n),'volume':1e6+rng.normal(0,1e5,n),'amount':1e8+rng.normal(0,1e7,n)}\n"
            "    price={'600519': pd.DataFrame(c, index=idx)}\n"
            "    eng=SignalEngine(use_calibrated=False)\n"
            "    sig=eng.run(['600519'], price, verbose=False)\n"
            "    print('RESULT_OK rows=%d cols=%d' % (len(sig), len(sig.columns)))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "quant-etf50", "title": "ETF50 量化 quant/etf50_quant", "layer": "quant",
        "description": "ETF50 规则量化扫描。",
        "expected": "导入成功并可对样本产出评分/候选。",
        "test": "tests/test_etf50_quant_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import quant.etf50_quant as m\n"
            "fn=getattr(m, 'scan_etf50', None) or getattr(m, 'run_etf50_quant', None) or getattr(m, 'etf50_quant_scan', None)\n"
            "try:\n"
            "    if fn is None:\n"
            "        print('RESULT_WARN etf50_quant 无预期导出函数，可用:', [x for x in dir(m) if not x.startswith('_')][:8])\n"
            "    else:\n"
            "        r=fn()\n"
            "        print('RESULT_OK type=%s' % type(r).__name__)\n"
            "except Exception as e:\n"
            "    print('RESULT_WARN etf50 需数据/接口差异:', repr(e)[:200])\n",
            needs_net=True,
        ),
    },
    # ---------------- nasdx 层 ----------------
    {
        "id": "nasdx-history-store", "title": "历史库 nasdx/history_store", "layer": "nasdx",
        "description": "SQLite 历史产物库（简报/报告/扫描/ETF池）。",
        "expected": "init+record+latest 全链路可用，artifact_counts 包含四类。",
        "test": "tests/test_history_store_contracts.py",
        "probe": _snip(
            "import sys, tempfile; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.history_store import init_history_db, record_artifact, latest_artifact, artifact_counts\n"
            "from pathlib import Path\n"
            "try:\n"
            "    d=Path(tempfile.mkdtemp())/'h.db'; init_history_db(d)\n"
            "    record_artifact('investment_brief','latest',{'action_gate':'normal'}, source_path='x.json', db_path=d)\n"
            "    latest=latest_artifact('investment_brief','latest', db_path=d)\n"
            "    print('RESULT_OK latest_gate=%s counts=%s' % (latest['payload'].get('action_gate'), artifact_counts(d)))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-rule-based", "title": "规则深度报告 nasdx/rule_based_analysis", "layer": "nasdx",
        "description": "无 LLM 的规则型多维度深度报告（技术/资金/风险/行业/瓶颈/综合）。",
        "expected": "build_rule_based_report 返回含 6 维度、入场/退出条件、analysis_mode=rules 的报告。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.rule_based_analysis import build_rule_based_report\n"
            "try:\n"
            "    r=build_rule_based_report('603501', risk_profile='balanced')\n"
            "    dims=set(r.research_results); need={'technical','fund_flow','risk','sector','chokepoint','synthesis'}\n"
            "    print('RESULT_OK signal=%s missing=%s' % (r.final_signal, need-dims))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-portfolio", "title": "组合路线 nasdx/portfolio", "layer": "nasdx",
        "description": "三档风险画像的组合级投资路线（配置/候选/情景/规则/监控/闸门）。",
        "expected": "三档均生成完整字段，含未来情景>=3、执行规则>=5、免责声明。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.portfolio import build_portfolio_plan\n"
            "try:\n"
            "    out={p: build_portfolio_plan(risk_profile=p) for p in ('conservative','balanced','aggressive')}\n"
            "    gates=[out[p]['action_gate'] for p in out]\n"
            "    print('RESULT_OK gates=%s' % gates)\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-position-sizing", "title": "资金仓位换算 nasdx/position_sizing", "layer": "nasdx",
        "description": "把投资简报候选换算为金额仓位，带隐私边界。",
        "expected": "parse_percent_band + build_position_sizing 返回金额上限与非空候选。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.investment_brief import build_investment_brief\n"
            "from nasdx.position_sizing import build_position_sizing, parse_percent_band\n"
            "try:\n"
            "    assert parse_percent_band('35%-60%')==(0.35,0.60)\n"
            "    b=build_investment_brief(risk_profile='balanced')\n"
            "    s=build_position_sizing(b, total_capital=100000, current_etf_exposure=10000, current_stock_exposure=5000, current_other_exposure=0)\n"
            "    print('RESULT_OK candidates=%d max_amt=%.0f' % (len(s['candidate_sizing']), s['exposure']['max_total_amount']))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-reco-tracker", "title": "建议漂移追踪 nasdx/recommendation_tracker", "layer": "nasdx",
        "description": "对比前后候选，追踪新增/移除/变化。",
        "expected": "build_recommendation_tracker 返回 schema v1、当前候选>=3、含复盘重点。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.recommendation_tracker import build_recommendation_tracker\n"
            "try:\n"
            "    t=build_recommendation_tracker()\n"
            "    print('RESULT_OK schema=%s current=%d' % (t['schema'], t['counts']['current_candidates']))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-reco-review", "title": "建议结果复盘 nasdx/recommendation_review", "layer": "nasdx",
        "description": "复盘历史建议信号是否延续/降级。",
        "expected": "build_recommendation_review 返回 schema v1、候选>=3、计数一致。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.recommendation_review import build_recommendation_review\n"
            "try:\n"
            "    r=build_recommendation_review()\n"
            "    print('RESULT_OK schema=%s rows=%d' % (r['schema'], len(r['review_rows'])))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-account-review", "title": "真实账户复盘 nasdx/account_review", "layer": "nasdx",
        "description": "导入成交流水做真实账户复盘，无流水时返回 missing_ledger。",
        "expected": "build_account_review(None) -> missing_ledger；含 CSV 模板。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.account_review import build_account_review, template_csv\n"
            "try:\n"
            "    m=build_account_review(None)\n"
            "    ok = m.get('review_status')=='missing_ledger' and 'date,code' in template_csv()\n"
            "    print('RESULT_OK missing_ledger_ok=%s' % ok)\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-review-snapshot", "title": "复盘快照包 nasdx/review_snapshot", "layer": "nasdx",
        "description": "打包简报/路线/复盘为 ZIP + manifest，含外部复核边界。",
        "expected": "build_review_snapshot 生成 ZIP 与 manifest v2，候选>=3。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys, tempfile; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.review_snapshot import build_review_snapshot\n"
            "from pathlib import Path\n"
            "try:\n"
            "    out=build_review_snapshot(risk_profile='balanced', output_dir=Path(tempfile.mkdtemp()), refresh=False)\n"
            "    print('RESULT_OK zip=%s candidates=%s' % (Path(out['zip_path']).exists(), out['manifest'].get('candidate_count')))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-investment-brief", "title": "最终投资简报 nasdx/investment_brief", "layer": "nasdx",
        "description": "汇总候选剧本/证据核查/执行队列/外部复核包/情景。",
        "expected": "build_investment_brief 返回完整字段，含盘前/盘中/盘后执行队列、外部复核包。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.investment_brief import build_investment_brief\n"
            "try:\n"
            "    b=build_investment_brief(risk_profile='balanced')\n"
            "    stages={i.get('stage') for i in b.get('execution_queue',[])}\n"
            "    print('RESULT_OK routes=%d audits=%d stages=%s' % (len(b['candidate_playbook']), len(b['candidate_audits']), stages))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-fast-market", "title": "并发行情缓存 nasdx/fast_market", "layer": "nasdx",
        "description": "ThreadPoolExecutor 并发拉取历史 + 磁盘缓存 + TTL + min_rows/sources 校验。",
        "expected": "fetch_histories 返回 DataFrame，缓存命中复用；min_rows 不足时回源。",
        "test": "tests/test_data_correctness_p1.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from datetime import datetime, timedelta\n"
            "from nasdx.fast_market import fetch_histories\n"
            "try:\n"
            "    end=datetime.now().strftime('%Y%m%d'); start=(datetime.now()-timedelta(days=20)).strftime('%Y%m%d')\n"
            "    res=fetch_histories(['600519'], start, end, min_rows=5, sources=('tdxrs',))\n"
            "    print('RESULT_OK type=%s' % type(res).__name__)\n"
            "except Exception as e:\n"
            "    print('RESULT_WARN fast_market 需网络/缓存:', repr(e)[:200])\n",
            needs_net=True,
        ),
    },
    {
        "id": "nasdx-data-quality", "title": "数据质量 nasdx/data_quality", "layer": "nasdx",
        "description": "评估扫描/简报数据的新鲜度与覆盖率。",
        "expected": "能产出 data_quality 状态（含 coverage/status）。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "from nasdx.data_quality import assess_data_quality\n"
            "try:\n"
            "    q=assess_data_quality({'date':'2026-07-18','generated_at':'2026-07-18 15:00:00'})\n"
            "    print('RESULT_OK status=%s' % q['status'])\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "nasdx-ui-security", "title": "UI 安全 nasdx/ui_security", "layer": "nasdx",
        "description": "Streamlit 侧安全校验（外部链接/输入转义/权限边界）。",
        "expected": "导入成功，提供安全校验 helper。",
        "test": "tests/test_ui_security_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import nasdx.ui_security as us\n"
            "try:\n"
            "    fns=[x for x in dir(us) if not x.startswith('_')]\n"
            "    print('RESULT_OK helpers=%d' % len(fns))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    # ---------------- desktop 层 ----------------
    {
        "id": "desktop-doctor", "title": "桌面诊断 desktop/doctor", "layer": "desktop",
        "description": "运行环境诊断（依赖/端口/Inno/日志）。",
        "expected": "导入成功，提供 run_doctor / CORE_MODULES。",
        "test": "tests/test_desktop_doctor_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import desktop.doctor as d\n"
            "try:\n"
            "    ok = hasattr(d,'run_doctor') and hasattr(d,'CORE_MODULES')\n"
            "    print('RESULT_OK doctor_ok=%s' % ok)\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "desktop-launcher", "title": "桌面启动器 desktop/launcher", "layer": "desktop",
        "description": "Streamlit 桌面启动封装。",
        "expected": "导入成功，提供 start_streamlit。",
        "test": "tests/test_desktop_launcher_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import desktop.launcher as l\n"
            "try:\n"
            "    print('RESULT_OK has_start=%s' % hasattr(l,'start_streamlit'))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "desktop-control", "title": "桌面控制面板 desktop/control", "layer": "desktop",
        "description": "Start/Stop/Open App/Settings/Logs/Data Refresh 控制动作。",
        "expected": "导入成功，提供 CONTROL_ACTIONS 且含核心动作。",
        "test": "tests/test_desktop_control_contracts.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import desktop.control as c\n"
            "try:\n"
            "    acts=getattr(c,'CONTROL_ACTIONS',())\n"
            "    names=list(acts)\n"
            "    print('RESULT_OK actions=%s' % names[:6])\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    # ---------------- 顶层流程 ----------------
    {
        "id": "run-final-audit", "title": "交付前总审计 run_final_audit", "layer": "top",
        "description": "24 项交付契约检查（语法/密钥/桌面资产/市场数据/并发/结构化/路线等）。",
        "expected": "退出码 0（全部通过）。",
        "test": "tests/test_architecture_contracts.py",
        "probe": {"kind": "shell", "cmd": [PY, "run_final_audit.py"], "needs_net": False},
    },
    {
        "id": "app-import", "title": "Streamlit 主界面 app.py 导入冒烟", "layer": "top",
        "description": "app.py 作为 Streamlit 入口的导入冒烟。",
        "expected": "能被 import（语法/依赖完整），不触发全局 requests monkey patch。",
        "test": "tests/test_app_import_smoke.py",
        "probe": _snip(
            "import sys; sys.path.insert(0, __ROOT__)\n"
            "import runpy\n"
            "try:\n"
            "    # 仅编译+导入，不启动 server\n"
            "    mod=runpy.run_path(__ROOT__ + '/app.py', run_name='__not_main__')\n"
            "    print('RESULT_OK imported keys=%d' % len(mod))\n"
            "except Exception as e:\n"
            "    print('RESULT_ERR', repr(e)[:300])\n"
        ),
    },
    {
        "id": "scan-stocks-help", "title": "个股全扫描 scan_stocks_full --help", "layer": "top",
        "description": "CLI 入口可用性（参数解析）。",
        "expected": "--help 正常输出，含覆盖率字段说明。",
        "test": "tests/test_quant_core_contracts.py",
        "probe": {"kind": "shell", "cmd": [PY, "scan_stocks_full.py", "--help"], "needs_net": False, "timeout": 90},
    },
    {
        "id": "fetch-stock-help", "title": "行情刷新 fetch_stock_data --help", "layer": "top",
        "description": "CLI 入口可用性。",
        "expected": "--help 正常输出。",
        "test": "tests/test_data_retry_contracts.py",
        "probe": {"kind": "shell", "cmd": [PY, "fetch_stock_data.py", "--help"], "needs_net": False, "timeout": 90},
    },
]


# --------------------------------------------------------------------------
# 执行器
# --------------------------------------------------------------------------
def run_snippet(code: str, timeout: int) -> Dict[str, Any]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        path = f.name
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["NASDX_CONFIG_FILE"] = ""  # 避免读取不存在的 config
    try:
        proc = subprocess.run(
            [PY, "-P", path], cwd=str(ROOT), env=env, text=True,
            encoding="utf-8", errors="replace", capture_output=True, timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return _classify(proc.returncode, out)
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "detail": f"超时({timeout}s)", "output": ""}
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "detail": repr(e), "output": ""}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def run_shell(cmd: List[str], timeout: int) -> Dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), env=env, text=True,
            encoding="utf-8", errors="replace", capture_output=True, timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return _classify(proc.returncode, out[-2000:])
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "detail": f"超时({timeout}s)", "output": ""}
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "detail": repr(e), "output": ""}


def _classify(rc: int, out: str) -> Dict[str, Any]:
    if "RESULT_OK" in out:
        status = "PASS"
    elif "RESULT_ERR" in out:
        status = "FAIL"
    elif "RESULT_WARN" in out:
        status = "WARN"
    elif rc != 0:
        status = "FAIL"
    else:
        status = "PASS"
    # 提取 RESULT 行作为实际结果摘要
    actual = ""
    for line in out.splitlines():
        if line.startswith("RESULT_"):
            actual = line
            break
    if not actual:
        actual = out.strip().splitlines()[-1] if out.strip() else "(无输出)"
    return {"status": status, "detail": actual[:400], "output": out[-1500:]}


def execute_module(mod: Dict[str, Any], no_net: bool) -> Dict[str, Any]:
    probe = mod["probe"]
    if probe.get("needs_net") and no_net:
        return {"status": "SKIP", "detail": "离线模式跳过网络探针", "output": ""}
    timeout = probe.get("timeout") or (NET_TIMEOUT if probe.get("needs_net") else DEFAULT_TIMEOUT)
    if probe["kind"] == "snippet":
        res = run_snippet(probe["code"], timeout)
    elif probe["kind"] == "shell":
        res = run_shell(probe["cmd"], timeout)
    else:
        res = {"status": "ERROR", "detail": "未知探针类型", "output": ""}
    res["expected"] = mod["expected"]
    res["title"] = mod["title"]
    res["layer"] = mod["layer"]
    return res


# --------------------------------------------------------------------------
# LLM 分析（复用 nasdx.llm）
# --------------------------------------------------------------------------
def llm_analyze(results: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    try:
        sys.path.insert(0, str(ROOT))
        from nasdx.llm import llm  # noqa: F401
    except Exception:  # noqa: BLE001
        return None
    summary = "\n".join(
        f"- [{r['status']}] {r.get('title','?')} (layer={r.get('layer','?')}): {r.get('detail','')}"
        for r in results
    )
    system = (
        "你是 NASDX（A股量化研究系统）的产品与工程质量分析师。基于功能模块的执行结果，"
        "识别产品层不足：功能缺陷、用户体验问题、边界情况处理缺失、健壮性/可观测性缺口。"
        "只输出 JSON，结构：{\"findings\":[{\"module\":模块id,\"severity\":\"P0|P1|P2|P3\","
        "\"category\":\"defect|ux|edge|robustness|docs\",\"title\":简短标题,"
        "\"description\":问题描述,\"expected\":预期行为,\"actual\":实际表现,"
        "\"repro\":复现步骤,\"suggestion\":优化建议,\"file\":最相关文件(可空)}]}。"
        "仅针对真实异常/风险产出发现；无问题则不输出该模块。最多 12 条。"
    )
    prompt = f"以下功能模块的执行结果（status: PASS/WARN/FAIL/ERROR）：\n{summary}\n\n请产出结构化 findings。"
    try:
        data = llm.ask_json([{"role": "user", "content": prompt}], system=system)
        findings = data.get("findings") if isinstance(data, dict) else None
        if isinstance(findings, list):
            return findings
    except Exception:  # noqa: BLE001
        return None
    return None


def heuristic_analyze(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """无 LLM 时的回退：从 FAIL/ERROR/WARN 推导发现。"""
    findings: List[Dict[str, Any]] = []
    for r in results:
        if r["status"] in ("FAIL", "ERROR"):
            sev = "P1" if r["status"] == "ERROR" else "P2"
            findings.append({
                "module": r.get("title", "?"), "severity": sev, "category": "defect",
                "title": f"{r.get('title','模块')} 执行异常",
                "description": r.get("detail", ""),
                "expected": r.get("expected", ""),
                "actual": r.get("detail", ""),
                "repro": f"运行模块 {r.get('title','?')} 探针",
                "suggestion": "定位异常根因并加固（异常捕获/边界/依赖）；补充针对性测试。",
                "file": "",
            })
        elif r["status"] == "WARN":
            findings.append({
                "module": r.get("title", "?"), "severity": "P3", "category": "robustness",
                "title": f"{r.get('title','模块')} 离线/降级路径",
                "description": r.get("detail", ""),
                "expected": r.get("expected", ""),
                "actual": r.get("detail", ""),
                "repro": "无网络/无数据环境运行",
                "suggestion": "明确离线降级行为并加测试，避免干净环境静默失败。",
                "file": "",
            })
    return findings


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def write_report(state: Dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"audit-report-NASDX-{DATE}.md"
    results = list(state["executed"].values())
    findings = state["findings"]
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "ERROR": 0, "SKIP": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    lines = []
    lines.append(f"# NASDX 功能审查使用报告\n")
    lines.append(f"**日期**：{DATE}  **范围**：NASDX 当前项目  **模式**：含真实网络取数\n")
    lines.append("\n## 📌 执行概览\n")
    lines.append(f"- 遍历模块：**{len(results)}** 个")
    lines.append(f"- 状态分布：🟢 PASS {counts['PASS']}  🟡 WARN {counts['WARN']}  🔴 FAIL {counts['FAIL']}  ⚫ ERROR {counts['ERROR']}  ⚪ SKIP {counts['SKIP']}")
    lines.append(f"- 产品层发现：**{len(findings)}** 条（见下方分析）\n")

    lines.append("## 1. 功能遍历与执行结果\n")
    lines.append("| 模块 | 层 | 状态 | 预期 | 实际结果 |")
    lines.append("|------|----|------|------|----------|")
    for r in results:
        icon = {"PASS": "🟢", "WARN": "🟡", "FAIL": "🔴", "ERROR": "⚫", "SKIP": "⚪"}.get(r["status"], "⚪")
        exp = (r.get("expected") or "")[:60]
        act = (r.get("detail") or "")[:80].replace("|", "\\|")
        lines.append(f"| {r.get('title','?')} | {r.get('layer','?')} | {icon} {r['status']} | {exp} | {act} |")

    lines.append("\n## 2. 产品层分析（缺陷 / UX / 边界缺失）\n")
    if not findings:
        lines.append("本次遍历未发现明确的产品层缺陷（异常/降级已记录于上表）。\n")
    else:
        by_sev = {"P0": [], "P1": [], "P2": [], "P3": []}
        for f in findings:
            by_sev.setdefault(f.get("severity", "P3"), []).append(f)
        for sev in ("P0", "P1", "P2", "P3"):
            items = by_sev[sev]
            if not items:
                continue
            lines.append(f"\n### {sev}（{len(items)} 条）\n")
            for i, f in enumerate(items, 1):
                lines.append(f"**{i}. {f.get('title','')}**  `[{f.get('category','')}]` — {f.get('module','')}")
                lines.append(f"- 描述：{f.get('description','')}")
                lines.append(f"- 预期：{f.get('expected','')}")
                lines.append(f"- 实际：{f.get('actual','')}")
                lines.append(f"- 复现：{f.get('repro','')}")
                lines.append(f"- 建议：{f.get('suggestion','')}")
                lines.append(f"- 相关文件：`{f.get('file','') or '待定位'}`")
                lines.append("")

    lines.append("\n## 3. 行动清单（Issue → 修复闭环）\n")
    lines.append("| 发现 | 严重度 | Issue | PR | 状态 |")
    lines.append("|------|--------|-------|----|------|")
    for i, f in enumerate(findings):
        fid = f.get("id", f"F{i}")
        issue = state["issues"].get(fid, "")
        pr_fixed = state["fixed"].get(fid, "")
        pr_open = state.get("prs", {}).get(fid, "")
        pr = pr_fixed or pr_open
        if pr_fixed:
            st = "已合并验证并关闭"
        elif pr_open:
            st = "PR 待合并（Issue 开放）"
        elif issue:
            st = "已建"
        else:
            st = "待建"
        lines.append(f"| {f.get('title','')} | {f.get('severity','')} | #{issue} | {('#'+str(pr)) if pr else ''} | {st} |")

    lines.append("\n---\n> 本报告由 NASDX 自主审查 harness（tools/audit_loop.py）生成；Issue/PR 闭环由同脚本在授权下自动推进。\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# GitHub Issue / PR
# --------------------------------------------------------------------------
def gh_issue_create(title: str, body: str) -> Optional[int]:
    proc = subprocess.run(
        ["gh", "issue", "create", "--repo", REPO, "--title", title, "--body", body],
        cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    if proc.returncode != 0:
        return None
    # 解析 #编号
    import re
    m = re.search(r"issues/(\d+)", proc.stdout)
    return int(m.group(1)) if m else None


def gh_pr_create(title: str, body: str, branch: str) -> Optional[int]:
    proc = subprocess.run(
        ["gh", "pr", "create", "--repo", REPO, "--base", "master", "--head", branch,
         "--title", title, "--body", body],
        cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    if proc.returncode != 0:
        return None
    import re
    m = re.search(r"pull/(\d+)", proc.stdout)
    return int(m.group(1)) if m else None


def gh_issue_comment(number: int, body: str) -> None:
    subprocess.run(
        ["gh", "issue", "comment", str(number), "--repo", REPO, "--body", body],
        cwd=str(ROOT), text=True, capture_output=True,
    )


def gh_issue_close(number: int) -> None:
    subprocess.run(
        ["gh", "issue", "close", str(number), "--repo", REPO],
        cwd=str(ROOT), text=True, capture_output=True,
    )


def gh_issue_reopen(number: int) -> None:
    subprocess.run(
        ["gh", "issue", "reopen", str(number), "--repo", REPO],
        cwd=str(ROOT), text=True, capture_output=True,
    )


def gh_issue_state(number: int) -> Optional[str]:
    """返回 issue 状态（'OPEN'/'CLOSED'），查询失败返回 None。"""
    proc = subprocess.run(
        ["gh", "issue", "view", str(number), "--repo", REPO, "--json", "state"],
        cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout).get("state")
    except (json.JSONDecodeError, AttributeError):
        return None


def gh_pr_view(number: int) -> Optional[Dict[str, Any]]:
    """查询 PR 状态。返回 {'state','mergedAt','mergeCommitSha'}；查询失败返回 None。"""
    proc = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", REPO,
         "--json", "state,mergedAt,mergeCommit"],
        cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    merge_commit = data.get("mergeCommit") or {}
    return {
        "state": data.get("state"),                       # OPEN / MERGED / CLOSED
        "mergedAt": data.get("mergedAt"),
        "mergeCommitSha": merge_commit.get("oid") if isinstance(merge_commit, dict) else None,
    }


def _git(args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args, cwd=str(ROOT), text=True,
        encoding="utf-8", errors="replace", capture_output=True, timeout=timeout,
    )


def commit_reachable_on_default(sha: str, default_branch: str = "master") -> bool:
    """校验提交是否可达于最新默认分支（fetch 失败或不可达都返回 False）。"""
    if not sha:
        return False
    fetch = _git(["fetch", "origin", default_branch])
    if fetch.returncode != 0:
        return False
    anc = _git(["merge-base", "--is-ancestor", sha, "FETCH_HEAD"])
    return anc.returncode == 0


# --------------------------------------------------------------------------
# 各阶段
# --------------------------------------------------------------------------
def phase_execute(state: Dict[str, Any], no_net: bool, limit: Optional[int]) -> None:
    pending = [m for m in MANIFEST if m["id"] not in state["executed"] and m["id"] not in state["skipped_modules"]]
    if limit:
        pending = pending[:limit]
    print(f"[execute] 待执行 {len(pending)} 个模块")
    for m in pending:
        print(f"  -> {m['title']} ...", end=" ", flush=True)
        res = execute_module(m, no_net)
        state["executed"][m["id"]] = res
        save_state(state)
        print(res["status"])


def phase_analyze(state: Dict[str, Any]) -> None:
    results = list(state["executed"].values())
    print("[analyze] LLM 产品分析 ...", flush=True)
    findings = llm_analyze(results)
    if findings is None:
        print("  LLM 不可用，回退启发式分析")
        findings = heuristic_analyze(results)
    for i, f in enumerate(findings):
        f.setdefault("id", f"F{i}")
    state["findings"] = findings
    save_state(state)
    print(f"[analyze] 发现 {len(findings)} 条")


def phase_report(state: Dict[str, Any]) -> Path:
    path = write_report(state)
    print(f"[report] 已生成 {path}")
    return path


def phase_issues(state: Dict[str, Any]) -> None:
    for f in state["findings"]:
        fid = f["id"]
        if fid in state["issues"]:
            continue
        title = f"[audit] {f.get('severity','P2')} {f.get('title','')}"
        body = textwrap.dedent(f"""\
        ## 产品层发现（自主审查 harness 产出）

        **模块**：{f.get('module','')}
        **类别**：{f.get('category','')}  **严重度**：{f.get('severity','')}

        ### 描述
        {f.get('description','')}

        ### 复现步骤
        {f.get('repro','')}

        ### 预期行为
        {f.get('expected','')}

        ### 实际表现
        {f.get('actual','')}

        ### 优化建议
        {f.get('suggestion','')}

        ### 相关文件
        `{f.get('file','') or '待定位'}`

        ---
        由 NASDX 自主审查 harness（tools/audit_loop.py）自动创建。
        """)
        num = gh_issue_create(title, body)
        if num:
            state["issues"][fid] = num
            save_state(state)
            print(f"  issue #{num}: {title}")
        else:
            print(f"  [WARN] issue 创建失败: {title}")


def phase_fix(state: Dict[str, Any], max_fix: int) -> None:
    """有界修复闭环（#61 语义）。

    本阶段只推进到「PR 已创建」（记入 ``state['prs']``），**不会**关闭 Issue、
    也不会把 finding 标成 ``fixed``。Issue 的关闭与 ``fixed`` 判定统一由
    :func:`phase_verify` 在确认 PR 已合并且合并提交可达默认分支后执行。
    """
    done = 0
    for f in state["findings"]:
        if done >= max_fix:
            break
        fid = f["id"]
        issue = state["issues"].get(fid)
        if not issue or fid in state["fixed"] or fid in state["prs"]:
            continue
        file_hint = f.get("file", "")
        if not file_hint:
            print(f"  [skip] F{fid} 无明确文件，需人工定位: {f.get('title','')}")
            continue
        # 读取目标文件，生成补丁
        target = ROOT / file_hint
        if not target.exists():
            print(f"  [skip] F{fid} 文件不存在 {file_hint}")
            continue
        patch = _generate_patch(target, f)
        if not patch:
            print(f"  [skip] F{fid} 无法生成补丁")
            continue
        branch = f"fix/audit-{fid}"
        ok = _apply_and_test(branch, target, patch, f.get("test", ""))
        if not ok:
            print(f"  [skip] F{fid} 补丁测试未通过，留 issue 开放")
            continue
        pr = gh_pr_create(
            f"fix(audit): {f.get('title','')}",
            f"## 修复说明\n{f.get('description','')}\n\n由自主审查 harness 自动生成补丁并通过测试。\nCloses #{issue}",
            branch,
        )
        if pr:
            # #61：只记录「PR 已开」，不关 Issue、不标 fixed。
            # PR body 已含 "Closes #issue"，GitHub 会在合并进默认分支时自动关闭；
            # phase_verify 负责显式核实合并与默认分支可达性。
            state["prs"][fid] = pr
            save_state(state)
            gh_issue_comment(
                issue,
                f"补丁已通过本地测试，已创建 PR #{pr}（分支 `{branch}`）。"
                f"Issue 将在 PR 合并进默认分支并验证后关闭。",
            )
            print(f"  [pr-open] F{fid} -> PR #{pr}, issue #{issue} 保持开放待合并验证")
            done += 1
        else:
            print(f"  [skip] F{fid} PR 创建失败")


def phase_verify(state: Dict[str, Any], default_branch: str = "master") -> None:
    """核实 ``state['prs']`` 中每个 PR 的真实生命周期状态（#61）。

    - PR 已合并且合并提交可达默认分支 → 记入 ``fixed``、评论并关闭 Issue；
    - PR 已合并但提交尚不可达默认分支（如 fetch 失败）→ 保持待验证，下轮重试；
    - PR 未合并即被关闭 → 清除 ``prs`` 条目使 finding 可重试，Issue 若已被关则重开；
    - PR 仍开放 / 查询失败 → 不改状态。
    """
    for fid in list(state["prs"].keys()):
        pr = state["prs"][fid]
        issue = state["issues"].get(fid)
        info = gh_pr_view(pr)
        if info is None:
            print(f"  [verify] F{fid} PR #{pr} 查询失败，保持待验证")
            continue
        pr_state = (info.get("state") or "").upper()
        merged = pr_state == "MERGED" or bool(info.get("mergedAt"))
        if merged:
            sha = info.get("mergeCommitSha") or ""
            if not commit_reachable_on_default(sha, default_branch):
                print(f"  [verify] F{fid} PR #{pr} 已合并但提交 {sha[:9] or '?'} 未确认在 {default_branch}，下轮重试")
                continue
            state["fixed"][fid] = pr
            state["prs"].pop(fid, None)
            save_state(state)
            if issue:
                gh_issue_comment(issue, f"PR #{pr} 已合并进 `{default_branch}`（{sha[:9]}），修复已验证生效。")
                if gh_issue_state(issue) != "CLOSED":
                    gh_issue_close(issue)
            print(f"  [verified] F{fid} PR #{pr} 合并已达默认分支，issue #{issue} 关闭")
        elif pr_state == "CLOSED":
            # 未合并即关闭：清除状态使 finding 可重试；误关的 Issue 重开
            state["prs"].pop(fid, None)
            save_state(state)
            if issue:
                gh_issue_comment(issue, f"PR #{pr} 未合并即被关闭，修复未生效；finding 重新进入待修复队列。")
                if gh_issue_state(issue) == "CLOSED":
                    gh_issue_reopen(issue)
            print(f"  [retry] F{fid} PR #{pr} 未合并被关闭，finding 可重试")
        else:
            print(f"  [verify] F{fid} PR #{pr} 仍开放（state={pr_state or '?'}），issue #{issue} 保持开放")


def _generate_patch(target: Path, f: Dict[str, Any]) -> Optional[str]:
    """调用 LLM 生成 unified diff 补丁（基于文件内容与建议）。无 LLM 返回 None。"""
    try:
        sys.path.insert(0, str(ROOT))
        from nasdx.llm import llm
    except Exception:  # noqa: BLE001
        return None
    src = target.read_text(encoding="utf-8")
    if len(src) > 12000:
        src = src[:12000] + "\n...(truncated)"
    prompt = (
        f"文件 `{target.name}` 内容（部分）：\n```python\n{src}\n```\n\n"
        f"问题：{f.get('description','')}\n建议：{f.get('suggestion','')}\n\n"
        "请只输出针对该问题的 unified diff 补丁（以 `diff --git` 开头，含上下文行），"
        "不要解释。补丁必须能直接 `git apply`。"
    )
    try:
        out = llm.ask([{"role": "user", "content": prompt}], system="你是资深 Python 工程师，只输出可应用的 git diff。")
    except Exception:  # noqa: BLE001
        return None
    if "diff --git" in out:
        return out
    return None


def _apply_and_test(branch: str, target: Path, patch: str, test: str) -> bool:
    """建分支、写补丁、git apply、跑测试；任一失败则回滚分支。"""
    rel = target.relative_to(ROOT)
    patch_path = ROOT / ".audit_patch.diff"
    patch_path.write_text(patch, encoding="utf-8")
    try:
        co = _git(["checkout", "-B", branch])
        if co.returncode != 0:
            print(f"    [checkout fail] {co.stderr[:200]}")
            return False
        ap = _git(["apply", str(patch_path)])
        if ap.returncode != 0:
            print(f"    [patch apply fail] {ap.stderr[:200]}")
            return False
        # 编译检查
        cmp = subprocess.run([PY, "-m", "py_compile", str(target)], cwd=str(ROOT), capture_output=True)
        if cmp.returncode != 0:
            return False
        # 跑测试（若有）
        if test:
            t = subprocess.run([PY, "-m", "pytest", test, "-q"], cwd=str(ROOT), text=True, capture_output=True, timeout=300)
            if t.returncode != 0:
                print(f"    [test fail] {test}: {t.stdout[-300:]}")
                return False
        # 提交/推送：任何一步失败都不能声称修复分支已发布（#61）
        add = _git(["add", str(rel)])
        if add.returncode != 0:
            print(f"    [git add fail] {add.stderr[:200]}")
            return False
        cm = _git(["commit", "-m", f"fix(audit): {target.name} 自主审查修复"])
        if cm.returncode != 0:
            print(f"    [git commit fail] {cm.stderr[:200]}")
            return False
        push = _git(["push", "-u", "origin", branch], timeout=300)
        if push.returncode != 0:
            print(f"    [git push fail] {push.stderr[:200]}")
            return False
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            patch_path.unlink()
        except OSError:
            pass
        subprocess.run(["git", "checkout", "master"], cwd=str(ROOT), capture_output=True)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["all", "execute", "analyze", "report", "issues", "fix", "verify"])
    ap.add_argument("--no-net", action="store_true", help="跳过网络依赖探针")
    ap.add_argument("--limit-modules", type=int, default=None)
    ap.add_argument("--max-fix", type=int, default=3)
    args = ap.parse_args()

    state = load_state()
    if args.phase in ("all", "execute"):
        phase_execute(state, args.no_net, args.limit_modules)
    if args.phase in ("all", "analyze"):
        if not state["executed"]:
            print("[analyze] 无执行结果，先执行 execute 阶段")
            phase_execute(state, args.no_net, args.limit_modules)
        phase_analyze(state)
    if args.phase in ("all", "report"):
        phase_report(state)
    if args.phase in ("all", "issues"):
        if not state["findings"]:
            phase_analyze(state)
        phase_issues(state)
    if args.phase in ("all", "fix"):
        phase_fix(state, args.max_fix)
    if args.phase in ("all", "verify"):
        phase_verify(state)
    if args.phase == "all":
        phase_report(state)
    save_state(state)
    print("[done] 状态已存 .audit_state.json")


if __name__ == "__main__":
    main()

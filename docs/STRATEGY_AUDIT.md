# NASDX Strategy Audit

本轮范围：只做审计和文档，不修策略。结论先行：存在 2 个 P0 策略可信度问题，当前回测和 ETF50 量化结果不能作为可落地策略验证。

## Findings

| Priority | Problem | Evidence | Impact | Fix plan | Verification |
|---|---|---|---|---|---|
| P0 | ETF50 量化回测先用全样本最后一天因子选 TopN，再回测整个历史期 | `quant/etf50_quant.py:136`, `quant/etf50_quant.py:183`, `quant/etf50_quant.py:189` | 选择偏差明显，收益/夏普不能证明策略有效 | 每个再平衡日只用当日以前数据动态选 TopN；当前排名和历史回测分开展示 | `python -B -m pytest -p no:cacheprovider tests/test_etf50_quant_contracts.py` |
| P0 | 回测引擎用当日收盘数据生成信号，并同日按收盘价成交 | `quant/backtest.py:125`, `quant/backtest.py:127`, `quant/backtest.py:132` | 动量、均值回归、因子策略都偏乐观 | 信号日只看前一 bar 或当日收盘后生成；交易在下一 bar 执行 | `python -B -m pytest -p no:cacheprovider tests/test_quant_core_contracts.py` |
| P1 | 参数优化没有真实优化策略，只按输入代码顺序截 TopN 等权 | `quant/vnpy_bridge.py:206`, `quant/vnpy_bridge.py:210`, `quant/vnpy_bridge.py:213` | “最优参数”容易形成过拟合幻觉 | 接入真实 momentum/factor_rank 滚动信号；增加样本外切分 | `python -B -m pytest -p no:cacheprovider tests/test_vnpy_bridge_contracts.py` |
| P1 | 60 股固定池存在板块错配 | `scan_stocks_full.py:24`, `scan_stocks_full.py:38`, `scan_stocks_full.py:52`, `scan_stocks_full.py:73`, `scan_stocks_full.py:86` | 板块强弱统计和候选解释被污染 | 抽成可校验配置，修正板块归属 | `python -B -m pytest -p no:cacheprovider tests/test_stock_pool_contracts.py` |
| P1 | selector 的板块分不是股票真实所属板块分 | `nasdx/selector/factors.py:41`, `nasdx/selector/factors.py:57`, `nasdx/selector/factors.py:202`, `nasdx/selector/scoring.py:282` | `sector_score` 20% 权重近似失效 | 接入真实股票-板块映射；不可得时显式中性并降权 | `python -B -m pytest -p no:cacheprovider tests/test_selector_contracts.py` |
| P1 | selector 页面不检查子进程退出码，可能展示旧 latest | `selector_page.py:89`, `selector_page.py:90`, `selector_page.py:200` | 用户可能误以为刚刚选股成功 | 记录 returncode、生成时间、错误日志；失败时禁止刷新旧结果为“新结果” | `python -B -m pytest -p no:cacheprovider tests/test_workflow_contracts.py` |
| P2 | 60 股扫描 JSON 叫 `stocks60_*`，HTML latest 仍叫 `stocks50_latest.html` | `scan_stocks_full.py:352`, `scan_stocks_full.py:353`, `scan_stocks50.py:1` | 自动化和用户容易误读产物 | 保留兼容别名，新增 `stocks60_latest.html`，文档说明旧名废弃 | `python scan_stocks_full.py` |
| P2 | 测试没有覆盖未来函数、ETF50 偏差、池子口径、selector 失败态 | `tests/test_quant_core_contracts.py:76`, `tests/test_workflow_contracts.py:45` | 策略风险不会被 CI 拦住 | 增加 contract tests 后再改策略 | `python -B -m pytest -p no:cacheprovider tests/test_quant_core_contracts.py tests/test_workflow_contracts.py` |

## Positive Boundary

组合和仓位层已经有 `action_gate`、扫描覆盖率、深度报告和人工复核闸门，扫描分数不会直接变成自动交易指令。但策略展示层仍要清楚标注：在 P0 修复前，量化回测只能当实验性参考。

## Repair Route

| Step | Scope | Files | Rollback risk |
|---|---|---|---|
| 1 | Backtester T+1/no-lookahead contract | `quant/backtest.py`, `tests/test_quant_core_contracts.py` | Medium: 指标会显著变化 |
| 2 | ETF50 rolling rebalance | `quant/etf50_quant.py`, new tests | Medium: 历史报告口径变化 |
| 3 | Real parameter optimization | `quant/vnpy_bridge.py`, tests | Medium: UI 上“最优参数”结果变化 |
| 4 | Stock pool config validation | `scan_stocks_full.py`, config/tests | Low: 修正解释口径 |
| 5 | Selector sector mapping and failure status | `nasdx/selector/*`, `selector_page.py`, tests | Medium: 候选排序会变化 |


# NASDX Master Audit

本轮范围：第一轮系统审计和文档，不直接重构，不删除 CLI，不迁移 Streamlit。结论先行：NASDX 已有可用桌面 MVP 和投研功能链，但离“可维护、可验证、闭环清晰的 Windows 桌面投研工具”还有 3 类关键缺口：策略可信度、运行/路径闭环、产品工作台体验。

## Overall Priority

| Priority | Issue | Evidence | Owner doc |
|---|---|---|---|
| P0 | ETF50 量化回测存在全样本 TopN 选择偏差 | `quant/etf50_quant.py:136`, `quant/etf50_quant.py:183`, `quant/etf50_quant.py:189` | `STRATEGY_AUDIT.md` |
| P0 | Backtester 同日信号同日收盘成交 | `quant/backtest.py:125`, `quant/backtest.py:127`, `quant/backtest.py:132` | `STRATEGY_AUDIT.md` |
| P0 | 当前正式 portable 包 release evidence 失败，`.venv` 有 23 个 `__pycache__` | `run_desktop_release_evidence.py --json --package-dir dist\\NASDX-Desktop` 返回 1，`package_forbidden_failures=23` | `DESKTOP_RELEASE_AUDIT.md` |
| P1 | selector/stocks/quant 批量拉数串行且缓存不贯通 | `run_stock_selector.py:95`, `nasdx/selector/factors.py:45`, `quant/data.py:424` | `PERFORMANCE_AUDIT.md` |
| P1 | 60 股扫描阻塞 Streamlit 主线程 | `app.py:1362`, `app.py:1364` | `PERFORMANCE_AUDIT.md` |
| P1 | 桌面 `NASDX_REPORTS_DIR` 未贯通核心报告模块 | `desktop/paths.py:58`, `nasdx/portfolio.py:213`, `README.md:304` | `ARCHITECTURE_AUDIT.md` |
| P1 | 桌面到一次投研缺主线 CTA，Streamlit 未暴露 selector 一键闭环 | `app.py:565`, `run_investment_workflow.py:24`, `selector_page.py:185` | `PRODUCT_FLOW_AUDIT.md` |
| P1 | 核心投研测试弱于桌面合同测试 | `tests/test_quant_core_contracts.py:76`, `tests/test_workflow_contracts.py:45` | `TEST_COVERAGE_AUDIT.md` |
| P2 | 首页/侧边栏信息架构不一致、selector 中英混用 | `app.py:244`, `selector_page.py:77` | `PRODUCT_FLOW_AUDIT.md` |
| P2 | 本地 ignored 运行产物会影响 latest 判断 | `git status --short --ignored` 显示 `reports/`, `dist/`, logs, DB | `ARCHITECTURE_AUDIT.md` |

## Phase A: 不改业务结果的性能和体验修复

| Field | Plan |
|---|---|
| Goal | 让现有功能更快、更不阻塞、更符合桌面运行边界，但不改变策略结果 |
| Files | `app.py`, `selector_page.py`, `quant/data.py`, `desktop/control.py`, `run_desktop_release_check.py`, packaging scripts, tests |
| Steps | stocks60 改后台任务；`get_ohlcv` 加交易日缓存；selector UI 增 limit/timeout；release evidence 顺序后移；smoke 后清理 package `.venv` cache；Open App 自动启动服务 |
| Verification | `python -B -m pytest -p no:cacheprovider tests/test_desktop_* tests/test_workflow_contracts.py`; `python -B run_desktop_release_evidence.py --json --package-dir dist\\NASDX-Desktop`; `python -B run_desktop_doctor.py --json` |
| Rollback risk | Low to medium；主要是 UI 状态和 release gate 顺序 |
| Do not touch | 不改策略公式、不改报告 schema、不迁移 UI 框架、不删除 CLI |

## Phase B: 修复策略、回测和投研闭环

| Field | Plan |
|---|---|
| Goal | 让策略指标可信，让 selector/ETF/个股扫描能进入稳定投研闭环 |
| Files | `quant/backtest.py`, `quant/etf50_quant.py`, `quant/vnpy_bridge.py`, `scan_stocks_full.py`, `nasdx/selector/*`, `run_investment_workflow.py`, tests |
| Steps | Backtester T+1 执行；ETF50 rolling rebalance；参数优化接真实策略；修正 60 股池；selector 接真实板块映射；selector 页面检查退出码和报告时间 |
| Verification | `python -B -m pytest -p no:cacheprovider tests/test_quant_core_contracts.py tests/test_workflow_contracts.py tests/test_selector_contracts.py`; 写 reports 的端到端 smoke 只在用户允许时跑 |
| Rollback risk | Medium to high；策略指标和候选排序会变化 |
| Do not touch | 不把扫描结果变成自动下单；不绕过人工复核和 action gate |

## Phase C: 重构架构和桌面正式发布

| Field | Plan |
|---|---|
| Goal | 形成 service 层、统一 runtime/report 路径、完成普通用户级安装包闭环 |
| Files | new `nasdx` service/path modules, `app.py`, scanner CLIs, desktop packaging docs/tests |
| Steps | 建 reports/history/data path context；scanner import-safe；workflow service；报告历史页；launcher exe 成默认快捷方式；安装/卸载/用户数据保留验证 |
| Verification | `python -B run_product_readiness.py`; `python -B run_desktop_release_check.py --full-package --zip-package --compile-installer`; installer roundtrip in disposable Windows profile |
| Rollback risk | High；涉及路径兼容和安装包行为 |
| Do not touch | 不重写 `app.py`，不迁移 Electron/Tauri/PySide6，不提交 reports/logs/cache/db/dist/build/secrets |

## GitHub Issue Plan

| Priority | Issue title |
|---|---|
| P0 | ETF50 quant backtest uses full-sample TopN selection |
| P0 | Backtester executes same-day close signals on same-day close prices |
| P0 | Desktop release evidence fails on portable package `__pycache__` contamination |
| P1 | Runtime reports/data paths are not fully desktopized |
| P1 | Selector and Stocks60 scans are slow or UI-blocking |
| P1 | Product flow lacks a first-class research workbench and report history |

## Verification Used In This Audit

| Command | Result |
|---|---|
| `python -B run_desktop_doctor.py --json` | PASS, 15 checks |
| `python -B run_desktop_completion_audit.py --json` | PASS, 10 checks |
| `python -B run_investment_workflow.py --workflow selector --analysis-mode rules --dry-run` | PASS, no writes |
| `python -B run_desktop_release_evidence.py --json --package-dir dist\\NASDX-Desktop` | FAIL as expected, `package_forbidden_failures=23` |
| `rg -n "def test_" tests` | 114 tests |


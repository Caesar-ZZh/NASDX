# NASDX Architecture Audit

本轮范围：只做审计和文档，不拆模块。结论先行：项目桌面包装层已经清晰，核心投研层还偏脚本式；最需要先补的是路径上下文、service 层和 import-safe scanner。

## Findings

| Priority | Problem | Evidence | Impact | Fix plan | Verification |
|---|---|---|---|---|---|
| P1 | `app.py` 混合 UI、任务状态、报告读取、业务触发、CLI 编排 | `app.py:5`, `app.py:104`, `app.py:523`, `app.py:1259`, `app.py:1364`, `app.py:1451` | 页面小改可能影响业务路径，service 复用困难 | 先抽报告读取和工作台动作 service，再抽页面 helper | `python -B run_final_audit.py` |
| P1 | 桌面 runtime/report 路径未贯通业务层 | `desktop/paths.py:58`, `README.md:304`, `nasdx/portfolio.py:66`, `nasdx/portfolio.py:213`, `nasdx/review_snapshot.py:36`, `quant/etf50_quant.py:248` | 安装版数据和源码 checkout 数据边界不清 | 新增薄路径上下文：reports/history/data roots，默认保持兼容 | 临时目录端到端 tests |
| P1 | 扫描脚本不是 import-safe service | `scan_etf50.py:26`, `scan_etf50.py:365`, `scan_stocks_full.py:177`, `scan_stocks_full.py:352` | UI/CLI 只能靠 subprocess 串，难测难复用 | 包成 `run_scan(output_dir=...)` + `main()`；导入不联网不写文件 | import-safe tests |
| P1 | workflow 主要通过 subprocess 串 CLI | `run_investment_workflow.py:74`, `run_investment_workflow.py:137`, `run_investment_workflow.py:225` | 编排失败原因难定位，UI/CLI parity 难保证 | 抽 workflow service，CLI 只解析参数 | mock step runner tests |
| P2 | 源码树存在 ignored 运行产物污染 | `.gitignore:2`, `.gitignore:7`, `.gitignore:10`, `.gitignore:56`; `git status --ignored` 显示 `reports/`, `dist/`, `nasdx_history.db`, logs | 本地“latest”判断可能受历史产物影响 | 审计和测试默认使用临时目录；不要提交产物 | `git status --short --ignored` |
| P2 | final audit 会跳过 ignored 目录 | `run_final_audit.py:1256`, `run_final_audit.py:1261`, `run_final_audit.py:1264` | 源码交付干净不等于 runtime 包干净 | release evidence 作为包级证据，不用 final audit 替代 | `python -B run_desktop_release_evidence.py --json --package-dir dist\\NASDX-Desktop` |

## Target Layering

| Layer | Current state | Next step |
|---|---|---|
| UI | Streamlit routes in `app.py`, some pages split | Keep Streamlit; move actions and readers out |
| Service | Mostly implicit in CLI scripts | Add workflow/report/scan services |
| Data | `quant/data.py`, `nasdx/market_sources.py`, selectors use AkShare directly | Cache and data-source health in one place |
| Quant | Factor/backtest modules exist | Fix no-lookahead and rolling rebalance |
| Agent | Multi-agent environment exists | Add output quality gates |
| Report | Many modules write `PROJECT_DIR/reports` | Central reports dir resolver |
| Desktop | Launcher/packaging mature | Fix release evidence and runtime path consistency |

## Minimal Refactor Order

| Order | Action | Why first |
|---|---|---|
| 1 | Add path/report context helpers | Reduces desktop/data/report inconsistency |
| 2 | Extract report readers and plan actions from `app.py` | Low-risk UI slimming |
| 3 | Make scanners import-safe | Enables tests and shared service use |
| 4 | Extract workflow orchestration service | Makes UI and CLI parity testable |
| 5 | Extract Streamlit table/card helpers | Reduces `app.py` size after behavior is protected |


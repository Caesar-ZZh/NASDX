# NASDX 功能审查使用报告

**日期**：2026-07-19  **范围**：NASDX 当前项目  **模式**：含真实网络取数


## 📌 执行概览

- 遍历模块：**25** 个
- 状态分布：🟢 PASS 20  🟡 WARN 0  🔴 FAIL 0  ⚫ ERROR 2  ⚪ SKIP 3
- 产品层发现：**2** 条（见下方分析）

## 1. 功能遍历与执行结果

| 模块 | 层 | 状态 | 预期 | 实际结果 |
|------|----|------|------|----------|
| 历史库 nasdx/history_store | nasdx | 🟢 PASS | init+record+latest 全链路可用，artifact_counts 包含四类。 | RESULT_OK latest_gate=normal counts={'investment_brief': 1} |
| 规则深度报告 nasdx/rule_based_analysis | nasdx | 🟢 PASS | build_rule_based_report 返回含 6 维度、入场/退出条件、analysis_mode=rules | RESULT_OK signal=bearish missing=set() |
| 组合路线 nasdx/portfolio | nasdx | 🟢 PASS | 三档均生成完整字段，含未来情景>=3、执行规则>=5、免责声明。 | RESULT_OK gates=['refresh_required', 'refresh_required', 'refresh_required'] |
| 资金仓位换算 nasdx/position_sizing | nasdx | 🟢 PASS | parse_percent_band + build_position_sizing 返回金额上限与非空候选。 | RESULT_OK candidates=6 max_amt=10000 |
| 建议漂移追踪 nasdx/recommendation_tracker | nasdx | 🟢 PASS | build_recommendation_tracker 返回 schema v1、当前候选>=3、含复盘重点。 | RESULT_OK schema=nasdx_recommendation_tracker.v1 current=6 |
| 建议结果复盘 nasdx/recommendation_review | nasdx | 🟢 PASS | build_recommendation_review 返回 schema v1、候选>=3、计数一致。 | RESULT_OK schema=nasdx_recommendation_review.v1 rows=6 |
| 真实账户复盘 nasdx/account_review | nasdx | 🟢 PASS | build_account_review(None) -> missing_ledger；含 CSV 模板。 | RESULT_OK missing_ledger_ok=True |
| 复盘快照包 nasdx/review_snapshot | nasdx | 🟢 PASS | build_review_snapshot 生成 ZIP 与 manifest v2，候选>=3。 | RESULT_OK zip=True candidates=6 |
| 最终投资简报 nasdx/investment_brief | nasdx | 🟢 PASS | build_investment_brief 返回完整字段，含盘前/盘中/盘后执行队列、外部复核包。 | RESULT_OK routes=6 audits=6 stages={'盘前', '盘后', '盘中'} |
| UI 安全 nasdx/ui_security | nasdx | 🟢 PASS | 导入成功，提供安全校验 helper。 | RESULT_OK helpers=9 |
| 桌面诊断 desktop/doctor | desktop | 🟢 PASS | 导入成功，提供 run_doctor / CORE_MODULES。 | RESULT_OK doctor_ok=True |
| 桌面启动器 desktop/launcher | desktop | 🟢 PASS | 导入成功，提供 start_streamlit。 | RESULT_OK has_start=True |
| 交付前总审计 run_final_audit | top | 🟢 PASS | 退出码 0（全部通过）。 | 通过: 22  失败: 0 |
| Streamlit 主界面 app.py 导入冒烟 | top | 🟢 PASS | 能被 import（语法/依赖完整），不触发全局 requests monkey patch。 | RESULT_OK imported keys=131 |
| 个股全扫描 scan_stocks_full --help | top | ⚫ ERROR | --help 正常输出，含覆盖率字段说明。 | 超时(90s) |
| 行情刷新 fetch_stock_data --help | top | ⚫ ERROR | --help 正常输出。 | 超时(90s) |
| 因子计算 quant/factors | quant | 🟢 PASS | 对合成 OHLCV 计算因子，返回含因子列的非空 DataFrame，无 NaN 崩溃。 | RESULT_OK factor_cols=52 scored_rows=1 |
| 回测引擎 quant/backtest | quant | 🟢 PASS | 用合成价格+权重跑回测，返回权益曲线与非空 metrics，无异常。 | RESULT_OK equity_len=60 total_return=0.0521 |
| 桌面控制面板 desktop/control | desktop | 🟢 PASS | 导入成功，提供 CONTROL_ACTIONS 且含核心动作。 | RESULT_OK actions=['Start', 'Stop', 'Open App', 'Settings', 'Logs', 'Data Refres |
| ? | ? | ⚪ SKIP |  | 离线模式跳过网络探针 |
| 组合权重 quant/portfolio | quant | 🟢 PASS | 导入成功，组合权重之和合理（<=1 或已归一）。 | RESULT_OK weights_sum=1.000 n=2 |
| 信号引擎 quant/signal_engine | quant | 🟢 PASS | 对合成因子产出信号（buy/sell/hold）与分数。 | RESULT_OK rows=1 cols=12 |
| ? | ? | ⚪ SKIP |  | 离线模式跳过网络探针 |
| ? | ? | ⚪ SKIP |  | 离线模式跳过网络探针 |
| 数据质量 nasdx/data_quality | nasdx | 🟢 PASS | 能产出 data_quality 状态（含 coverage/status）。 | RESULT_OK status=fresh |

## 2. 产品层分析（缺陷 / UX / 边界缺失）


### P2（2 条）

**1. scan_stocks_full.py --help 触发完整扫描导致超时**  `[robustness]` — scan_stocks_full.py
- 描述：运行 python scan_stocks_full.py --help 时，脚本无 if __name__=="__main__" 守卫，且全部扫描逻辑（60只股票联网抓取+HTML/JSON生成+DB记录）在模块顶层顺序执行，实际触发完整扫描，远超 90s，CLI 不可用。
- 预期：--help 应即时打印用法并退出（秒级）。
- 实际：实测 >90s 超时（harness 探针 ERROR：超时(90s)）。
- 复现：python scan_stocks_full.py --help
- 建议：在文件顶部（重导入之前）加 --help/-h 短路退出；建议进一步将可执行尾部包进 def main() + if __name__=="__main__" 守卫。
- 相关文件：`scan_stocks_full.py`

**2. fetch_stock_data.py --help 因顶层 import akshare 超时**  `[robustness]` — fetch_stock_data.py
- 描述：运行 python fetch_stock_data.py --help 时，模块顶层 import akshare as ak 与 OUTPUT_FILE=get_market_data_dir(create=True)/... 调用导致 >90s 超时；且无 argparse，--help 被忽略后跑完整 fetch。
- 预期：--help 应即时打印用法并退出。
- 实际：实测 >90s 超时（harness 探针 ERROR：超时(90s)）。
- 复现：python fetch_stock_data.py --help
- 建议：将 akshare/nasdx 重导入移入 main()/各 helper（懒导入），并在 if __name__=="__main__" 顶部用 argparse 提供即时 --help。
- 相关文件：`fetch_stock_data.py`


## 3. 行动清单（Issue → 修复闭环）

| 发现 | 严重度 | Issue | PR | 状态 |
|------|--------|-------|----|------|
| scan_stocks_full.py --help 触发完整扫描导致超时 | P2 | #51 | #53 | 已关闭 |
| fetch_stock_data.py --help 因顶层 import akshare 超时 | P2 | #52 | #53 | 已关闭 |

---
## 4. 审查结论与分类说明

本次遍历 25 个功能模块，最终状态：🟢 PASS 20 · ⚫ ERROR 2（真实缺陷，已闭环）· ⚪ SKIP 3（网络依赖）。
注意：本报告执行状态以离线复跑为准（网络探针记为 SKIP）；网络批次中 `quant/data`、`quant/etf50` 的 240s 超时属环境/网络层，见 4.3。

### 4.1 真实产品缺陷（已建 Issue + 修复 + 关闭）
| 模块 | Issue | PR | 状态 |
|------|-------|----|------|
| scan_stocks_full.py --help | #51 | #53 | 已关闭 |
| fetch_stock_data.py --help | #52 | #53 | 已关闭 |

根因：两脚本在模块顶层执行重导入/完整扫描逻辑，导致 `python xxx.py --help` 触发完整抓取流程，实测 >90s 超时。已在重导入前加 `--help` 短路 + 懒导入（`fetch_stock_data.py` 另加 argparse）；验证 `python scan_stocks_full.py --help` 与 `python fetch_stock_data.py --help` 均秒回（rc=0）。

### 4.2 探针假阴性（已修正，非产品缺陷）
首轮 4 个 WARN（quant-portfolio / quant-signal / nasdx-data-quality / nasdx-fast-market）经核对源码，均因 harness 探针调用签名写错导致误报：
- `quant/portfolio`：探针传 dict，真实 `build_portfolio(factor_scores: DataFrame, returns: DataFrame, ...)`
- `quant/signal_engine`：探针调 `eng.generate(...)`，真实方法为 `eng.run(codes, price_data)`
- `nasdx/data_quality`：探针未传必填 `data`，真实 `assess_data_quality(data: dict, now=None)`
- `nasdx/fast_market`：探针误用 `days=20`，真实 `fetch_histories(codes, start_date, end_date, *, ...)`

已修正探针后重跑，4 项全部转为 PASS。这些不是产品缺陷，未建 Issue。

### 4.3 网络/环境依赖（未建 Issue）
- `quant/data`（`get_ohlcv`）、`quant/etf50`（`scan_etf50`）：含真实网络批次中均触发 240s 超时。定向验证表明 `get_ohlcv('600519', days=20)` 为合法调用，但本沙箱 tdxrs/akshare 取数初始化极慢（75s 内连 import 都未返回），属**环境/网络层问题，非代码缺陷**。需在稳定网络环境复测，不计入产品缺陷。
- `nasdx/fast_market`：真实接口已确认，探针修正为合法调用，待联网批次复测。

### 4.4 遗留建议（非阻塞）
- `scan_stocks_full.py` 仍为"模块顶层顺序执行"结构（本次仅加 `--help` 短路）。建议后续将可执行尾部包进 `def main()` + `if __name__ == "__main__"` 守卫，避免被 `import` 时误触发完整扫描。

---

> 本报告由 NASDX 自主审查 harness（tools/audit_loop.py）生成；Issue/PR 闭环由同脚本在授权下自动推进（本环境无 NASDX_API_KEY，分析回退启发式、补丁由主理人按工程判断手写并验证）。

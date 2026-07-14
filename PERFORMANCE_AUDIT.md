# NASDX Performance Audit

本轮范围：只做审计和文档，不重构业务代码。结论先行：未发现 P0；当前性能风险主要来自多标的串行拉数、缓存未贯通、部分 Streamlit 操作阻塞 UI。

## Findings

| Priority | Problem | Evidence | Fix plan | Verification | Risk |
|---|---|---|---|---|---|
| P1 | 动态选股默认最多处理 5000 只，逐只拉 90 日 K 线 | `selector_page.py:90`, `run_stock_selector.py:39`, `run_stock_selector.py:95`, `nasdx/selector/factors.py:45` | 先给 UI 暴露 limit/timeout；再加入交易日快照缓存和小批量并发 | `python -B run_stock_selector.py --top 20 --limit 100 --output-dir $env:TEMP\\nasdx-selector-bench` | 并发过高会触发数据源限流，必须限速 |
| P1 | 个股 60 扫描在 Streamlit 主线程同步等待 | `app.py:1362`, `app.py:1364`, `scan_stocks_full.py:181`, `nasdx/market_sources.py:26` | 改成 ETF50 同类后台任务，统一 task_id、日志和退出码 | `streamlit run app.py`; 点击 60 股扫描时页面应可继续响应 | UI 状态机改动会影响页面刷新 |
| P1 | 量化页和持仓页批量拉数缺少真实缓存 | `quant_page.py:417`, `quant_page.py:517`, `position_page.py:251`, `quant/data.py:342`, `quant/data.py:424` | 给 `get_ohlcv/get_batch_ohlcv` 加按 code/days/trade-date 的有界缓存 | `python -B -c "import time; from quant.data import get_ohlcv; c='159611'; t=time.perf_counter(); get_ohlcv(c,180); a=time.perf_counter()-t; t=time.perf_counter(); get_ohlcv(c,180); b=time.perf_counter()-t; print(round(a,2), round(b,2))"` | 缓存日期口径错会使用旧行情 |
| P1 | full/quick 工作流串行跑刷新、扫描、分析、简报 | `run_investment_workflow.py:98`, `run_investment_workflow.py:185`, `fetch_stock_data.py:136`, `fetch_stock_data.py:210` | 增加同交易日 freshness gate，默认复用新鲜快照；只在 stale 时刷新 | `python -B run_investment_workflow.py 603501 --workflow full --dry-run` | 新鲜度规则过宽会误用旧数据 |
| P2 | 首页已有缓存函数但最近报告区绕过缓存 | `app.py:163`, `app.py:489` | 首页统一走 `load_recent_reports` 或 service 层查询 | 页面 rerun 时最近报告区不重复 glob 全目录 | 需要确认排序和展示字段不变 |
| P2 | 数据源健康状态未缓存 | `quant/data.py:207`, `quant/data.py:312`, `quant/data.py:385` | 会话级记录 tdxrs/AkShare/mootdx 的短 TTL 健康状态 | mock 某源失败时批量请求只付一次失败成本 | 健康缓存不能长到跨交易时段 |
| P2 | 深度分析每 3 秒读完整日志并 rerun | `app.py:1472`, `app.py:1500` | 改成 tail/offset 读取；长日志只读新增部分 | 长日志场景 rerun 耗时稳定 | offset 状态要和 task_id 绑定 |
| P2 | ETF50 规则/量化扫描逐只拉数 | `scan_etf50.py:52`, `scan_etf50.py:164`, `quant/etf50_quant.py:107` | 加交易日缓存、失败源短路、可观测耗时日志 | `python -B -c "import time; from quant.data import get_batch_ohlcv; codes='159611,513160,515880,588200,512480'.split(','); t=time.perf_counter(); r=get_batch_ohlcv(codes, days=180, verbose=False); print(len(r), round(time.perf_counter()-t,2))"` | 数据源限流需保留退避 |

## Slow Path Map

| Path | Current behavior | Target behavior |
|---|---|---|
| Desktop launch | doctor and launcher are fast enough; Streamlit still imports a large `app.py` | Keep launcher thin; defer heavy data work until user action |
| ETF50 scan | background thread in UI, but scanner itself is serial | Preserve background behavior; cache per trade date |
| Stocks60 scan | synchronous spinner in UI | Background task with timeout, log, and exit status |
| Selector | all-A universe plus per-code K-line fetch | Explicit small/normal/full modes |
| Quant/backtest | repeated `get_ohlcv` calls | cached OHLCV service with bounded concurrency |
| Deep analysis | subprocess log polling | tail-based progress and structured status |

## Recommended Benchmarks

| Purpose | Command |
|---|---|
| Safe desktop baseline | `python -B run_desktop_doctor.py --json` |
| Workflow dry-run | `python -B run_investment_workflow.py 603501 --workflow full --dry-run` |
| Batch OHLCV timing | `python -B -c "import time; from quant.data import get_batch_ohlcv; codes='159611,513160,515880,588200,512480'.split(','); t=time.perf_counter(); r=get_batch_ohlcv(codes, days=180, verbose=False); print(len(r), round(time.perf_counter()-t,2))"` |
| Selector small sample | `python -B run_stock_selector.py --top 20 --limit 100 --output-dir $env:TEMP\\nasdx-selector-bench` |

## 2026-07-14 Selector History Update

| Item | Evidence |
|---|---|
| Root cause | Per-symbol Tencent fan-out triggered provider throttling; 30 histories took 34-58 seconds and individual requests stretched to 17-31 seconds. |
| Fix | Issue #25 adds one reusable QFQ `TdxHqClient` for SSE/SZSE stocks and indices, while BSE and missing symbols use the existing Tencent fallback concurrently. |
| Data parity | Three representative stocks had 81 overlapping dates and identical latest closes versus Tencent; historical differences were limited to QFQ rounding. |
| Real benchmark | 29 tdxrs histories plus one BSE Tencent fallback completed 30/30 in 8.22 seconds without disk cache. |
| End-to-end selector | The real 30-symbol selector covered 5,528 live listings, completed its history-factor stage in 6.8 seconds, and wrote JSON/Markdown/HTML reports in 32.1 seconds. |
| Desktop dependency | `tdxrs>=0.6.5,<0.7.0`; CPython 3.11 Windows x64 wheel verified, and both hashed Windows dependency locks are refreshed with the pinned toolchain. |

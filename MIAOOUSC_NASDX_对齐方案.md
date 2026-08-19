# miaoousc.xyz 拆解 · 与 NASDX 功能对齐方案

> 逆向对象：`https://miaoousc.xyz/`（「元基鉴股分析引擎」落地页 + 市场研究驾驶舱 `/mrd/`）
> 结论先行：该站是**开源项目组合部署**，作者 `zhangsa69`，全部 MIT 开源、可直接克隆。
> 拆解所用真实源码（已浅克隆至 `_reverse_miaoou/`）：
> - `stock-analysis-base`（TypeScript/Python，2096★）—— 投研 Agent 平台「Vibe-Research」，含 A股/全球数据源工具包与 FastAPI 后端
> - `stock-web-system`（Python）—— 「基于 NotebookLM 的上市公司财报全量解读系统」= 元基鉴股引擎
> - `stock-analysis-platform`（React18+FastAPI）—— 作者早期的美股/A股报告平台（与本站关系较弱，仅作架构参照）

---

## 0. 一句话结论与重要纠正

1. **miaoousc.xyz = `stock-web-system`（财报引擎 + 落地页） + `stock-analysis-base/backend`（投研 API） + `/mrd/` 驾驶舱（React SPA）的 Docker 组合**。无黑盒，全开源。
2. **你点名的 11 个数据源里，实际接入的只有：东方财富、新浪、腾讯、同花顺（含问财 iwencai）、巨潮。** 海外在**运行后端**里实际只用了**东方财富的全球子集**（全球指数 + 美港股）。
3. **Binance / OKX / CNBC / FRED / 生意社(100ppi) 在本项目代码中 0 接入**（全仓 grep 验证）。SEC EDGAR / 美国财政部 / CBOE / FINRA / Yahoo 的代码存在于 `global-stock-data` 工具包里，但**未被部署进运行后端**（仅作本地自托管研究用）。→ 这 5 个源大概率是与其他项目的混淆，或你的**期望新增项**，并非本站已有功能。
4. **NASDX 与 Vibe-Research 在 A股数据层高度重叠**：NASDX 已有「腾讯 gtimg 行情优先 + 同花顺 mootdx/ths_bridge + akshare 兜底」的数据层，且量化/决策内核远强于对方。对齐重点不是"补数据"，而是**搬运 Vibe 独有且 NASDX 缺失的「资讯 / 全球 / 复盘 / 辩论 / 研报 / 驾驶舱」模块**，同时**保留 NASDX 的回测 / 决策评估 / 组合账本 / 盘中 copilot / 证据层 / 桌面化**。

---

## 1. 整体架构拆解

### 1.1 部署拓扑（miaoousc.xyz 真实组成）

```
                          miaoousc.xyz (Cloudflare CDN)
                                  │
        ┌─────────────────────────┼──────────────────────────┐
        ▼                         ▼                           ▼
  [落地页 + 财报引擎]        [市场研究驾驶舱 /mrd/]        [投研 API]
  stock-web-system           React SPA + Node:3000          stock-analysis-base/backend
  nginx:80 → FastAPI:8000    (mrd-server 数据代理)          FastAPI :8900
       │  ├ Postgres:5432          │ 直连/代理                  │
       │  ├ Redis:6379             │ qt.gtimg.cn (行情)         │ astock.py / gstock.py
       │  └ Celery Worker ──► Hermes Agent:9888 ─► CNINFO财报 ─► NotebookLM
       │                          │ api-one-wscn.awtmt.com (快讯)
       │ 用户/点券/报告/邮件        │ 东财全球指数(push2delay)
       └ 首页市场速览(纯前端): 腾讯gtimg(A股指数)+东财push2delay(全球指数)

  投研 API 后端(:8900) 模块：astock(A股) / gstock(全球) / market(复盘聚合)
  / newsradar(资讯雷达) / debate(多空) / chat(接入AI) / portfolio / myreports / reflection
  前端 Vibe-Research(React19)：11 个页面，vite 代理 /api → :8900
```

### 1.2 技术栈

| 层 | miaoousc.xyz（Vibe-Research 体系） | NASDX（我们） |
|---|---|---|
| 前端 | React 19 + Router7 + ECharts + Zustand + Tailwind（Vite）；`/mrd/` 独立 React 大屏 | Streamlit（Notion 风格 UI） |
| 后端 | FastAPI（Python）:8900，可选 API Key 鉴权，NDJSON 流式 | Python CLI + Streamlit 后端进程 |
| 数据层 | 直连东财/腾讯/同花顺/新浪/巨潮 + akshare + mootdx | 腾讯 gtimg 优先 + 同花顺 mootdx/ths + akshare 兜底 |
| AI 层 | 可插拔：API 多模型 / 本机 CLI 订阅 / MCP；NotebookLM（财报） | DeepSeek Chat 固定接入 |
| 存储 | Postgres + Redis（财报引擎）；本地 JSON/SQLite（投研） | 本地 SQLite（决策/账本/历史） |
| 部署 | Docker Compose（nginx + 多容器） | Windows 桌面打包（PyInstaller/启动 bat） |

### 1.3 页面结构（Vibe-Research 前端 11 页 + 驾驶舱）

| 路由 | 页面 | 核心内容 |
|---|---|---|
| `/daily-review` | 每日复盘 | 大盘指数 / 全球市场 / 关注股 / 短线情绪（连板梯队·封板率·炸板率·晋级率）/ 成交 TOP20 / 市场宽度 / 板块资金 / AI 复盘 |
| `/intel` | 资讯雷达 | 12 赛道 108 RSS 源，AI「今日要点」提炼 |
| `/sectors` `/sectors/:key` | 板块中心 / 详情 | 板块 + 产业链环节骨架 |
| `/stock-data` | 个股数据 | A股：行情/估值矩阵/财报速览/估值分位/资金面/龙虎榜/解禁/概念/互动易；美港股/韩股：行情+关键财务指标 |
| `/debate` | 多空调论 | 多 agent：事实底稿→多/空研究员→中立主持归纳分歧 |
| `/watchlist` | 自选股 | 批量粘贴代码，实时行情开关（交易时段 3s 刷新） |
| `/portfolio` | 我的持仓 | 录入即实时盈亏，已清仓记录（本地） |
| `/my-reports` | 我的研报 | 上传 PDF/Word/txt/图，按文件名自动归档（本地） |
| `/notes` | 研究记录 | 复盘/要点/问AI/辩论结果沉淀 + 反思审计 |
| `/settings` | 接入 AI / 设置 | API 多模型 / CLI 订阅 / MCP |

`/mrd/` 驾驶舱（独立大屏）：全球关键指数、板块热点、资金流向、7×24 快讯、个股榜单、大宗商品、美债曲线、产业链全景。

---

## 2. 核心功能模块与交互逻辑

| 模块 | 交互逻辑要点 |
|---|---|
| **每日复盘聚合** (`market.py`) | `get_overview()`=市场情绪(涨跌家数/涨停跌停/广度分档)+板块资金流（akshare `stock_market_activity_legu`/`stock_fund_flow_industry`）；`get_short_term_emotion()`=东财涨停四池聚合成**零个股名**的连板梯队/封板率/炸板率/晋级率（守"零标的"红线）；`get_turnover_top()`=成交 TOP20；全站共享 5 分钟 TTL 缓存 |
| **资讯雷达** (`newsradar.py`) | `news_sources.json` 定义 12 赛道/108 RSS；`ThreadPoolExecutor(40)` 并发抓取 → 合规红线过滤（赌/预测/加密/色情）→ 按赛道分组时间倒序；AI「今日要点」复用 `/api/chat` 交给用户自己的模型 |
| **个股数据** (`astock.py`+`gstock.py`) | 单 code 聚合：腾讯行情 + 东财研报/公告/资金面 + 同花顺财务/一致预期 + 百度估值分位 + 东财全球(美港股)；所有接口按传入代码返回客观数据，**不预置标的、不推荐** |
| **多空调论** (`debate.py`) | 后端先拉 13 项客观事实底稿 → 多方/空方基于同一份数据各自立论（可选交叉反驳）→ 中立主持归纳「共识/分歧点/验证清单/数据缺口」；**刻意不产出买卖结论**；约 100s/轮、3 次模型调用 |
| **自选/持仓/研报/笔记** | 均**只存本地、不上传**；研报拖拽上传按文件名自动归行业；笔记沉淀 + 「反思审计」（让 AI 审推理链哪处有数据撑、最脆弱一环在哪） |
| **接入 AI** (`chat.py`+`cli_runtime.py`) | 三种接入：① API 多模型（自动填 baseURL，OpenAI 兼容 function-calling 流式）② 本机 CLI 订阅（免 key，调已登录 CLI）③ MCP（挂进 Claude Code 等）；用户配置随请求传入，后端不持久化 |

---

## 3. 国内外数据源 API 对接方式（重点）

### 3.1 国内源（实际接入）

| 数据源 | 对接端点 / 方式 | 鉴权 | 限流/代理策略 | 在 Vibe 中用途 |
|---|---|---|---|---|
| **东方财富**（主源） | `push2.eastmoney.com/api/qt/clist/get`(榜单) · `/slist/get`(板块) · `/stock/get`(个股) · `push2delay`(降级) · `push2ex.eastmoney.com/getTopicZTPool`等(涨停四池, ut=`7eea3edcaed734bea9cbfc24409ed989`) · `reportapi.eastmoney.com/report/list`(研报) · `datacenter-web.eastmoney.com/api/data/v1/get`(龙虎榜/解禁/融资融券/大宗/股东户数/分红, reportName 枚举) · `push2his.eastmoney.com/.../fflow/daykline/get`(资金流) · `emappdata.eastmoney.com/stockrank/getHotStockRankList`(热门概念) · `searchapi.eastmoney.com/api/suggest/get`(搜索, token=`D43BF722C8E33BDC906FB84D85E326E8`) · `np-anotice-stock.eastmoney.com/api/security/ann`(公告) | 无 Key；Referer 头（`quote.eastmoney.com`/`data.eastmoney.com`） | **`em_get` 统一入口：串行限流 ≥1s + 直连优先、失败降级系统代理**（`VR_DATA_PROXY=1` 强制走代理）；避免 Clash 代理挂掉国内站 | 行情/榜单/研报/资金面/打板/板块/概念/搜索/公告/美港股财务 |
| **腾讯财经** | `qt.gtimg.cn/q=`(行情, GBK 解码, 53+ 字段) · `ifzq.gtimg.cn/appstock/app/mktHs/rank`(排行) · `/app/minute/query`(分时) | 无 | 仅标准库 `urllib`，**不封 IP**，永远可用（作为行情主源兜底） | A股/指数实时行情、驾驶舱报价 |
| **新浪** | `hq.sinajs.cn`(ETF期权T型) · `quotes.sina.cn`(财报三表) · 日度四档单净额(资金流备胎) · 美股 K线(回看1984) | 无 | 备胎源 | 期权/财报三表/资金流备胎/美股K线 |
| **同花顺（含问财 iwencai）** | `basic.10jqka.com.cn`(一致预期 EPS) · `stock_financial_abstract_ths`(财务摘要) · 同花顺热点/涨停揭秘 · **iwencai 语义搜索需 API Key**（skillhub 申请，X-Claw 鉴权） | 一致预期/财务经 **akshare** 惰性封装；iwencai 需 Key | akshare 缺失时优雅报错 | 一致预期/财务摘要/热点/涨停揭秘/语义搜索 |
| **巨潮(CNINFO)** | `irm.cninfo.com.cn/newircs/...`(互动易问答) · `stock_zh_a_disclosure_report_cninfo`(公告, akshare) | 无 | requests/akshare | 互动易、公告 |
| **百度股市通** | `stock_zh_valuation_baidu`(akshare) | 无 | akshare | PE-TTM/PB 历史估值分位 |
| **mootdx** | `Quotes.factory().bars()`(K线) · `.finance()`(财务) | 无 | 惰性导入，缺失报错 | K线、财务快照 |

### 3.2 海外源

| 数据源 | 真实状态 | 对接方式（来自 `global-stock-data` 工具包） |
|---|---|---|
| **东方财富全球**（实际部署在用） | ✅ 运行后端已并入 | `gstock.py`：全球指数(secid 100.DJIA/SPX/NDX/100.HSI/124.HSTECH) + 美港股行情(`push2`优先降级`push2delay`) + 搜索(`searchapi`) + GMAININDICATOR 财务指标 + 港股现金流 |
| **SEC EDGAR** | ⚠️ 代码在 skill，**未部署** | `edgar.sec.gov`；需声明 User-Agent(`SEC_CONTACT`)，官方限 10 req/s，内置节流 8 req/s；XBRL 503 GAAP 指标 / 申报流 / 全市场筛选 |
| **美国财政部(Treasury)** | ⚠️ 代码在 skill，**未部署** | 收益率曲线 1M~30Y，无鉴权 |
| **CFTC** | ⚠️ 代码在 skill，**未部署** | COT 持仓报告 |
| **FINRA** | ⚠️ 代码在 skill，**未部署** | 全市场日度卖空成交量（12,112 标的），条款限制商用 |
| **CBOE** | ⚠️ 代码在 skill，**未部署** | 期权链 + 希腊字母 + 0DTE 流（需事先授权） |
| **Yahoo Finance** | ⚠️ 代码在 skill，**未部署** | crumb 鉴权自动管理，个人研究用；K线/quoteSummary |
| **Binance / OKX / CNBC / FRED / 生意社(100ppi)** | ❌ **全项目 0 接入** | 无任何代码；属用户期望但本站不存在的源 |

> 合规分级（Vibe 自述）：**S**=政府数据可商用（SEC/Treasury/CFTC）；**B**=发布文件商用前需核实（FINRA）；**C**=需授权或个人研究（CBOE/Nasdaq/Yahoo/东财/新浪/腾讯）。

### 3.3 代理 / 限流 / 容灾策略（可直接借鉴）

- **东财统一 `em_get`**：串行 ≥1s 间隔防封；`auto` 模式先直连（短超时不重试），失败再降级系统代理并固定，整进程复用探测结果。
- **push2/push2delay 双主机 latch**：实时不可达自动降级延迟行情，锁定可用主机。
- **缓存分层**：行情/榜单/指数 5 分钟共享缓存；日/季级数据（资金面/解禁等）30 分钟缓存；空结果不缓存（下次重试）。
- **合规红线**：所有接口按用户传入代码返回客观数据，不预置标的、不排名、不预测、不给买卖结论；打板原始池只聚合成计数/比率，不暴露个股名单（守"零标的"）。

---

## 4. NASDX 现有能力盘点（保留基线）

**数据层**：`quant/data.py` 腾讯 gtimg 优先 + 同花顺 mootdx(`ths_bridge`, `bestip=False`) + akshare 兜底；批量行情 `get_batch_ohlcv`(并发+磁盘缓存)；本机网络下东财不可达、腾讯 HTTP 为唯一稳定主力（见 CONTEXT 2026-08-13）。

**量化内核（NASDX 独有且更强）**：
- 回测 `backtest.py` + 防过拟合 `anti_overfit.py` + `position_advisor.py` + `rl_strategy.py` + `signal_engine.py` + `ml_model.py` + `confidence_trainer.py` + `vnpy_bridge.py`
- **决策记录与评估**：`decision_record` / `outcome_labels`(无前视标签) / `decision_evaluation`(校准/消融/对比) / `decision_wiring` —— 过拟合控制与"LLM 比规则更准吗"的可证伪框架
- **组合账本**：`portfolio_store`(事件溯源+整数手规则) / `portfolio_gate`(四态闸门) / `portfolio_link`
- **盘中 copilot**：`intraday_decision` + `intraday_copilot`（半小时快照、确定性动作策略、fail-closed）
- **证据层**：`evidence`(权威分/新鲜度/验证) / `announcement_sources`(巨潮) / `news_sources` / `external_review`
- **多智能体**：`analyzer` / `battle`(投票) / `research` / `investment_brief` / `decision.py`
- **桌面化**：`desktop/` Windows 打包、启动 bat、release check
- **质量门禁**：`run_final_audit`、secret scan、Dependabot/CodeQL/ruleset、contracts 测试（数百条）

---

## 5. 功能对齐方案（新增 / 复刻 / 保留 三分界）

### 5.1 复刻搬运（Vibe 有、NASDX 缺失或较弱 → 直接搬）

| # | 模块 | Vibe 实现参考 | NASDX 现状 | 搬运方式 |
|---|---|---|---|---|
| R1 | **资讯雷达** | `newsradar.py` + `news_sources.json`（12赛道/108 RSS，并发抓取+合规过滤） | 仅有 `news_sources`/`evidence` 雏形 | 移植 `newsradar` 模块 + 源清单，挂到现有 evidence 层或新页面 |
| R2 | **全球市场 / 美港股** | `gstock.py`（东财全球指数+美港股行情+财务） | A股专注，几乎无海外 | 新增 `nasdx/global_market.py`，复用东财 `em_get` 范式 |
| R3 | **每日复盘聚合** | `market.py`（情绪/板块资金/短线情绪/成交榜） | 有 `intraday_copilot` 但范围不同 | 新增 `nasdx/daily_review.py` 聚合层 + Streamlit 页 |
| R4 | **多空调论** | `debate.py`（事实底稿→多/空→中立主持） | `battle.py` 投票但未结构化多空 | 新增 `nasdx/debate.py`，复用现有 LLM 层 |
| R5 | **我的研报** | `myreports.py`（上传+按文件名归档，本地） | 无 | 新增本地研报库模块 |
| R6 | **研究记录/笔记 + 反思审计** | `notes` + `reflection.py` | 无专门 | 新增 notes 模块 + 反思审计端点 |
| R7 | **市场驾驶舱大屏 `/mrd/`** | React SPA + Node:3000 数据代理（指数/板块/资金/快讯/榜单/大宗/美债/产业链） | Streamlit，无大屏 | 评估：用现有 Streamlit 大屏页替代，或另起 React 大屏；**建议优先 Streamlit 复用以守住桌面化路线** |
| R8 | **估值历史分位** | `valuation_percentile`（百度 PE/PB 分位） | 无 | 新增，依赖 akshare `stock_zh_valuation_baidu` |
| R9 | **资金面直连东财全套** | `astock.py` 融资融券/大宗/股东户数/分红/资金流/龙虎榜/解禁/板块/热门概念/互动易 | 走 akshare 部分覆盖，东财直连更全更稳 | 优先复用已安装的 **`a-stock-data` skill**（本机已装），补直连东财函数 |
| R10 | **接入 AI 多模型** | `chat.py` API多模型/CLI订阅/MCP | 固定 DeepSeek | 评估扩展 `llm.py` 为多模型可插拔（中优先级） |

### 5.2 保留原有（NASDX 独有且更强 → 不动）

| # | 模块 | 说明 |
|---|---|---|
| K1 | 回测引擎 + 防过拟合 + 信号/RL/ML | Vibe 无回测；这是 NASDX 核心壁垒 |
| K2 | 决策记录/评估/前视防护框架 | 可证伪的"模型有效性"框架，Vibe 无 |
| K3 | 组合事件溯源账本 + 闸门 + 整数手规则 | NASDX 独有正确性保障 |
| K4 | 盘中 copilot（半小时快照+确定性动作） | NASDX 独有 |
| K5 | 证据层（权威分/新鲜度/验证） | NASDX 独有 |
| K6 | Windows 桌面化打包 | NASDX 路线核心，Vibe 是 Web 部署 |
| K7 | 多智能体投票 battle + 投资简报 | NASDX 已有 |
| K8 | ETF50 量化 / 选股扫描 | NASDX 已有 |
| K9 | 测试/安全/审计门禁（contracts/secret scan/CodeQL） | NASDX 质量基线，务必保留 |

### 5.3 新增（用户期望但项目完全无 → 需新建，非搬运）

| # | 源/模块 | 状态 | 建议 |
|---|---|---|---|
| N1 | **Binance / OKX 加密行情** | 项目 0 接入 | 新建 `nasdx/crypto.py`（公开行情 API，无 Key）；注意合规/风控面，仅做行情展示 |
| N2 | **CNBC 资讯** | 项目 0 接入 | Vibe 用华尔街见闻+RSS 替代；如需 CNBC 新建 RSS/抓取模块 |
| N3 | **FRED 宏观数据** | 项目 0 接入 | 新建 `nasdx/macro_fred.py`（FRED API，需免费 Key） |
| N4 | **生意社(100ppi) 大宗商品** | 项目 0 接入 | 新建 `nasdx/commodity_100ppi.py`（大宗商品价格） |
| N5 | **SEC/Treasury/CBOE/FINRA/Yahoo 完整海外源** | 代码在 Vibe skill 但未部署 | 可作为"新增参考源"从 `global-stock-data` 移植（注意各源合规分级 B/C 限制商用） |

> ⚠️ N1~N4 是**你点名但本站根本没有**的源。若确实要"对齐"这些，属于**从零新建**而非搬运，需单独排期与合规评估，不计入 Vibe 复刻范围。

---

## 6. 实施优先级与风险

**P0（先搬、低风险、与现有数据层同源）**
- R9 资金面直连东财全套（复用已装 `a-stock-data` skill，补 NASDX 数据广度）
- R2 全球市场/美港股（东财 `em_get` 范式，与现有腾讯源一致）
- R8 估值历史分位（akshare 即可）

**P1（中等工作量、补齐信息面）**
- R1 资讯雷达（12赛道/108 RSS 源清单可直接复用）
- R3 每日复盘聚合
- R5/R6 我的研报 + 研究笔记

**P2（AI 交互层、视需求）**
- R4 多空调论（复用现有 LLM 层）
- R10 多模型接入
- R7 驾驶舱大屏（建议 Streamlit 复用而非另起 React，守住桌面化）

**P3（新增源，独立排期）**
- N1~N5 加密货币/ CNBC / FRED / 生意社 / 完整海外源 —— 新建 + 合规评估

**关键风险**
1. **数据源预期偏差**：你列的 11 源中 5 个（Binance/OKX/CNBC/FRED/生意社）本站不存在，勿误判为"未复刻"。
2. **东财可达性**：NASDX 本机网络东财直连/代理均不可达（CONTEXT 2026-08-13），Vibe 的 `em_get` 直连优先范式在你环境可能同样失效——R2/R9 需先在你网络实测东财连通性，否则沿用"腾讯为唯一稳定主力"的现状。
3. **不破坏既有架构**：NASDX 的回测/决策/账本/证据/桌面化是壁垒，复刻 Vibe 模块时只增不删，新模块挂 `nasdx/` 子包，不改动 `quant/`、`decision_*`、`portfolio_*` 内核。
4. **合规红线**：搬运 Vibe 的"零标的/不推荐/不预测"原则，打板原始池只聚合成计数不暴露个股名单。

---

## 附录：已克隆源码位置（分析用，非项目文件）

```
NASDX/_reverse_miaoou/
├── stock-analysis-base/   # Vibe-Research：backend/(astock.py,gstock.py,market.py,newsradar.py,debate.py,app.py) + a-stock-data/ + global-stock-data/ + frontend/(React19,11页)
├── stock-web-system/      # 元基财报引擎：FastAPI+Celery+NotebookLM，docker-compose
└── stock-analysis-platform/  # 早期美股/A股报告平台（架构参照）
```

> 注：`_reverse_miaoou/` 为本次逆向的分析副本，未纳入 NASDX 版本控制；如不需要可整体删除。

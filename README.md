# NASDX — A股热门板块多智能体量化分析系统

> 基于 [FinGenius](https://github.com/HuaYaoAI/FinGenius) 架构设计  
> 无需付费 API，数据源 AkShare（免费），支持任意 OpenAI 兼容接口

---

## ✨ 功能特性

- 📊 **ETF50 全量扫描** — 50只主流ETF每日技术面评分排行，自动打开浏览器报告
- 📈 **60只个股扫描** — 10大热门板块龙头，均线/MACD/RSI/布林带综合评分
- 🤖 **多智能体分析** — 5个专家Agent（技术面/资金流/风险/板块/供应链瓶颈）+ Battle多空辩论
- 🧱 **LLM 结构化输出** — Agent 要求模型返回 JSON 信号、置信度、结论和关键依据，减少文本解析漂移
- 🧠 **规则深度报告** — 无 API Key 时自动生成同结构单票深度报告，仍进入组合路线和最终简报
- 🔁 **一键投研闭环** — 可选串联行情刷新、ETF/个股规则扫描、多智能体深度分析
- 🧩 **多行情源回退** — 东方财富不稳时自动切到腾讯历史 K 线，避免扫描覆盖率大面积掉线
- 🧭 **组合级投资路线** — 聚合扫描榜单和深度报告，输出 ETF 主线、个股卫星、观察/回避池
- 🧾 **最终投资简报** — 无 API Key 时也能生成方向、仓位、候选剧本、情景和风险边界
- 🔎 **候选证据核查** — 每个候选标记试错/观察/补报告/回避，并列出待人工复核项
- 🗓️ **盘前/盘中/盘后执行队列** — 把候选审计转成补报告、复核、观察和盘后刷新动作
- 🔗 **外部复核包** — 为每个候选列出公告、行情、交易所入口和必须通过的复核条件
- 💰 **资金仓位换算** — 输入临时总资金和已有仓位，换算剩余额度、候选上限和第一笔试错金额
- 🧭 **建议漂移追踪** — 对比本次和上次简报，标出新增、移除、状态变化和下次复盘重点
- 📈 **建议结果复盘** — 用最新行情和扫描验证候选信号是否延续、降级、仍待补证据或缺数据
- 📦 **复盘快照包** — 一键导出路线、简报、候选审计、执行队列、外部复核包和来源清单
- 🗄️ **SQLite 历史库** — 自动追加 `nasdx_history.db`，索引简报、报告、扫描和 ETF 池历史
- 🔮 **未来情景推演** — 按数据闸门、市场强弱、主题轮动生成后续加仓/降仓/观望规则
- 🧭 **行动计划** — 把多维信号转成方向、仓位区间、入场条件、止损/复核触发
- 🛡️ **风险画像与数据质量闸门** — 保守/均衡/进取三档仓位纪律，过期行情或低覆盖扫描会自动降仓/阻断执行
- ⏰ **工作日定时** — 早10:00 + 下午14:30 自动扫描，浏览器实时查看
- 🌐 **Streamlit 网页** — 输入股票代码一键分析，暗色专业UI

---

## 🏗️ 架构

```
NASDX
├── nasdx/                   # 核心包（多智能体框架）
│   ├── agents/              # 5个专家 Agent
│   │   ├── technical.py     # 技术面（MA/MACD/RSI/布林带）
│   │   ├── fund_flow.py     # 资金流向（主力/超大单）
│   │   ├── risk.py          # 风险评估（超买/背离）
│   │   ├── sector.py        # 板块轮动
│   │   ├── chokepoint.py    # Serenity供应链瓶颈/需求冲击/贝叶斯更新
│   │   └── synthesis.py     # 综合研判
│   ├── environments/
│   │   ├── research.py      # 研究环境（5 Agent 并发分析，可配置顺序回退）
│   │   └── battle.py        # 辩论环境（多空博弈 + 投票）
│   ├── llm.py               # LLM 客户端（支持 DeepSeek/Claude/Qwen）
│   ├── data_loader.py       # 数据加载与格式化
│   ├── market_sources.py    # A股/ETF K线多数据源回退与字段归一
│   ├── data_quality.py      # 行情数据新鲜度检查
│   ├── decision.py          # 投资决策层（方向/仓位/风险纪律/复核触发）
│   ├── portfolio.py         # 组合级投资路线（ETF主线/个股卫星/未来情景）
│   ├── investment_brief.py  # 最终投资简报（方向/候选剧本/风险边界）
│   ├── candidate_audit.py   # 候选证据核查（数据/深度报告/人工复核项）
│   ├── position_sizing.py   # 资金仓位换算（临时输入，不保存账户信息）
│   ├── recommendation_tracker.py # 建议漂移追踪（跨次简报变化）
│   ├── recommendation_review.py # 建议结果复盘（信号延续/降级）
│   ├── account_review.py    # 真实账户复盘（成交 CSV/盈亏/路线匹配）
│   ├── execution_queue.py   # 盘前/盘中/盘后执行队列
│   ├── external_review.py   # 外部复核包（公告/行情/交易所入口与通过条件）
│   ├── review_snapshot.py   # 复盘快照包导出（ZIP/manifest/CSV）
│   ├── history_store.py     # SQLite 历史库（报告/扫描/ETF池/简报索引）
│   ├── rule_based_analysis.py # 无API规则深度报告
│   ├── analyzer.py          # 主分析器（三阶段管道）
│   └── report.py            # HTML 报告生成
│
├── scan_etf50.py            # ETF50 全量扫描（纯规则，无需API）
├── scan_stocks_full.py      # 60只个股完整扫描
├── fetch_stock_data.py      # AkShare 数据抓取
├── run_analysis.py          # 单只股票多智能体分析
├── run_investment_workflow.py # 一键投研闭环（刷新/扫描/深度分析）
├── run_portfolio_plan.py    # 生成组合级投资路线
├── run_investment_brief.py  # 生成最终投资简报
├── run_position_sizing.py   # 按临时资金输入换算仓位金额
├── run_recommendation_tracker.py # 生成建议漂移追踪
├── run_recommendation_review.py # 生成建议结果复盘
├── run_account_review.py    # 导入成交 CSV 生成真实账户复盘
├── run_review_snapshot.py   # 导出复盘快照包
├── run_final_audit.py       # 最终版交付自检
├── app.py                   # Streamlit 网页入口
├── etf50_pool.json          # 50只ETF池配置
├── stocks.json              # 股票池配置（6板块30股+39ETF）
└── 启动网页.bat             # Windows 一键启动 Streamlit
```

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements_nasdx.txt
```

### 2. 获取数据（无需 API Key）

```bash
python fetch_stock_data.py
```

### 3. 运行扫描（无需 API Key）

```bash
# ETF50 技术面扫描（纯规则，秒出结果）
python scan_etf50.py

# 60只热门个股扫描
python scan_stocks_full.py
```

### 4. 深度分析（LLM 可选）

```bash
# 可选：设置 API Key（支持 DeepSeek / Claude / Qwen 任意 OpenAI 兼容接口）
export NASDX_API_KEY=sk-xxxx
export NASDX_BASE_URL=https://api.deepseek.com   # 可选
export NASDX_MODEL=deepseek-chat                  # 可选

# 分析单只股票：默认 auto，有 API/本地模型则用 LLM，否则自动用规则深度报告
python run_analysis.py 603501 --risk-profile balanced

# 强制无 API 规则版
python run_analysis.py 603501 --mode rules --risk-profile balanced

# 强制 LLM 版
python run_analysis.py 603501 --mode llm --risk-profile balanced

# 一键闭环：仅深度分析（默认，最快）
python run_investment_workflow.py 603501

# 一键闭环：先刷新行情和 ETF50 扫描，再深度分析
python run_investment_workflow.py 603501 --workflow quick --risk-profile balanced

# 一键闭环：刷新行情 + ETF50/个股双扫描 + 深度分析（较慢）
python run_investment_workflow.py 603501 --workflow full --rounds 1 --risk-profile conservative

# 只生成组合级投资路线（读取最新本地扫描和深度报告）
python run_portfolio_plan.py --risk-profile balanced

# 生成最终投资简报（读取最新路线并输出 Markdown/JSON）
python run_investment_brief.py --risk-profile balanced

# 临时资金仓位换算（不写入账户数据）
python run_position_sizing.py --capital 100000 --current-etf 10000 --current-stock 5000 --risk-profile balanced

# 生成建议漂移追踪（对比最新简报与上一份不同时间的简报）
python run_recommendation_tracker.py --print

# 生成建议结果复盘（用最新行情/扫描验证上一份建议）
python run_recommendation_review.py --print

# 真实账户复盘（从成交 CSV 计算已实现/浮动盈亏和路线匹配）
python run_account_review.py --ledger trades.csv --capital 100000 --print

# 导出复盘快照包（ZIP，含简报/路线/候选审计/执行队列/外部复核包）
python run_review_snapshot.py --risk-profile balanced

# 最终版交付自检（语法、安全、路线契约、SQLite历史库、网页入口、文档覆盖）
python run_final_audit.py

# 产品化巡检聚合入口（单测 + 最终审计）
python run_product_readiness.py

# 若当前 shell 已设置 NASDX_API_KEY，可追加一次 LLM smoke 验证
python run_product_readiness.py --llm-smoke

# 启动网页界面
双击 启动网页.bat
# 或: streamlit run app.py
```

| 工作流 | 做什么 | 适合场景 |
|---|---|---|
| `analysis-only` | 直接使用最新本地行情做 5 Agent 深度分析 | 已有新数据，只想看行动计划 |
| `quick` | 刷新行情、跑 ETF50 扫描、再深度分析 | 想先看大盘/ETF 强弱，再看单票 |
| `full` | 刷新行情、跑 ETF50 和 60只个股扫描、再深度分析 | 做完整复盘或盘后筛选 |

`run_investment_workflow.py` 默认会在结束时生成 `reports/portfolio_plan_latest.md/json` 和 `reports/investment_brief_latest.md/json`，网页端“投资路线”页会直接读取这两份产物。`--analysis-mode auto|rules|llm` 可控制深度分析通道；无 API Key 时推荐保持 `auto`。

研究阶段默认使用 `ThreadPoolExecutor` 并发运行技术面、资金流、风险、板块、供应链瓶颈 5 个 Agent，以减少 LLM 等待时间；如需调试或限流，可设置 `NASDX_RESEARCH_MAX_WORKERS=1` 退回顺序执行。Streamlit 入口、行情抓取和量化模块导入时都不改写全局 `requests.get`，HTTP 连接使用 `requests` 原生环境代理和数据源回退。

Streamlit 页面里的 API Key / Base URL / 模型名只保存在当前会话，并通过子进程环境传给深度分析任务，不写回全局 `os.environ` 或 `nasdx.llm` 单例。后台分析和 ETF 扫描用 `task_id` 追踪，线程对象只保存在进程内任务表，`session_state` 只保存可序列化状态；分析日志文件名包含 `task_id`，避免同一股票并发任务互相覆盖。

LLM Agent 会在提示词末尾追加统一 JSON 契约，优先读取 `signal`、`confidence`、`conclusion`、`key_points` 字段；若模型未返回合法 JSON，才回退到旧的 `【信号】/【置信度】` 文本解析。最终审计会检查这条结构化输出链路。

保存报告、最终简报、建议复盘、账户复盘和扫描结果时，会同步追加到本地 `nasdx_history.db`。它只做历史索引，不替代 `reports/` 下的 Markdown/JSON；如需改位置，可设置 `NASDX_HISTORY_DB`。

### 子代理协作

项目内置 5 个 `.claude/agents` 子代理模板：上游方案拆解、单功能实现、契约审计、Streamlit 验证、交付收口。协作方式见 `docs/SUBAGENT_WORKFLOW.md`。默认规则是：审计和验证代理只读，单功能实现代理只改授权文件，API Key 只从环境变量读取且不写入文件。

组合路线包含：

- 仓位框架：总仓位上限、ETF预算、个股预算、现金缓冲。
- 候选分层：ETF主线、个股卫星、观察名单、回避/减仓池。
- 未来情景：数据恢复、强势延续、信号转弱，或顺势偏多、结构轮动、防守下行。
- 执行规则：何时刷新、何时只观察、何时试错、何时降仓。
- 深度报告：过期报告只作为重跑提醒，不参与当前候选排序。
- 规则深度报告：无 API Key 时仍生成 `report_CODE_DATE.json/html`，用于候选升级、仓位纪律和最终简报。
- 扫描覆盖率：若个股或 ETF 榜单有效数据低于池子 50%，该榜单不参与加仓候选，组合路线自动进入 `position_cap`。
- 最终简报：把路线压缩为方向、仓位动作、ETF/个股候选剧本、未来情景和风险控制，适合盘后复盘。
- 候选证据核查：对每个前排候选列出数据闸门、扫描排序、深度报告、风险红灯、公告/成交人工复核和仓位纪律；缺报告候选只能观察或补报告，不能直接进入试错。
- 资金仓位换算：网页端“投资路线”页或 `run_position_sizing.py` 可把总仓位、ETF/个股预算、单票上限换算成金额；账户资金只做本次计算，不保存到文件。
- 建议漂移追踪：网页端“投资路线”页或 `run_recommendation_tracker.py` 会对比最新简报和上一份不同时间简报，标出新增/移除/状态变化和下次复盘重点。
- 建议结果复盘：网页端“投资路线”页或 `run_recommendation_review.py` 会用最新行情、ETF/个股扫描和当前简报状态复盘上一份建议，输出信号延续、降级复核、仍待补证据或缺当前数据。
- 真实账户复盘：网页端“投资路线”页或 `run_account_review.py` 可导入成交 CSV，计算已实现盈亏、浮动盈亏、当前仓位和路线匹配；没有账户流水时不会计算真实收益。
- 执行队列：把候选审计拆成盘前补报告、盘中人工复核/观察、盘后刷新简报三类动作；任何阻断项存在时都不会写成试错许可。
- 外部复核包：为每个候选给出巨潮资讯、行情页和交易所入口，并列明公告/财报、成交/折溢价、账户仓位等必须人工确认的通过条件；链接存在不代表复核已通过。
- 复盘快照包：网页端“导出复盘包”或 `run_review_snapshot.py` 会输出 ZIP，内含 latest Markdown/JSON、建议漂移追踪、建议结果复盘、manifest、候选审计 CSV、执行队列 CSV、外部复核 CSV 和来源文件哈希。
- SQLite 历史库：`nasdx.history_store` 追加保存单股报告、每日扫描、ETF 池、组合路线、最终简报和复盘产物，默认文件为 `nasdx_history.db`。

---

## 📊 监控池

### 股票（6板块 × 5只）

| 板块 | 代表标的 |
|---|---|
| 半导体 | 中芯国际、韦尔股份、兆易创新、华虹半导体、北京君正 |
| 半导体设备 | 北方华创、中微公司、华海清科、芯源微、盛美上海 |
| 通信 | 中兴通讯、中际旭创、烽火通信、华工科技、新易盛 |
| 电力 | 长江电力、国电南瑞、三峡能源、中国核电、特变电工 |
| AI算力 | 寒武纪、海光信息、科大讯飞、中科曙光、海康威视 |
| 军工 | 中航西飞、航发动力、中航光电、紫光国微、振华科技 |

### ETF池（50只）

涵盖：半导体芯片、科创板、通信5G、电力电网、AI算力、军工、海外纳指、港股科技、红利低波、机器人等主题

---

## 🤖 多智能体架构

```
用户输入股票代码
        ↓
  ┌─────────────────────────────────┐
  │  Phase 1: Research 研究阶段     │
  │   技术面Agent → 资金流Agent     │
  │   风险Agent  → 板块Agent        │
  │   供应链瓶颈Agent               │
  ├─────────────────────────────────┤
  │  Phase 2: Battle 辩论阶段       │
  │   多头辩手 ←→ 空头辩手          │
  │   裁判综合 → 5位投票者          │
  ├─────────────────────────────────┤
  │  Phase 3: Synthesis 综合研判    │
  │   行动计划 + 仓位纪律 + 风险复核│
  └─────────────────────────────────┘
        ↓
  HTML / JSON 报告（自动打开浏览器）
```

---

## ⚙️ LLM 配置

支持任何 OpenAI 兼容接口：

| 服务 | base_url | 推荐模型 |
|---|---|---|
| DeepSeek（推荐） | `https://api.deepseek.com` | `deepseek-chat` |
| 阿里通义 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| Ollama 本地 | `http://localhost:11434/v1` | `qwen2.5:14b` |

---

## ⚠️ 免责声明

本项目仅供学习研究，所有分析结果为技术规则计算或 AI 推演，**不构成任何投资建议**。股市有风险，投资需谨慎。

---

## 📄 License

MIT License — 自由使用，欢迎 Star ⭐ 和 PR

---

*基于 [FinGenius](https://github.com/HuaYaoAI/FinGenius) 开源架构 · 数据来自 [AkShare](https://github.com/akfamily/akshare)*

## 参考来源

- Serenity Chokepoint Investing Skill：用于新增供应链瓶颈、需求冲击、贝叶斯更新研究维度，项目内只作为研究框架，不作为投资建议。

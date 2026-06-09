# NASDX — A股热门板块多智能体量化分析系统

> 基于 [FinGenius](https://github.com/HuaYaoAI/FinGenius) 架构设计  
> 无需付费 API，数据源 AkShare（免费），支持任意 OpenAI 兼容接口

---

## ✨ 功能特性

- 📊 **ETF50 全量扫描** — 50只主流ETF每日技术面评分排行，自动打开浏览器报告
- 📈 **60只个股扫描** — 10大热门板块龙头，均线/MACD/RSI/布林带综合评分
- 🤖 **多智能体分析** — 5个专家Agent（技术面/资金流/风险/板块/供应链瓶颈）+ Battle多空辩论
- 🔁 **一键投研闭环** — 可选串联行情刷新、ETF/个股规则扫描、多智能体深度分析
- 🧭 **组合级投资路线** — 聚合扫描榜单和深度报告，输出 ETF 主线、个股卫星、观察/回避池
- 🔮 **未来情景推演** — 按数据闸门、市场强弱、主题轮动生成后续加仓/降仓/观望规则
- 🧭 **行动计划** — 把多维信号转成方向、仓位区间、入场条件、止损/复核触发
- 🛡️ **风险画像与数据新鲜度** — 保守/均衡/进取三档仓位纪律，过期行情自动降仓或阻断执行
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
│   │   ├── research.py      # 研究环境（5 Agent 顺序分析）
│   │   └── battle.py        # 辩论环境（多空博弈 + 投票）
│   ├── llm.py               # LLM 客户端（支持 DeepSeek/Claude/Qwen）
│   ├── data_loader.py       # 数据加载与格式化
│   ├── data_quality.py      # 行情数据新鲜度检查
│   ├── decision.py          # 投资决策层（方向/仓位/风险纪律/复核触发）
│   ├── portfolio.py         # 组合级投资路线（ETF主线/个股卫星/未来情景）
│   ├── analyzer.py          # 主分析器（三阶段管道）
│   └── report.py            # HTML 报告生成
│
├── scan_etf50.py            # ETF50 全量扫描（纯规则，无需API）
├── scan_stocks_full.py      # 60只个股完整扫描
├── fetch_stock_data.py      # AkShare 数据抓取
├── run_analysis.py          # 单只股票多智能体分析
├── run_investment_workflow.py # 一键投研闭环（刷新/扫描/深度分析）
├── run_portfolio_plan.py    # 生成组合级投资路线
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
pip install akshare pandas openai streamlit pydantic
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

### 4. 多智能体深度分析（需要 LLM API Key）

```bash
# 设置 API Key（支持 DeepSeek / Claude / Qwen 任意 OpenAI 兼容接口）
export NASDX_API_KEY=sk-xxxx
export NASDX_BASE_URL=https://api.deepseek.com   # 可选
export NASDX_MODEL=deepseek-chat                  # 可选

# 分析单只股票
python run_analysis.py 603501 --risk-profile balanced

# 一键闭环：仅深度分析（默认，最快）
python run_investment_workflow.py 603501

# 一键闭环：先刷新行情和 ETF50 扫描，再深度分析
python run_investment_workflow.py 603501 --workflow quick --risk-profile balanced

# 一键闭环：刷新行情 + ETF50/个股双扫描 + 深度分析（较慢）
python run_investment_workflow.py 603501 --workflow full --rounds 1 --risk-profile conservative

# 只生成组合级投资路线（读取最新本地扫描和深度报告）
python run_portfolio_plan.py --risk-profile balanced

# 最终版交付自检（语法、安全、路线契约、网页入口、文档覆盖）
python run_final_audit.py

# 启动网页界面
双击 启动网页.bat
# 或: streamlit run app.py
```

| 工作流 | 做什么 | 适合场景 |
|---|---|---|
| `analysis-only` | 直接使用最新本地行情做 5 Agent 深度分析 | 已有新数据，只想看行动计划 |
| `quick` | 刷新行情、跑 ETF50 扫描、再深度分析 | 想先看大盘/ETF 强弱，再看单票 |
| `full` | 刷新行情、跑 ETF50 和 60只个股扫描、再深度分析 | 做完整复盘或盘后筛选 |

`run_investment_workflow.py` 默认会在结束时生成 `reports/portfolio_plan_latest.md/json`，网页端“投资路线”页会直接读取这份组合路线。

组合路线包含：

- 仓位框架：总仓位上限、ETF预算、个股预算、现金缓冲。
- 候选分层：ETF主线、个股卫星、观察名单、回避/减仓池。
- 未来情景：数据恢复、强势延续、信号转弱，或顺势偏多、结构轮动、防守下行。
- 执行规则：何时刷新、何时只观察、何时试错、何时降仓。
- 深度报告：过期报告只作为重跑提醒，不参与当前候选排序。

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

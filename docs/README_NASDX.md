# NASDX — A股多智能体分析框架

> 基于 FinGenius 架构设计，针对 NASDX 6大板块股票池定制
> 无需复杂配置，接入任意 OpenAI 兼容 API 即可运行

---

## 架构总览

```
用户 → analyze.py
         │
         ▼
   NasdxAnalyzer
    ┌────────────────────────────────────────┐
    │                                        │
    │  Phase 1: ResearchEnvironment          │
    │   ├─ TechnicalAgent   (MA/MACD/RSI)    │
    │   ├─ FundFlowAgent    (主力/超大单)     │
    │   ├─ RiskAgent        (超买/背离/波动)  │
    │   └─ SectorAgent      (板块轮动)        │
    │                                        │
    │  Phase 2: BattleEnvironment            │
    │   ├─ 多头辩手 (看多论点)               │
    │   ├─ 空头辩手 (看空论点)               │
    │   ├─ 裁判综合                          │
    │   └─ 5位投票者                         │
    │                                        │
    │  Phase 3: SynthesisAgent               │
    │   └─ 综合研判 + 操作建议               │
    │                                        │
    └────────────────────────────────────────┘
         │
         ▼
    HTML / JSON 报告
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements_nasdx.txt
```

### 2. 配置 API Key

```bash
# 方式一：环境变量（推荐）
export NASDX_API_KEY=sk-xxxx
export NASDX_BASE_URL=https://api.deepseek.com   # 可选，默认 DeepSeek
export NASDX_MODEL=deepseek-chat                  # 可选

# 方式二：命令行参数
python scripts/analyze.py 603501 --api-key sk-xxxx
```

### 3. 获取数据

```bash
python scripts/fetch_stock_data.py
# 输出：stock_data_YYYYMMDD.json
```

### 4. 运行分析

```bash
# 分析单只股票
python scripts/analyze.py 603501

# 3轮辩论（更充分）
python scripts/analyze.py 603501 --rounds 3

# JSON 格式输出
python scripts/analyze.py 603501 --format json

# 批量分析多只
python scripts/analyze.py --batch 603501 000063 600900 002371

# 分析全部6板块42只标的
python scripts/analyze.py --all-sectors
```

## 支持的 LLM

| 服务 | base_url | 推荐模型 |
|---|---|---|
| DeepSeek（推荐） | `https://api.deepseek.com` | `deepseek-chat` |
| 阿里云通义 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Ollama（本地） | `http://localhost:11434/v1` | `qwen2.5:14b` |

## 文件结构

```
NASDX/
├── nasdx/                      # 核心包
│   ├── schema.py               # 数据结构（Message/AnalysisResult/FinalReport）
│   ├── llm.py                  # LLM 客户端
│   ├── data_loader.py          # 数据加载 + 格式化
│   ├── analyzer.py             # 主分析器
│   ├── report.py               # HTML 报告生成
│   ├── agents/
│   │   ├── base.py             # Agent 基类
│   │   ├── technical.py        # 技术面 Agent
│   │   ├── fund_flow.py        # 资金流向 Agent
│   │   ├── risk.py             # 风险 Agent
│   │   ├── sector.py           # 板块 Agent
│   │   └── synthesis.py        # 综合 Agent
│   └── environments/
│       ├── research.py         # 研究环境
│       └── battle.py           # 辩论环境
├── analyze.py                  # 命令行入口
├── fetch_stock_data.py         # 数据抓取（原有）
├── config.example.toml         # 配置示例
└── reports/                    # 报告输出目录（自动创建）
```

## 与 FinGenius 的对比

| 维度 | FinGenius | NASDX |
|---|---|---|
| 数据源 | efinance + 网络搜索 | AkShare（已抓取JSON）|
| Agent数 | 6个 | 4个研究 + 1综合 |
| 辩论 | ✅ 多轮 | ✅ 多轮 |
| API 依赖 | GPT-4o（较贵）| 任意兼容接口（DeepSeek最省） |
| 安装复杂度 | MCP + uvicorn | 仅 openai + pydantic |
| 股票池 | 任意股票 | 6板块42只专注池 |

## 注意事项

- 688xxx（科创板）股票无资金流向数据，FundFlowAgent 会自动跳过
- ETF 同样无资金流向，仅分析技术面和板块面
- 每只股票分析约需 1-3 分钟（取决于 API 速度和辩论轮数）
- 报告仅供学习研究，不构成投资建议

---

*NASDX · 基于 FinGenius 开源架构 · 数据来自 AkShare*

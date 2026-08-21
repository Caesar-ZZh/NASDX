# FinGenius 仓库完整结构与代码清单

**Repository**: https://github.com/HuaYaoAI/FinGenius  
**主分支**: main  
**抓取时间**: 2026-06-01

---

## 项目概述

FinGenius 是一个基于多agent的金融股票分析框架，包含以下核心特性：
- 研究阶段（Research Phase）：多个专家agent并行分析股票
- 辩论阶段（Battle Phase）：agent之间进行结构化辩论与投票
- 可视化输出：美观的控制台输出和HTML报告生成
- MCP集成：支持Model Context Protocol工具调用

---

## 完整目录结构

```
FinGenius/
├── main.py                          # 入口文件
├── requirements.txt                 # 依赖列表
├── LICENSE                          # GPL v3
├── config/
│   ├── config.example.toml
│   └── config.toml                  # 用户配置（API密钥等）
├── docs/
│   ├── logo.png
│   ├── architecture.png
│   ├── boyi.png
│   └── wechat.JPG
├── report/                          # 输出报告目录
└── src/
    ├── __init__.py
    ├── agent/                       # Agent定义
    │   ├── __init__.py
    │   ├── base.py                  # BaseAgent 抽象基类
    │   ├── react.py                 # ReActAgent
    │   ├── mcp.py                   # MCPAgent
    │   ├── sentiment_analysis.py     # SentimentAgent
    │   ├── risk_control.py           # RiskControlAgent
    │   ├── hot_money.py              # HotMoneyAgent (龙虎榜)
    │   ├── technical_analysis.py     # TechnicalAnalysisAgent
    │   ├── chip_analysis.py          # ChipAnalysisAgent
    │   ├── big_deal_analysis.py      # BigDealAnalysisAgent
    │   └── report.py                 # ReportAgent
    │
    ├── environment/                 # 环境框架
    │   ├── __init__.py
    │   ├── base.py                  # BaseEnvironment & EnvironmentFactory
    │   ├── research.py              # ResearchEnvironment
    │   └── battle.py                # BattleEnvironment (多轮辩论框架)
    │
    ├── tool/                        # 工具与任务执行
    │   ├── __init__.py
    │   ├── base.py                  # BaseTool & ToolResult
    │   ├── battle.py                # BattleTool
    │   ├── sentiment_analysis.py     # SentimentAnalysisTool
    │   ├── risk_control.py           # RiskControlTool
    │   ├── hot_money.py              # HotMoneyTool
    │   ├── technical_analysis.py     # TechnicalAnalysisTool
    │   ├── chip_analysis.py          # ChipAnalysisTool (筹码分析)
    │   ├── big_deal_analysis.py      # BigDealAnalysisTool (大单分析)
    │   ├── create_chat_completion.py # OpenAI ChatCompletion 调用
    │   ├── create_html.py            # HTML报告生成
    │   ├── tts_tool.py               # 文本转语音
    │   └── fetch_*.py                # 各种数据获取工具
    │
    ├── prompt/                      # 提示词模板
    │   ├── __init__.py
    │   ├── battle.py                 # 辩论相关提示词
    │   ├── sentiment_analysis.py
    │   ├── risk_control.py
    │   ├── hot_money.py
    │   ├── technical_analysis.py
    │   ├── chip_analysis.py
    │   └── big_deal_analysis.py
    │
    ├── mcp/                         # MCP服务器定义
    │   ├── __init__.py
    │   ├── battle_server.py
    │   ├── big_deal_analysis_server.py
    │   ├── chip_analysis_server.py
    │   ├── hot_money_server.py
    │   ├── sentiment_analysis_server.py
    │   ├── technical_analysis_server.py
    │   └── risk_control_server.py
    │
    ├── utils/                       # 工具函数库
    │   ├── cleanup_reports.py        # 报告清理工具
    │   └── report_manager.py         # 报告管理与保存
    │
    ├── config.py                    # 配置管理 (11,245 bytes)
    ├── console.py                   # 可视化输出 (21,159 bytes)
    ├── exceptions.py                # 自定义异常
    ├── llm.py                       # LLM接口与Token计数 (29,328 bytes)
    ├── logger.py                    # 日志配置
    ├── ollama_client.py             # Ollama集成 (11,583 bytes)
    ├── schema.py                    # Pydantic数据模型 (6,026 bytes)
    └── __init__.py                  # 包初始化
```

---

## 核心模块代码清单

### 1. **main.py** - 入口与主分析流程

**关键类**: `EnhancedFinGeniusAnalyzer`

**主要方法**:
- `analyze_stock(stock_code, max_steps=3, debate_rounds=2)` — 完整分析流程
- `_run_research_phase()` — 研究阶段（6个专家并行分析）
- `_run_battle_phase()` — 辩论阶段（专家多轮投票）
- `_generate_reports()` — 生成HTML报告与JSON数据
- `_prepare_final_results()` — 汇总最终结果

**支持的Agent**:
- sentiment_agent
- risk_control_agent
- hot_money_agent
- technical_analysis_agent
- chip_analysis_agent
- big_deal_analysis_agent

**输出格式**:
- HTML报告 (`report/report_{stock_code}_{timestamp}.html`)
- JSON辩论记录 (`debate_data`)
- JSON投票结果 (`vote_data`)
- MP3语音播报 (可选)

---

### 2. **src/schema.py** - 数据模型

```python
# 枚举
class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

class ToolChoice(str, Enum):
    NONE = "none"
    AUTO = "auto"
    REQUIRED = "required"

class AgentState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    ERROR = "ERROR"

# 主数据类
class Message(BaseModel):
    role: ROLE_TYPE
    content: Optional[str]
    tool_calls: Optional[List[Any]]
    name: Optional[str]
    tool_call_id: Optional[str]
    base64_image: Optional[str]

class Memory(BaseModel):
    messages: List[Message] = []
    max_messages: int = 100
```

---

### 3. **src/agent/base.py** - Agent基类

```python
class BaseAgent(BaseModel, ABC):
    name: str
    description: Optional[str]
    system_prompt: Optional[str]
    next_step_prompt: Optional[str]
    llm: LLM
    memory: Memory
    state: AgentState = AgentState.IDLE
    max_steps: int = 10
    current_step: int = 0
    
    # 核心方法
    async def run(request: Optional[str]) -> str
    @abstractmethod
    async def step() -> str
    
    def update_memory(role, content, base64_image)
    def is_stuck() -> bool
    def handle_stuck_state()
```

---

### 4. **src/environment/base.py** - 环境框架

```python
class BaseEnvironment(BaseModel):
    name: str
    agents: Dict[str, BaseAgent] = {}
    max_steps: int = 3
    
    @classmethod
    async def create(cls, **kwargs) -> "BaseEnvironment"
    
    def register_agent(agent: BaseAgent)
    async def run(**kwargs) -> Dict[str, Any]

class EnvironmentFactory:
    @staticmethod
    async def create_environment(
        environment_type: EnvironmentType,  # RESEARCH | BATTLE
        agents: Union[BaseAgent, List[BaseAgent], Dict[str, BaseAgent]],
        **kwargs
    ) -> BaseEnvironment
```

---

### 5. **src/tool/base.py** - 工具基础

```python
class BaseTool(ABC, BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]
    
    @abstractmethod
    async def execute(**kwargs) -> ToolResult

class ToolResult(BaseModel):
    output: Optional[str]
    error: Optional[str]
    base64_image: Optional[str]
    system: Optional[str]

def get_recent_trading_day(date_format) -> str
    # 返回最近的交易日（周一-周五）
```

---

### 6. **src/llm.py** - LLM 接口与Token计数

**关键类**: `LLM`（单例模式）

**Token计数**:
```python
class TokenCounter:
    BASE_MESSAGE_TOKENS = 4
    LOW_DETAIL_IMAGE_TOKENS = 85
    HIGH_DETAIL_TILE_TOKENS = 170
    
    def count_text(text: str) -> int
    def count_image(image_item: dict) -> int
    def count_message_tokens(messages: List[dict]) -> int
```

**主要方法**:
```python
async def ask(messages, system_msgs, stream=True, temperature) -> str
async def ask_with_images(messages, images, system_msgs, stream, temperature) -> str
async def ask_tool(messages, system_msgs, tools, tool_choice, temperature, **kwargs)
```

**支持的API类型**:
- OpenAI API
- Azure OpenAI
- Ollama (本地模型)

**推理模型检测**:
```python
REASONING_MODELS = ["o1", "o3-mini", "deepseek-r1"]
# 自动为推理模型使用 max_completion_tokens 而非 max_tokens
```

---

## src/agent/ 目录文件列表

| 文件 | 大小 | 功能 |
|------|------|------|
| `__init__.py` | 361 B | 包导出 |
| `base.py` | 7,298 B | BaseAgent抽象基类 |
| `big_deal_analysis.py` | 1,616 B | 大单异动分析agent |
| `chip_analysis.py` | 2,314 B | 筹码分析agent |
| `hot_money.py` | 2,116 B | 龙虎榜游资agent |
| `mcp.py` | 10,738 B | MCP协议agent |
| `react.py` | - | ReAct思维agent |
| `report.py` | - | 报告生成agent |
| `risk_control.py` | - | 风险控制agent |
| `sentiment_analysis.py` | - | 情感分析agent |
| `technical_analysis.py` | - | 技术面分析agent |

---

## src/environment/ 目录文件列表

| 文件 | 大小 | 功能 |
|------|------|------|
| `__init__.py` | 299 B | 包导出 |
| `base.py` | 3,788 B | BaseEnvironment & Factory |
| `battle.py` | 31,001 B | 多轮辩论环境 |
| `research.py` | 6,799 B | 研究阶段环境 |

---

## src/tool/ 目录文件列表

| 文件 | 大小 | 功能 |
|------|------|------|
| `__init__.py` | 546 B | 包导出 |
| `base.py` | 3,229 B | BaseTool & ToolResult |
| `battle.py` | 2,785 B | 辩论工具 |
| `big_deal_analysis.py` | 8,099 B | 大单分析工具 |
| `chip_analysis.py` | 37,702 B | 筹码分析工具（最大） |
| `create_chat_completion.py` | 5,621 B | OpenAI接口 |
| `create_html.py` | - | HTML报告生成 |
| `hot_money.py` | - | 游资分析 |
| `risk_control.py` | - | 风险控制 |
| `sentiment_analysis.py` | - | 情感分析 |
| `technical_analysis.py` | - | 技术指标 |
| `tts_tool.py` | - | 文本转语音 |
| `fetch_*.py` | - | 数据获取工具 |

---

## src/ 直接文件列表

| 文件 | 大小 | 功能 |
|------|------|------|
| `__init__.py` | 270 B | 包初始化 |
| `config.py` | 11,245 B | 配置管理 |
| `console.py` | 21,159 B | 富文本可视化输出 |
| `exceptions.py` | 242 B | 自定义异常 |
| `llm.py` | 29,328 B | LLM接口 |
| `logger.py` | 1,466 B | 日志配置 |
| `ollama_client.py` | 11,583 B | Ollama客户端 |
| `schema.py` | 6,026 B | Pydantic模型 |

---

## src/utils/ 文件列表

| 文件 | 大小 | 功能 |
|------|------|------|
| `cleanup_reports.py` | 6,897 B | 报告文件清理 |
| `report_manager.py` | 12,431 B | 报告保存与管理 |

---

## src/prompt/ 文件列表

| 文件 | 功能 |
|------|------|
| `__init__.py` | 空 |
| `battle.py` | 辩论提示词 |
| `big_deal_analysis.py` | 大单分析提示 |
| `chip_analysis.py` | 筹码分析提示 |
| `hot_money.py` | 龙虎榜提示 |
| `risk_control.py` | 风险控制提示 |
| `sentiment_analysis.py` | 情感分析提示 |
| `technical_analysis.py` | 技术分析提示 |

---

## src/mcp/ 文件列表

| 文件 | 功能 |
|------|------|
| `__init__.py` | 空 |
| `battle_server.py` | MCP辩论服务 |
| `big_deal_analysis_server.py` | MCP大单分析服务 |
| `chip_analysis_server.py` | MCP筹码分析服务 |
| `hot_money_server.py` | MCP游资分析服务 |
| `sentiment_analysis_server.py` | MCP情感分析服务 |
| `technical_analysis_server.py` | MCP技术分析服务 |
| `risk_control_server.py` | MCP风险控制服务 |

---

## 命令行使用

```bash
# 基础用法
python main.py 000001.SZ

# 指定输出格式
python main.py 000001.SZ --format json --output results.json

# 启用语音播报
python main.py 000001.SZ --tts

# 自定义分析步数和辩论轮数
python main.py 000001.SZ --max-steps 5 --debate-rounds 3
```

**参数说明**:
- `stock_code` — 股票代码（必需）
- `-f/--format` — 输出格式 (text|json), 默认text
- `-o/--output` — 保存结果到文件
- `--tts` — 启用文本转语音
- `--max-steps` — 每个agent最大步数，默认3
- `--debate-rounds` — 辩论轮数，默认2

---

## 关键设计模式

1. **单例模式**: `LLM` 类用单例确保全局唯一
2. **工厂模式**: `EnvironmentFactory` 动态创建环境实例
3. **策略模式**: 多个agent实现不同分析策略
4. **观察者模式**: 环境监听agent状态变化
5. **装饰器模式**: `state_context` 上下文管理器

---

## 数据流向

```
main.py
├── ResearchEnvironment
│   ├── sentiment_agent
│   ├── risk_control_agent
│   ├── hot_money_agent
│   ├── technical_analysis_agent
│   ├── chip_analysis_agent
│   └── big_deal_analysis_agent
│       ↓ (并行执行，3秒间隔)
│   研究结果 → research_results
│
├── BattleEnvironment
│   ├── 同上6个agent
│   ├── 多轮辩论 (debate_rounds=2)
│   ├── 投票机制
│   └── 结果汇总
│       ↓
│   battle_results (final_decision, vote_count, debate_history)
│
└── ReportAgent
    ├── 生成HTML报告
    ├── 保存JSON数据
    └── 可选TTS播报
```

---

## 文件总数统计

- **Python源文件**: 35+ 个
- **目录**: 8 个（agent, environment, tool, prompt, mcp, utils等）
- **总代码行数**: ~3000+ 行
- **最大文件**: `src/tool/chip_analysis.py` (37,702 B)
- **最复杂模块**: `src/environment/battle.py` (31,001 B)

---

## 依赖包（推测）

基于导入语句：
- `pydantic` — 数据验证
- `openai` — OpenAI API
- `tenacity` — 重试机制
- `tiktoken` — Token计数
- `rich` — 彩色输出
- `ollama` — 本地模型支持
- 其他金融数据库（如tushare）

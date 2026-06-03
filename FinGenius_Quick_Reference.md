# FinGenius 快速参考卡片

## 项目信息
| 项目 | 详情 |
|------|------|
| **名称** | FinGenius |
| **地址** | https://github.com/HuaYaoAI/FinGenius |
| **分支** | main |
| **语言** | Python 3.7+ |
| **框架** | Pydantic + AsyncIO + OpenAI API |
| **许可** | GPL v3 |

---

## 核心概念（5分钟理解）

### 三阶段流程
```
输入股票代码
    ↓
[研究阶段] 6个专家并行分析
    ↓
[辩论阶段] 多轮投票辩论
    ↓
[报告生成] HTML + JSON输出
    ↓
专家共识 + 最终结论
```

### 6个分析专家
1. **SentimentAgent** - 情感分析（市场情绪）
2. **RiskControlAgent** - 风险控制（风险评估）
3. **HotMoneyAgent** - 龙虎榜分析（资金面）
4. **TechnicalAnalysisAgent** - 技术面（K线指标）
5. **ChipAnalysisAgent** - 筹码分析（持仓分布）
6. **BigDealAnalysisAgent** - 大单异动（异常交易）

---

## 快速开始

### 安装
```bash
git clone https://github.com/HuaYaoAI/FinGenius.git
cd FinGenius
pip install -r requirements.txt
```

### 配置 (config/config.toml)
```toml
[llm.default]
api_type = "openai"  # or "azure" or "ollama"
model = "gpt-4"
api_key = "your-api-key"
base_url = "https://api.openai.com/v1"
max_tokens = 4096
temperature = 0.7
```

### 运行分析
```bash
# 基础
python main.py 000001.SZ

# 完整选项
python main.py 000001.SZ \
  --format json \
  --output result.json \
  --max-steps 5 \
  --debate-rounds 3 \
  --tts
```

### 参数说明
| 参数 | 说明 | 默认值 |
|------|------|--------|
| `stock_code` | 股票代码（必需） | - |
| `-f/--format` | 输出格式 (text/json) | text |
| `-o/--output` | 保存到文件 | 无 |
| `--max-steps` | 每agent最大步数 | 3 |
| `--debate-rounds` | 辩论轮数 | 2 |
| `--tts` | 启用语音播报 | 否 |

---

## 关键类速查表

### BaseAgent (agent/base.py)
```python
class BaseAgent:
    name: str                      # agent名称
    system_prompt: str             # 系统指令
    llm: LLM                       # LLM实例
    memory: Memory                 # 消息存储
    state: AgentState              # IDLE/RUNNING/FINISHED/ERROR
    max_steps: int                 # 最大执行步数
    
    async def run(request) → str   # 主执行方法
    @abstractmethod
    async def step() → str         # 单步逻辑（子类实现）
    def update_memory(role, content)  # 保存消息
    def is_stuck() → bool          # 检测重复响应
```

### LLM (src/llm.py)
```python
class LLM:  # 单例
    model: str                     # 模型名
    max_tokens: int               # 最大生成token
    api_type: str                 # "openai" | "azure" | "ollama"
    
    async def ask(messages, system_msgs, stream=True, temperature)
        → str  # 基础提问（支持流式）
    
    async def ask_tool(messages, tools, tool_choice="auto")
        → Message  # 工具调用（返回tool_calls）
    
    async def ask_with_images(messages, images)
        → str  # 多模态支持
    
    def count_tokens(text) → int  # Token计数
    def count_message_tokens(messages) → int  # 消息token数
```

### Memory (schema.py)
```python
class Memory:
    messages: List[Message] = []
    max_messages: int = 100
    
    def add_message(msg)           # 加入单条消息
    def add_messages(msgs)         # 加入多条消息
    def get_recent_messages(n)     # 获取最近n条
    def clear()                    # 清空
```

### Message (schema.py)
```python
class Message:
    role: str                      # "system"|"user"|"assistant"|"tool"
    content: str                   # 消息内容
    tool_calls: List[ToolCall]     # 工具调用（assistant消息）
    tool_call_id: str              # 工具调用ID（tool消息）
    
    @classmethod
    Message.user_message(content)
    Message.system_message(content)
    Message.assistant_message(content)
    Message.tool_message(content, name, tool_call_id)
```

### ToolResult (tool/base.py)
```python
class ToolResult:
    output: str                    # 输出内容
    error: str                     # 错误信息
    base64_image: str              # base64编码的图像
    system: str                    # 系统消息
    
    def __str__() → str            # 返回output或error
    def replace(**kwargs)          # 创建副本并修改字段
```

---

## 文件结构速查

```
src/
├── agent/                    # 6个分析agent
│   ├── base.py              # BaseAgent抽象基类
│   ├── sentiment_analysis.py
│   ├── risk_control.py
│   ├── hot_money.py
│   ├── technical_analysis.py
│   ├── chip_analysis.py
│   └── big_deal_analysis.py
│
├── environment/             # 执行环境
│   ├── base.py             # BaseEnvironment & Factory
│   ├── research.py         # 研究阶段环境
│   └── battle.py           # 辩论阶段环境 (31KB)
│
├── tool/                   # 工具实现
│   ├── base.py            # BaseTool & ToolResult
│   ├── *_analysis.py      # 各分析工具
│   └── create_html.py     # HTML报告生成
│
├── prompt/                # 提示词模板
│   └── *.py              # 各agent的system_prompt
│
├── mcp/                   # MCP服务器
│   └── *_server.py       # 各分析的MCP实现
│
├── utils/                # 工具函数
│   ├── report_manager.py # 报告管理
│   └── cleanup_reports.py
│
├── llm.py               # LLM接口 (最复杂, 29KB)
├── schema.py            # 数据模型
├── console.py           # 可视化输出
└── config.py            # 配置管理
```

---

## API速查

### ResearchEnvironment
```python
env = await ResearchEnvironment.create(max_steps=3)
env.register_agent(agent)
results = await env.run(stock_code)  # → Dict[str, Any]
await env.cleanup()
```

### BattleEnvironment
```python
env = await BattleEnvironment.create(
    max_steps=3,
    debate_rounds=2
)
env.register_agent(agent)
results = await env.run(research_results)  # → Dict with vote_count, debate_history
await env.cleanup()
```

### LLM调用
```python
llm = LLM(config_name="default")

# 基础调用
response = await llm.ask([
    Message.user_message("问题内容"),
])

# 工具调用
message = await llm.ask_tool(
    messages=[...],
    tools=[tool.to_param() for tool in tools],
    tool_choice="auto"
)
if message.tool_calls:
    for tool_call in message.tool_calls:
        tool_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
```

---

## 枚举值速查

### AgentState
```python
AgentState.IDLE        # 空闲
AgentState.RUNNING     # 运行中
AgentState.FINISHED    # 完成
AgentState.ERROR       # 错误
```

### Role
```python
Role.SYSTEM = "system"
Role.USER = "user"
Role.ASSISTANT = "assistant"
Role.TOOL = "tool"
```

### ToolChoice
```python
ToolChoice.AUTO = "auto"        # LLM自动选择是否调用工具
ToolChoice.NONE = "none"        # 不调用工具
ToolChoice.REQUIRED = "required"  # 必须调用工具
```

---

## 常见操作

### 创建自定义Agent
```python
from src.agent.base import BaseAgent
from src.llm import LLM

class MyAgent(BaseAgent):
    name: str = "my_agent"
    system_prompt: str = "你是一个分析师..."
    
    async def step(self) -> str:
        # 获取最后一条消息
        last_msg = self.messages[-1] if self.messages else None
        
        # 调用LLM
        response = await self.llm.ask(self.messages)
        
        # 更新内存
        self.update_memory("assistant", response)
        
        return response
```

### 创建自定义Tool
```python
from src.tool.base import BaseTool, ToolResult

class MyTool(BaseTool):
    name: str = "my_tool"
    description: str = "我的工具"
    parameters: dict = {
        "type": "object",
        "properties": {
            "input": {"type": "string"}
        }
    }
    
    async def execute(self, **kwargs) -> ToolResult:
        try:
            result = some_function(kwargs["input"])
            return ToolResult(output=result)
        except Exception as e:
            return ToolResult(error=str(e))
```

### 在Agent中使用Tool
```python
async def step(self) -> str:
    # LLM调用工具
    message = await self.llm.ask_tool(
        messages=self.messages,
        tools=[self.my_tool.to_param()]
    )
    
    # 处理工具调用
    if message.tool_calls:
        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            
            # 执行工具
            if tool_name == "my_tool":
                tool_result = await self.my_tool.execute(**args)
                
                # 保存工具结果到内存
                self.update_memory(
                    role="tool",
                    content=str(tool_result),
                    name=tool_name,
                    tool_call_id=tool_call.id
                )
    
    return message.content or ""
```

---

## 输出格式

### final_results 结构
```python
{
    "stock_code": "000001.SZ",
    "analysis_time": 45.23,              # 秒
    "total_tool_calls": 12,
    "total_llm_calls": 24,
    "expert_consensus": "72.5% 看涨",
    "battle_result": {
        "final_decision": "bullish",     # bullish/bearish/unknown
        "vote_count": {
            "bullish": 4,
            "bearish": 2
        },
        "debate_history": [              # 辩论过程
            {
                "agent": "sentiment_agent",
                "content": "...",
                "timestamp": "2026-06-01T12:34:56"
            },
            ...
        ],
        "battle_highlights": [           # 关键观点
            {
                "agent": "chip_analysis_agent",
                "point": "筹码分布显示..."
            },
            ...
        ]
    }
}
```

### HTML报告结构
```
report_000001.SZ_20260601_123456.html
├── 标题 + 股票基本信息
├── 博弈结果与投票统计
│  └── 看涨 72.5% (4票) vs 看跌 27.5% (2票)
├── 各项研究分析结果
│  ├── 情感分析
│  ├── 风险评估
│  ├── 龙虎榜分析
│  ├── 技术面分析
│  ├── 筹码分析
│  └── 大单异动分析
├── 辩论对话过程
│  └── 时间线形式展示debate_history
├── 可视化图表 (可选)
└── AI免责声明
```

---

## 性能参数调优

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| max_steps | 3-5 | 每agent最多执行步数，越大越详细但更慢 |
| debate_rounds | 2-4 | 辩论轮数，3轮左右效果最佳 |
| max_tokens | 4096 | LLM最大生成token，越大越详细但更贵 |
| temperature | 0.7 | 创意度(0=确定, 1=随机)，分析用0.7较好 |
| max_messages | 100 | 内存保存消息数，越大越占内存 |

---

## 常见错误排查

| 错误 | 原因 | 解决 |
|------|------|------|
| TokenLimitExceeded | Token超限 | 减少max_steps或降低输入内容长度 |
| AuthenticationError | API密钥错误 | 检查config.toml中的api_key |
| RateLimitError | API速率限制 | 等待或使用OpenAI官方SDK |
| Empty response | LLM返回空 | 检查模型是否支持，增加temperature |
| Agent stuck | Agent重复响应 | 增加max_steps或修改system_prompt |

---

## 扩展建议

1. **新增分析指标**
   - 继承BaseAgent创建新Agent
   - 在agent/目录下创建py文件
   - 添加提示词到prompt/目录
   - 在main.py中注册agent

2. **集成新数据源**
   - 创建新Tool（继承BaseTool）
   - 在Agent.step()中调用
   - 通过ask_tool将Tool提供给LLM

3. **支持新模型**
   - 修改config.py中的LLMSettings
   - LLM类已支持OpenAI/Azure/Ollama
   - 可扩展添加其他API（Claude, Gemini等）

4. **创建MCP服务**
   - 在mcp/目录下创建*_server.py
   - 实现MCP protocol
   - 用其他工具（Claude等）调用

---

## 项目统计

| 指标 | 数值 |
|------|------|
| 总文件数 | 35+ |
| 总代码行 | 3000+ |
| 主要目录 | 8 |
| Agent数 | 6 |
| 最大模块 | chip_analysis.py (37KB) |
| 平均模块大小 | 5-15KB |
| 架构复杂度 | 中高 |
| 可扩展性 | 高 |

---

## 有用链接

- GitHub: https://github.com/HuaYaoAI/FinGenius
- OpenAI API: https://platform.openai.com
- Pydantic: https://docs.pydantic.dev
- AsyncIO: https://docs.python.org/3/library/asyncio.html

---

**最后更新**: 2026-06-01  
**版本**: v1.0  
**维护**: HuaYaoAI

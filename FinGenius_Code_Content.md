# FinGenius 核心源代码详解

---

## 1. main.py - 程序入口与主分析流程

### EnhancedFinGeniusAnalyzer 类

**初始化**:
```python
class EnhancedFinGeniusAnalyzer:
    def __init__(self):
        self.start_time = time.time()
        self.total_tool_calls = 0
        self.total_llm_calls = 0
```

**核心方法 - analyze_stock**:
```python
async def analyze_stock(self, stock_code: str, max_steps: int = 3, debate_rounds: int = 2) -> Dict[str, Any]:
    # 1. 清屏与显示Logo
    clear_screen()
    visualizer.show_logo()
    
    # 2. 研究阶段 - 6个专家并行分析
    research_results = await self._run_research_phase(stock_code, max_steps)
    
    # 3. 专家辩论阶段 - 多轮投票
    battle_results = await self._run_battle_phase(research_results, max_steps, debate_rounds)
    
    # 4. 生成报告
    await self._generate_reports(stock_code, research_results, battle_results)
    
    # 5. 汇总结果
    final_results = self._prepare_final_results(stock_code, research_results, battle_results)
    
    # 6. 显示完成提示与耗时
    total_time = time.time() - self.start_time
    visualizer.show_completion(total_time)
    
    return final_results
```

**研究阶段 - _run_research_phase**:
```python
async def _run_research_phase(self, stock_code: str, max_steps: int) -> Dict[str, Any]:
    research_env = await ResearchEnvironment.create(max_steps=max_steps)
    
    agent_names = [
        "sentiment_agent",
        "risk_control_agent", 
        "hot_money_agent",
        "technical_analysis_agent",
        "chip_analysis_agent",
        "big_deal_analysis_agent",
    ]
    
    # 注册6个专家agent（每3秒执行一个）
    for name in agent_names:
        agent = research_env.get_agent(name)
        if agent:
            visualizer.show_progress_update(f"注册研究员", f"专家: {agent.name}")
    
    # 运行分析
    results = await research_env.run(stock_code)
    
    # 统计调用次数
    if hasattr(research_env, 'tool_calls'):
        self.total_tool_calls += research_env.tool_calls
    if hasattr(research_env, 'llm_calls'):
        self.total_llm_calls += research_env.llm_calls
    
    await research_env.cleanup()
    return results
```

**辩论阶段 - _run_battle_phase**:
```python
async def _run_battle_phase(self, research_results: Dict[str, Any], max_steps: int, debate_rounds: int) -> Dict[str, Any]:
    battle_env = await BattleEnvironment.create(max_steps=max_steps, debate_rounds=debate_rounds)
    
    # 从研究环境获取同样的6个agent
    research_env = await ResearchEnvironment.create(max_steps=max_steps)
    agent_names = [...]
    
    for name in agent_names:
        agent = research_env.get_agent(name)
        if agent:
            agent.current_step = 0
            agent.state = AgentState.IDLE
            battle_env.register_agent(agent)
    
    # 增强可视化效果（显示辩论消息）
    self._enhance_battle_agents_with_visualization(battle_env)
    
    # 运行辩论
    results = await battle_env.run(research_results)
    
    await research_env.cleanup()
    await battle_env.cleanup()
    return results
```

**报告生成 - _generate_reports**:
```python
async def _generate_reports(self, stock_code: str, research_result: Dict[str, Any], battle_result: Dict[str, Any]):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    report_agent = await ReportAgent.create(max_steps=3)
    
    # 整合研究和辩论结果
    summary = f"""
    金融专家对{stock_code}的研究结果如下：
    情感分析：{research_result.get('sentiment', '暂无数据')}
    风险分析：{research_result.get('risk', '暂无数据')}
    游资分析：{research_result.get('hot_money', '暂无数据')}
    技术面分析：{research_result.get('technical', '暂无数据')}
    筹码分析：{research_result.get('chip_analysis', '暂无数据')}
    大单异动分析：{research_result.get('big_deal', '暂无数据')}
    博弈结果：{battle_result.get('final_decision', '无结果')}
    投票统计：{battle_result.get('vote_count', {})}
    """
    
    # 计算投票百分比
    bull_cnt = battle_result.get('vote_count', {}).get('bullish', 0)
    bear_cnt = battle_result.get('vote_count', {}).get('bearish', 0)
    total_votes = bull_cnt + bear_cnt
    bull_pct = round(bull_cnt / total_votes * 100, 1) if total_votes else 0
    
    # 生成HTML报告（调用create_html工具）
    html_filename = f"report_{stock_code}_{timestamp}.html"
    html_path = f"report/{html_filename}"
    
    # 保存辩论记录
    debate_data = {
        "stock_code": stock_code,
        "timestamp": timestamp,
        "debate_rounds": battle_result.get("debate_rounds", 0),
        "agent_order": battle_result.get("agent_order", []),
        "debate_history": battle_result.get("debate_history", []),
        "battle_highlights": battle_result.get("battle_highlights", [])
    }
    
    report_manager.save_debate_report(stock_code, debate_data, metadata={...})
    
    # 保存投票结果
    vote_data = {
        "stock_code": stock_code,
        "final_decision": battle_result.get("final_decision", "No decision"),
        "vote_count": battle_result.get("vote_count", {}),
        "bullish": bull_cnt,
        "bearish": bear_cnt
    }
    
    report_manager.save_vote_report(stock_code, vote_data, metadata={...})
```

**最终结果汇总 - _prepare_final_results**:
```python
def _prepare_final_results(self, stock_code: str, research_results: Dict[str, Any], battle_results: Dict[str, Any]) -> Dict[str, Any]:
    final_results = {
        "stock_code": stock_code,
        "analysis_time": time.time() - self.start_time,
        "total_tool_calls": self.total_tool_calls,
        "total_llm_calls": self.total_llm_calls
    }
    
    if research_results:
        final_results.update(research_results)
    
    if battle_results and "vote_count" in battle_results:
        votes = battle_results["vote_count"]
        total_votes = sum(votes.values())
        if total_votes > 0:
            bullish_pct = (votes.get("bullish", 0) / total_votes) * 100
            final_results["expert_consensus"] = f"{bullish_pct:.1f}% 看涨"
            final_results["battle_result"] = battle_results
    
    return final_results
```

**主函数**:
```python
async def main():
    parser = argparse.ArgumentParser(description="FinGenius Stock Research")
    parser.add_argument("stock_code", help="Stock code to research (e.g., AAPL, MSFT)")
    parser.add_argument("-f", "--format", choices=["text", "json"], default="text")
    parser.add_argument("-o", "--output", help="Save results to file")
    parser.add_argument("--tts", action="store_true", help="Enable text-to-speech")
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--debate-rounds", type=int, default=2)

    args = parser.parse_args()
    analyzer = EnhancedFinGeniusAnalyzer()

    try:
        results = await analyzer.analyze_stock(args.stock_code, args.max_steps, args.debate_rounds)
        display_results(results, args.format, args.output)

        if args.tts:
            os.makedirs("results", exist_ok=True)
            await announce_result_with_tts(results)

    except KeyboardInterrupt:
        visualizer.show_error("分析被用户中断", "Ctrl+C")
        return 1
    except Exception as e:
        visualizer.show_error(f"分析过程中发生错误: {str(e)}")
        return 1

    return 0
```

---

## 2. src/schema.py - 数据模型定义

```python
from enum import Enum
from typing import Any, List, Literal, Optional, Union
from pydantic import BaseModel, Field

# === 枚举 ===

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

ROLE_VALUES = tuple(role.value for role in Role)
ROLE_TYPE = Literal[ROLE_VALUES]

class ToolChoice(str, Enum):
    NONE = "none"
    AUTO = "auto"
    REQUIRED = "required"

TOOL_CHOICE_VALUES = tuple(choice.value for choice in ToolChoice)
TOOL_CHOICE_TYPE = Literal[TOOL_CHOICE_VALUES]

class AgentState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    ERROR = "ERROR"

# === 数据模型 ===

class Function(BaseModel):
    name: str
    arguments: str

class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: Function

class Message(BaseModel):
    role: ROLE_TYPE = Field(...)
    content: Optional[str] = Field(default=None)
    tool_calls: Optional[List[Any]] = Field(default=None)
    name: Optional[str] = Field(default=None)
    tool_call_id: Optional[str] = Field(default=None)
    base64_image: Optional[str] = Field(default=None)

    def __add__(self, other) -> List["Message"]:
        if isinstance(other, list):
            return [self] + other
        elif isinstance(other, Message):
            return [self, other]
        else:
            raise TypeError(f"...")

    def to_dict(self) -> dict:
        message = {"role": self.role}
        if self.content is not None:
            message["content"] = self.content
        if self.tool_calls is not None:
            message["tool_calls"] = [
                tool_call.model_dump() if hasattr(tool_call, "model_dump") else tool_call.dict()
                for tool_call in self.tool_calls
            ]
        if self.name is not None:
            message["name"] = self.name
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.base64_image is not None:
            message["base64_image"] = self.base64_image
        return message

    @classmethod
    def user_message(cls, content: str, base64_image: Optional[str] = None) -> "Message":
        return cls(role=Role.USER, content=content, base64_image=base64_image)

    @classmethod
    def system_message(cls, content: str, base64_image: Optional[str] = None) -> "Message":
        return cls(role=Role.SYSTEM, content=content, base64_image=base64_image)

    @classmethod
    def assistant_message(cls, content: Optional[str] = None, base64_image: Optional[str] = None) -> "Message":
        return cls(role=Role.ASSISTANT, content=content, base64_image=base64_image)

    @classmethod
    def tool_message(cls, content: str, name, tool_call_id: str, base64_image: Optional[str] = None) -> "Message":
        return cls(role=Role.TOOL, content=content, name=name, tool_call_id=tool_call_id, base64_image=base64_image)

    @classmethod
    def from_tool_calls(cls, tool_calls: List[Any], content: Union[str, List[str]] = "",
                        base64_image: Optional[str] = None, **kwargs):
        formatted_calls = [
            {"id": call.id, "function": call.function.model_dump(), "type": "function"}
            for call in tool_calls
        ]
        return cls(role=Role.ASSISTANT, content=content, tool_calls=formatted_calls,
                   base64_image=base64_image, **kwargs)

class Memory(BaseModel):
    messages: List[Message] = Field(default_factory=list)
    max_messages: int = Field(default=100)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def add_messages(self, messages: List[Message]) -> None:
        self.messages.extend(messages)
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def clear(self) -> None:
        self.messages.clear()

    def get_recent_messages(self, n: int) -> List[Message]:
        return self.messages[-n:]

    def to_dict_list(self) -> List[dict]:
        return [msg.to_dict() for msg in self.messages]
```

---

## 3. src/agent/base.py - Agent 基类

```python
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator
from src.llm import LLM
from src.schema import ROLE_TYPE, AgentState, Memory, Message

class BaseAgent(BaseModel, ABC):
    name: str = Field(..., description="Unique name of the agent")
    description: Optional[str] = Field(None, description="Optional agent description")
    system_prompt: Optional[str] = Field(None, description="System-level instruction prompt")
    next_step_prompt: Optional[str] = Field(None, description="Prompt for determining next action")
    llm: LLM = Field(default_factory=LLM, description="Language model instance")
    memory: Memory = Field(default_factory=Memory, description="Agent's memory store")
    state: AgentState = Field(default=AgentState.IDLE, description="Current agent state")
    max_steps: int = Field(default=10, description="Maximum steps before termination")
    current_step: int = Field(default=0, description="Current step in execution")
    duplicate_threshold: int = 2

    class Config:
        arbitrary_types_allowed = True
        extra = "allow"

    @model_validator(mode="after")
    def initialize_agent(self) -> "BaseAgent":
        if self.llm is None or not isinstance(self.llm, LLM):
            self.llm = LLM(config_name=self.name.lower())
        if not isinstance(self.memory, Memory):
            self.memory = Memory()
        return self

    # 状态管理上下文
    @asynccontextmanager
    async def state_context(self, new_state: AgentState):
        if not isinstance(new_state, AgentState):
            raise ValueError(f"Invalid state: {new_state}")
        previous_state = self.state
        self.state = new_state
        try:
            yield
        except Exception as e:
            self.state = AgentState.ERROR
            raise e
        finally:
            self.state = previous_state

    # 更新agent的记忆
    def update_memory(self, role: ROLE_TYPE, content: str, base64_image: Optional[str] = None, **kwargs) -> None:
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }
        if role not in message_map:
            raise ValueError(f"Unsupported message role: {role}")
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        self.memory.add_message(message_map[role](content, **kwargs))

    # 主运行循环
    async def run(self, request: Optional[str] = None) -> str:
        if self.state != AgentState.IDLE:
            raise RuntimeError(f"Cannot run agent from state: {self.state}")
        if request:
            self.update_memory("user", request)
        results: List[str] = []
        async with self.state_context(AgentState.RUNNING):
            while self.current_step < self.max_steps and self.state != AgentState.FINISHED:
                self.current_step += 1
                logger.info(f"Executing step {self.current_step}/{self.max_steps}")
                step_result = await self.step()
                if self.is_stuck():
                    self.handle_stuck_state()
                results.append(f"Step {self.current_step}: {step_result}")
            if self.current_step >= self.max_steps:
                self.current_step = 0
                self.state = AgentState.IDLE
                results.append(f"Terminated: Reached max steps ({self.max_steps})")
        return "\n".join(results) if results else "No steps executed"

    # 抽象方法 - 由子类实现单个步骤的逻辑
    @abstractmethod
    async def step(self) -> str:
        pass

    # 卡住状态处理
    def handle_stuck_state(self):
        stuck_prompt = "Observed duplicate responses. Consider new strategies and avoid repeating ineffective paths."
        self.next_step_prompt = f"{stuck_prompt}\n{self.next_step_prompt}"
        logger.warning(f"Agent detected stuck state. Added prompt: {stuck_prompt}")

    # 检测是否卡住（重复响应）
    def is_stuck(self) -> bool:
        if len(self.memory.messages) < 2:
            return False
        last_message = self.memory.messages[-1]
        if not last_message.content:
            return False
        duplicate_count = sum(
            1 for msg in reversed(self.memory.messages[:-1])
            if msg.role == "assistant" and msg.content == last_message.content
        )
        return duplicate_count >= self.duplicate_threshold

    @property
    def messages(self) -> List[Message]:
        return self.memory.messages

    @messages.setter
    def messages(self, value: List[Message]):
        self.memory.messages = value

    def reset_execution_state(self) -> None:
        self.current_step = 0
        self.state = AgentState.IDLE
        logger.info(f"Agent '{self.name}' execution state has been reset")
```

---

## 4. src/environment/base.py - 环境框架

```python
from abc import abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field
from src.agent.base import BaseAgent
from src.logger import logger

class BaseEnvironment(BaseModel):
    name: str = Field(default="base_environment")
    description: str = Field(default="Base environment class")
    agents: Dict[str, BaseAgent] = Field(default_factory=dict)
    max_steps: int = Field(default=3, description="Maximum steps for each agent")

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    async def create(cls, **kwargs) -> "BaseEnvironment":
        instance = cls(**kwargs)
        await instance.initialize()
        return instance

    async def initialize(self) -> None:
        logger.info(f"Initializing {self.name} environment (max_steps={self.max_steps})")

    def register_agent(self, agent: BaseAgent) -> None:
        self.agents[agent.name] = agent
        logger.debug(f"Agent {agent.name} registered in {self.name}")

    def add_agent(self, agent: BaseAgent) -> None:
        self.register_agent(agent)

    def get_agent(self, agent_name: str) -> Optional[BaseAgent]:
        return self.agents.get(agent_name)

    @abstractmethod
    async def run(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError("Subclasses must implement run method")

    async def cleanup(self) -> None:
        logger.info(f"Cleaning up {self.name} environment")

# 环境类型枚举
class EnvironmentType(str, Enum):
    RESEARCH = "research"
    BATTLE = "battle"

# 环境工厂
class EnvironmentFactory:
    @staticmethod
    async def create_environment(
        environment_type: EnvironmentType,
        agents: Union[BaseAgent, List[BaseAgent], Dict[str, BaseAgent]] = None,
        **kwargs,
    ) -> BaseEnvironment:
        from src.environment.battle import BattleEnvironment
        from src.environment.research import ResearchEnvironment

        environments = {
            EnvironmentType.RESEARCH: ResearchEnvironment,
            EnvironmentType.BATTLE: BattleEnvironment,
        }

        environment_class = environments.get(environment_type)
        if not environment_class:
            raise ValueError(f"Unknown environment type: {environment_type}")

        environment = await environment_class.create(**kwargs)

        if agents:
            if isinstance(agents, BaseAgent):
                environment.add_agent(agents)
            elif isinstance(agents, list):
                for agent in agents:
                    environment.add_agent(agent)
            elif isinstance(agents, dict):
                for agent in agents.values():
                    environment.add_agent(agent)

        return environment
```

---

## 5. src/tool/base.py - 工具基类

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
import time, logging
from datetime import datetime, timedelta

class BaseTool(ABC, BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]

    def __call__(self, **kwargs):
        return self.execute(**kwargs)

    @abstractmethod
    async def execute(**kwargs):
        pass

    def to_param(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }

class ToolResult(BaseModel):
    output: Optional[str] = None
    error: Optional[str] = None
    base64_image: Optional[str] = None
    system: Optional[str] = None

    def __bool__(self):
        return bool(self.output or self.error or self.base64_image or self.system)

    def __add__(self, other):
        if isinstance(other, ToolResult):
            if self.base64_image and other.base64_image and self.base64_image != other.base64_image:
                raise ValueError("Cannot merge ToolResults with conflicting base64_image values")
            return ToolResult(
                output=(self.output or "") + (other.output or ""),
                error=self.error or other.error,
                base64_image=self.base64_image or other.base64_image,
                system=self.system or other.system,
            )
        return NotImplemented

    def __str__(self):
        if self.error:
            return self.error
        return self.output or ""

    def replace(self, **kwargs):
        return self.model_copy(update=kwargs)

class CLIResult(ToolResult):
    """CLI-renderable result"""
    pass

class ToolFailure(ToolResult):
    """Represents a tool failure"""
    pass

def get_recent_trading_day(date_format: str = "%Y-%m-%d") -> str:
    """返回最近的交易日（周一-周五）"""
    current_date = datetime.now()
    while current_date.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        current_date -= timedelta(days=1)
    return current_date.strftime(date_format)
```

---

## 6. src/llm.py - LLM 接口详解（关键部分）

### TokenCounter 类

```python
class TokenCounter:
    BASE_MESSAGE_TOKENS = 4
    FORMAT_TOKENS = 2
    LOW_DETAIL_IMAGE_TOKENS = 85
    HIGH_DETAIL_TILE_TOKENS = 170
    MAX_SIZE = 2048
    HIGH_DETAIL_TARGET_SHORT_SIDE = 768
    TILE_SIZE = 512

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def count_text(self, text: str) -> int:
        """计算文本的token数"""
        if not text:
            return 0
        return len(self.tokenizer.encode(text))

    def count_image(self, image_item: dict) -> int:
        """计算图像的token数"""
        detail = image_item.get("detail", "medium")
        if detail == "low":
            return self.LOW_DETAIL_IMAGE_TOKENS
        if detail in ("high", "medium"):
            if "dimensions" in image_item:
                width, height = image_item["dimensions"]
                return self._calculate_high_detail_tokens(width, height)
        if detail == "high":
            return self._calculate_high_detail_tokens(1024, 1024)
        elif detail == "medium":
            return 1024
        else:
            return 1024

    def _calculate_high_detail_tokens(self, width: int, height: int) -> int:
        """计算高清图像的token数（分块处理）"""
        if width > self.MAX_SIZE or height > self.MAX_SIZE:
            scale = self.MAX_SIZE / max(width, height)
            width = int(width * scale)
            height = int(height * scale)
        scale = self.HIGH_DETAIL_TARGET_SHORT_SIDE / min(width, height)
        scaled_width = int(width * scale)
        scaled_height = int(height * scale)
        tiles_x = math.ceil(scaled_width / self.TILE_SIZE)
        tiles_y = math.ceil(scaled_height / self.TILE_SIZE)
        total_tiles = tiles_x * tiles_y
        return (total_tiles * self.HIGH_DETAIL_TILE_TOKENS) + self.LOW_DETAIL_IMAGE_TOKENS

    def count_content(self, content: Union[str, List[Union[str, dict]]]) -> int:
        """统计content中的token数"""
        if not content:
            return 0
        if isinstance(content, str):
            return self.count_text(content)
        token_count = 0
        for item in content:
            if isinstance(item, str):
                token_count += self.count_text(item)
            elif isinstance(item, dict):
                if "text" in item:
                    token_count += self.count_text(item["text"])
                elif "image_url" in item:
                    token_count += self.count_image(item)
        return token_count

    def count_message_tokens(self, messages: List[dict]) -> int:
        """计算整个消息列表的token数"""
        total_tokens = self.FORMAT_TOKENS
        for message in messages:
            tokens = self.BASE_MESSAGE_TOKENS
            tokens += self.count_text(message.get("role", ""))
            if "content" in message:
                tokens += self.count_content(message["content"])
            if "tool_calls" in message:
                tokens += self.count_tool_calls(message["tool_calls"])
            tokens += self.count_text(message.get("name", ""))
            tokens += self.count_text(message.get("tool_call_id", ""))
            total_tokens += tokens
        return total_tokens
```

### LLM 主类

```python
class LLM:
    _instances: Dict[str, "LLM"] = {}  # 单例存储

    def __new__(cls, config_name: str = "default", llm_config: Optional[LLMSettings] = None):
        """单例模式"""
        if config_name not in cls._instances:
            instance = super().__new__(cls)
            instance.__init__(config_name, llm_config)
            cls._instances[config_name] = instance
        return cls._instances[config_name]

    def __init__(self, config_name: str = "default", llm_config: Optional[LLMSettings] = None):
        if not hasattr(self, "client"):
            llm_config = llm_config or config.llm
            llm_config = llm_config.get(config_name, llm_config["default"])
            
            self.model = llm_config.model
            self.max_tokens = llm_config.max_tokens
            self.temperature = llm_config.temperature
            self.api_type = llm_config.api_type  # "openai", "azure", "ollama"
            self.api_key = llm_config.api_key
            self.api_version = llm_config.api_version
            self.base_url = llm_config.base_url
            self.total_input_tokens = 0
            self.max_input_tokens = (
                llm_config.max_input_tokens if hasattr(llm_config, "max_input_tokens") else None
            )
            
            # 初始化tokenizer
            try:
                self.tokenizer = tiktoken.encoding_for_model(self.model)
            except KeyError:
                self.tokenizer = tiktoken.get_encoding("cl100k_base")

            # 根据API类型初始化客户端
            if self.api_type == "azure":
                self.client = AsyncAzureOpenAI(
                    base_url=self.base_url, api_key=self.api_key, api_version=self.api_version
                )
            elif self.api_type == "ollama":
                self.client = OllamaAsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
            else:
                self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

            self.token_counter = TokenCounter(self.tokenizer)

    @staticmethod
    def format_messages(messages: List[Union[dict, Message]]) -> List[dict]:
        """将Message对象转换为dict格式"""
        formatted_messages = []
        for message in messages:
            if isinstance(message, Message):
                message = message.to_dict()
            if isinstance(message, dict):
                if "role" not in message:
                    raise ValueError("Message dict must contain 'role' field")
                if "content" in message or "tool_calls" in message:
                    formatted_messages.append(message)
            else:
                raise TypeError(f"Unsupported message type: {type(message)}")
        for msg in formatted_messages:
            if msg["role"] not in ROLE_VALUES:
                raise ValueError(f"Invalid role: {msg['role']}")
        return formatted_messages

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type((OpenAIError, Exception, ValueError)),
    )
    async def ask(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        stream: bool = True,
        temperature: Optional[float] = None,
    ) -> str:
        """通用提问方法（支持流式输出）"""
        try:
            # 合并系统消息
            if system_msgs:
                system_msgs = self.format_messages(system_msgs)
                messages = system_msgs + self.format_messages(messages)
            else:
                messages = self.format_messages(messages)

            # Token检查
            input_tokens = self.count_message_tokens(messages)
            if not self.check_token_limit(input_tokens):
                raise TokenLimitExceeded(self.get_limit_error_message(input_tokens))

            # 构建请求参数
            params = {"model": self.model, "messages": messages}

            if self.api_type == "ollama":
                params["max_tokens"] = self.max_tokens
                params["temperature"] = temperature if temperature is not None else self.temperature
            elif is_reasoning_model(self.model):
                params["max_completion_tokens"] = self.max_tokens  # 推理模型用不同参数
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = temperature if temperature is not None else self.temperature

            # 非流式调用
            if not stream:
                params["stream"] = False
                response = await self.client.chat.completions.create(**params)
                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("Empty or invalid response from LLM")
                self.update_token_count(response.usage.prompt_tokens)
                return response.choices[0].message.content

            # 流式调用
            self.update_token_count(input_tokens)
            params["stream"] = True
            response = await self.client.chat.completions.create(**params)

            collected_messages = []
            async for chunk in response:
                chunk_message = chunk.choices[0].delta.content or ""
                collected_messages.append(chunk_message)
                print(chunk_message, end="", flush=True)  # 实时打印

            print()
            full_response = "".join(collected_messages).strip()
            if not full_response:
                raise ValueError("Empty response from streaming LLM")
            return full_response

        except TokenLimitExceeded:
            raise
        except ValueError as ve:
            logger.error(f"Validation error: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask: {e}")
            raise

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type((OpenAIError, Exception, ValueError)),
    )
    async def ask_tool(
        self,
        messages: List[Union[dict, Message]],
        system_msgs: Optional[List[Union[dict, Message]]] = None,
        timeout: int = 300,
        tools: Optional[List[dict]] = None,
        tool_choice: TOOL_CHOICE_TYPE = ToolChoice.AUTO,
        temperature: Optional[float] = None,
        **kwargs,
    ):
        """工具调用方法"""
        try:
            # 验证工具参数
            if tool_choice not in TOOL_CHOICE_VALUES:
                raise ValueError(f"Invalid tool_choice: {tool_choice}")

            # 合并消息
            if system_msgs:
                system_msgs = self.format_messages(system_msgs)
                messages = system_msgs + self.format_messages(messages)
            else:
                messages = self.format_messages(messages)

            # Token统计
            input_tokens = self.count_message_tokens(messages)
            tools_tokens = 0
            if tools:
                for tool in tools:
                    tools_tokens += self.count_tokens(str(tool))
            input_tokens += tools_tokens

            if not self.check_token_limit(input_tokens):
                raise TokenLimitExceeded(self.get_limit_error_message(input_tokens))

            # 工具验证
            if tools:
                for tool in tools:
                    if not isinstance(tool, dict) or "type" not in tool:
                        raise ValueError("Each tool must be a dict with 'type' field")

            # 某些API不支持AUTO，降级为NONE
            if tool_choice == ToolChoice.AUTO and any(
                keyword in self.base_url.lower() for keyword in ["openrouter", "infini"]
            ):
                tool_choice = ToolChoice.NONE

            # 构建参数
            params = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "timeout": timeout,
                **kwargs,
            }

            if self.model in REASONING_MODELS:
                params["max_completion_tokens"] = self.max_tokens
            else:
                params["max_tokens"] = self.max_tokens
                params["temperature"] = temperature if temperature is not None else self.temperature

            response = await self.client.chat.completions.create(**params)
            if not response.choices or not response.choices[0].message:
                raise ValueError("Invalid or empty response from LLM")

            self.update_token_count(response.usage.prompt_tokens)
            return response.choices[0].message

        except TokenLimitExceeded:
            raise
        except ValueError as ve:
            logger.error(f"Validation error in ask_tool: {ve}")
            raise
        except OpenAIError as oe:
            logger.error(f"OpenAI API error: {oe}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in ask_tool: {e}")
            raise
```

---

## 关键常数与配置

**推理模型列表**:
```python
REASONING_MODELS = ["o1", "o3-mini", "deepseek-r1"]
```

**重试策略**:
- 最多6次重试
- 指数退避：1-60秒随机等待
- 针对OpenAI错误、ValueError和Exception

**Token限制**:
- 默认max_input_tokens: None（无限制）
- max_messages: 100条（内存中保存最多100条消息）

---

## 流程总结

```
用户输入股票代码
    ↓
EnhancedFinGeniusAnalyzer.analyze_stock()
    ├─ ResearchEnvironment (研究)
    │  ├─ sentiment_agent
    │  ├─ risk_control_agent
    │  ├─ hot_money_agent
    │  ├─ technical_analysis_agent
    │  ├─ chip_analysis_agent
    │  └─ big_deal_analysis_agent
    │     → 各自执行 agent.run() → 调用 LLM.ask_tool()
    │     → 结果保存到Memory中
    │
    ├─ BattleEnvironment (辩论)
    │  ├─ 相同的6个agent（重置状态）
    │  ├─ 多轮辩论（debate_rounds轮）
    │  ├─ 每轮：
    │  │   ├─ Agent说出观点（LLM生成）
    │  │   ├─ 广播给其他agent
    │  │   └─ 其他agent回应
    │  └─ 最后投票（bullish/bearish）
    │     → 结果：final_decision, vote_count, debate_history
    │
    ├─ ReportAgent (报告)
    │  ├─ 生成HTML美化报告
    │  ├─ 保存JSON辩论记录
    │  └─ 保存JSON投票结果
    │
    └─ 返回final_results
       ├─ stock_code
       ├─ analysis_time
       ├─ total_tool_calls
       ├─ total_llm_calls
       ├─ expert_consensus (看涨百分比)
       └─ battle_result (详细投票信息)
```


"""
BaseAgent — 所有专家 Agent 的抽象基类
同步版（无 asyncio），直接调用 LLM
"""
from abc import abstractmethod
from typing import Any, Dict, Optional
from nasdx.schema import AgentState, AnalysisResult, Memory, Message
from nasdx.llm import llm


class BaseAgent:
    """专家 Agent 基类"""

    name: str = "base_agent"
    description: str = "基础 Agent"
    system_prompt: str = "你是一位专业的A股分析师。"

    def __init__(self, max_steps: int = 3):
        self.max_steps = max_steps
        self.state = AgentState.IDLE
        self.memory = Memory()

    def run(self, stock_code: str, stock_data: Dict[str, Any]) -> AnalysisResult:
        """执行分析，返回结构化结果"""
        self.state = AgentState.RUNNING
        self.memory.clear()

        # 注入股票基础信息
        intro = self._build_context(stock_code, stock_data)
        self.memory.add_message(Message.user_message(intro))

        try:
            result = self._analyze(stock_code, stock_data)
            self.state = AgentState.FINISHED
            return result
        except Exception as e:
            self.state = AgentState.ERROR
            return AnalysisResult(
                agent_name=self.name,
                dimension=self.dimension,
                conclusion=f"分析失败：{e}",
                signal="neutral",
                confidence=0.0,
                key_points=[],
            )

    @property
    def dimension(self) -> str:
        return "base"

    def _build_context(self, stock_code: str, stock_data: Dict[str, Any]) -> str:
        """构建上下文字符串，子类可 override"""
        name = stock_data.get("name", "")
        sector = stock_data.get("sector_name", "")
        return f"股票：{stock_code} {name}，所属板块：{sector}"

    @abstractmethod
    def _analyze(self, stock_code: str, stock_data: Dict[str, Any]) -> AnalysisResult:
        """子类实现具体分析逻辑"""
        raise NotImplementedError

    def _ask(self, prompt: str, temperature: float = 0.3) -> str:
        """调用 LLM"""
        messages = self.memory.to_list()
        messages.append({"role": "user", "content": prompt})
        return llm.ask(messages, system=self.system_prompt, temperature=temperature)

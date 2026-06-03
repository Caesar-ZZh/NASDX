"""
核心数据结构 — 消息、状态、记忆
"""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    FINISHED = "finished"
    ERROR = "error"


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    role: Role
    content: str

    @classmethod
    def system_message(cls, content: str) -> "Message":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user_message(cls, content: str) -> "Message":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant_message(cls, content: str) -> "Message":
        return cls(role=Role.ASSISTANT, content=content)

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role.value, "content": self.content}


class Memory(BaseModel):
    messages: List[Message] = Field(default_factory=list)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)

    def to_list(self) -> List[Dict[str, str]]:
        return [m.to_dict() for m in self.messages]

    def clear(self) -> None:
        self.messages.clear()


class AnalysisResult(BaseModel):
    """单个 Agent 的分析结果"""
    agent_name: str
    dimension: str  # technical / fund_flow / sentiment / risk / chip / sector
    conclusion: str
    signal: str  # bullish / bearish / neutral
    confidence: float = Field(ge=0.0, le=1.0)
    key_points: List[str] = Field(default_factory=list)
    raw_data_summary: Optional[str] = None


class BattleVote(BaseModel):
    """辩论投票"""
    agent_name: str
    vote: str  # bullish / bearish / neutral
    reasoning: str


class FinalReport(BaseModel):
    """最终综合报告"""
    stock_code: str
    stock_name: str
    date: str
    research_results: Dict[str, Any] = Field(default_factory=dict)
    battle_transcript: List[str] = Field(default_factory=list)
    votes: List[BattleVote] = Field(default_factory=list)
    final_signal: str = "neutral"
    bullish_pct: float = 0.0
    summary: str = ""
    operation_advice: str = ""

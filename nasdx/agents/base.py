"""
BaseAgent — 所有专家 Agent 的抽象基类
同步版（无 asyncio），直接调用 LLM
"""
from abc import abstractmethod
from typing import Any, Dict, Optional, Tuple
from nasdx.schema import AgentState, AnalysisResult, Memory, Message
from nasdx.llm import extract_json_payload, llm


class BaseAgent:
    """专家 Agent 基类"""

    name: str = "base_agent"
    description: str = "基础 Agent"
    system_prompt: str = "你是一位专业的A股分析师。"
    STRUCTURED_OUTPUT_CONTRACT = """

请在回答末尾单独输出一个 ```json 代码块，字段必须为：
{
  "signal": "bullish|bearish|neutral",
  "confidence": 0.0,
  "conclusion": "可直接进入报告的中文结论",
  "key_points": ["关键依据1", "关键依据2"]
}
要求：confidence 必须是 0 到 1 的数字；JSON 外可以有分析正文，但最终决策字段以 JSON 为准。
"""

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

    def _ask_analysis(
        self,
        prompt: str,
        temperature: float = 0.3,
    ) -> Tuple[str, Dict[str, Any] | None]:
        """Call the LLM and parse the optional structured analysis payload."""
        response = self._ask(prompt + self.STRUCTURED_OUTPUT_CONTRACT, temperature=temperature)
        try:
            return response, extract_json_payload(response)
        except ValueError:
            return response, None

    def _parse_structured_signal(
        self,
        response: str,
        payload: Dict[str, Any] | None,
        final: bool = False,
    ) -> tuple[str, float]:
        """Prefer JSON signal/confidence, then fall back to legacy tail tags."""
        fallback_signal, fallback_confidence = self._legacy_parse_signal(response, final=final)
        if not payload:
            return fallback_signal, fallback_confidence

        signal = str(payload.get("signal") or payload.get("final_signal") or "").strip().lower()
        if signal not in {"bullish", "bearish", "neutral"}:
            signal = fallback_signal

        confidence_raw = payload.get("confidence")
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = fallback_confidence
        confidence = min(1.0, max(0.0, confidence))
        return signal, confidence

    def _structured_conclusion(self, response: str, payload: Dict[str, Any] | None) -> str:
        """Return report-facing conclusion text from JSON when available."""
        if payload:
            for key in ("conclusion", "summary", "analysis"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return response

    def _structured_key_points(self, payload: Dict[str, Any] | None) -> list[str]:
        """Extract key point strings from a structured payload."""
        if not payload:
            return []
        value = payload.get("key_points")
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _merge_key_points(self, *groups: list[str]) -> list[str]:
        """Merge key points while preserving order and removing duplicates."""
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                text = str(item).strip()
                if text and text not in seen:
                    merged.append(text)
                    seen.add(text)
        return merged

    def _parse_signal(self, text: str, final: bool = False) -> tuple[str, float]:
        """Parse legacy signal tags for callers that still use plain text output."""
        return self._legacy_parse_signal(text, final=final)

    def _legacy_parse_signal(self, text: str, final: bool = False) -> tuple[str, float]:
        signal = "neutral"
        confidence = 0.5
        signal_markers = ("【最终信号】", "【信号】") if final else ("【信号】",)
        for line in text.split("\n"):
            if any(marker in line for marker in signal_markers):
                lower = line.lower()
                if "bullish" in lower:
                    signal = "bullish"
                elif "bearish" in lower:
                    signal = "bearish"
                elif "neutral" in lower:
                    signal = "neutral"
            if "【置信度】" in line:
                try:
                    confidence = float(line.split("】")[-1].strip())
                except ValueError:
                    pass
        return signal, min(1.0, max(0.0, confidence))

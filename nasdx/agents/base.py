"""
BaseAgent — 所有专家 Agent 的抽象基类
同步版（无 asyncio），直接调用 LLM
"""
import re
import time
from abc import abstractmethod
from typing import Any, Dict, Optional, Tuple
from nasdx.schema import AgentState, AnalysisResult, Memory, Message
from nasdx.llm import extract_json_payload, llm


# Agent 层外层重试开关。LLM 客户端在单次 ask() 内部已有 5 次重试 + fallback；
# 但当 5 次全部失败时，丢整张卡片对单只股票的影响太大——绝大多数瞬时抖动
# （request_timeout / rate_limit / 上游限流）在 2s 后再试就能恢复。详见
# test_llm_structured_contracts 中的 test_agent_retries_once_on_transient
# 与 test_agent_does_not_retry_invalid_request。
_AGENT_RETRY_DELAY_SECONDS = 2.0
# 决定是否值得重试的错误分类：与 LLMClient 内部的 _classify_api_error 一致。
# invalid_request (400/422 等) 是请求本身有问题，重试不会恢复；authentication
# 是凭证问题，重试同样不会恢复；其余全部按瞬时错误处理。
# llm.py 的中文报错用全角括号「（）」，按 ASCII / 全角都接住。
_TRANSIENT_CLASSIFICATION_RE = re.compile(
    r"[(（](?:错误分类|classification)[：:](?P<cls>[a-z_]+)[)）]"
)
_NON_RETRYABLE_CLASSIFICATIONS = frozenset({"invalid_request", "authentication", "fatal"})


def _should_retry_agent(error_message: str) -> bool:
    """判断 Agent 层外层是否值得重试：仅在错误属于瞬时类（timeout / rate /
    transient / elapsed budget）时返回 True；invalid_request / auth / fatal 直接吞。
    """
    match = _TRANSIENT_CLASSIFICATION_RE.search(error_message or "")
    if not match:
        # 没有分类信息说明不是 LLMClient 抛出的——通常是 _analyze 内部别的代码
        # 路径，比如 _parse_structured_signal 之类的本地错误，没必要重试。
        return False
    return match.group("cls") not in _NON_RETRYABLE_CLASSIFICATIONS


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

        # 注入股票基础信息。若 _build_context 返回空字符串（子类刻意为之或
        # 实现遗漏），不要把空 user 消息塞进 memory——OpenAI 兼容网关（Agnes /
        # DeepSeek / Qwen 等）会把空 content 的 user 消息判定为 invalid_request
        # 直接 400，导致整个 Agent 卡片显示「分析失败: LLM 请求无效」。
        intro = self._build_context(stock_code, stock_data)
        if intro and intro.strip():
            self.memory.add_message(Message.user_message(intro))

        # Agent 层外层重试：LLM 客户端内部 5 次重试全部失败时，丢整张卡片对
        # 深度分析的影响不可接受（risk / sector 等单点失败会让最终看多占比失真）。
        # 绝大多数瞬时抖动 2s 后再试一次就能恢复；invalid_request / auth 这类
        # 确定性错误不重试，避免拖慢整体节奏。
        last_error: Optional[BaseException] = None
        retried = False
        for attempt in (1, 2):
            try:
                result = self._analyze(stock_code, stock_data)
                self.state = AgentState.FINISHED
                return result
            except Exception as e:
                last_error = e
                if attempt == 1 and _should_retry_agent(str(e)):
                    retried = True
                    time.sleep(_AGENT_RETRY_DELAY_SECONDS)
                    self.memory.clear()
                    if intro and intro.strip():
                        self.memory.add_message(Message.user_message(intro))
                    continue
                break
        self.state = AgentState.ERROR
        suffix = "（已重试 1 次）" if retried else ""
        return AnalysisResult(
            agent_name=self.name,
            dimension=self.dimension,
            conclusion=f"分析失败：{last_error}{suffix}",
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

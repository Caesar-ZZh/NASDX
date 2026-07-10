"""
LLM 客户端 — 支持 OpenAI 兼容接口（DeepSeek / Qwen / GPT-4o 等）
无需 API Key 也能通过 Ollama 本地模型运行
"""
import os
import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from openai import OpenAI

# 从环境变量读取；不要在仓库中写入真实 API Key。
API_KEY    = os.environ.get("NASDX_API_KEY", "")
BASE_URL   = os.environ.get("NASDX_BASE_URL", "https://api.deepseek.com")
MODEL_NAME = os.environ.get("NASDX_MODEL", "deepseek-chat")
MAX_TOKENS = int(os.environ.get("NASDX_MAX_TOKENS", "4096"))
TEMPERATURE = float(os.environ.get("NASDX_TEMPERATURE", "0.3"))


def extract_json_payload(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from an LLM response."""
    if not text or not text.strip():
        raise ValueError("empty LLM response")

    decoder = json.JSONDecoder()
    fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    for block in fenced_blocks:
        payload = _loads_json_object(block.strip())
        if payload is not None:
            return payload

    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("LLM response does not contain a JSON object")


def _loads_json_object(text: str) -> Dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class LLMClient:
    """单例 LLM 客户端"""
    _instance: Optional["LLMClient"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.client = OpenAI(api_key=API_KEY or "nasdx-local-placeholder", base_url=BASE_URL)
        self.model = MODEL_NAME
        self.max_tokens = MAX_TOKENS
        self.temperature = TEMPERATURE

    # 主模型失败时的备用模型列表
    FALLBACK_MODELS = ["deepseek-reasoner", "deepseek-chat"]

    def ask(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_retries: int = 3,
    ) -> str:
        """同步调用 LLM，失败自动降级备用模型"""
        if not API_KEY and not _is_local_base_url(BASE_URL):
            raise RuntimeError("请先设置 NASDX_API_KEY，或切换到 Ollama 本地模型")

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        models_to_try = [self.model] + self.FALLBACK_MODELS

        for model in models_to_try:
            for attempt in range(max_retries):
                try:
                    # 推理模型（如 deepseek-reasoner）temperature 必须为 1
                    is_reasoner = any(x in model for x in ("reasoner", "thinking", "r1"))
                    call_kwargs = dict(
                        model=model,
                        messages=full_messages,
                        max_tokens=self.max_tokens,
                    )
                    if not is_reasoner:
                        call_kwargs["temperature"] = temperature if temperature is not None else self.temperature
                    resp = self.client.chat.completions.create(**call_kwargs)
                    msg = resp.choices[0].message
                    # 优先取 content，推理模型 content 为空时取 reasoning_content
                    content = msg.content or ""
                    if not content:
                        content = getattr(msg, "reasoning_content", "") or ""
                    if model != self.model:
                        print(f"[LLM] 已降级使用 {model}")
                    return content
                except Exception as e:
                    err_str = str(e)
                    # 502/503 服务不可用 → 换模型
                    if "502" in err_str or "503" in err_str or "Upstream" in err_str:
                        if attempt == max_retries - 1:
                            print(f"[LLM] {model} 不可用，尝试备用模型...")
                            break  # 跳到下一个模型
                        wait = 2 ** attempt
                        print(f"[LLM] 调用失败，{wait}s 后重试... ({err_str[:60]})")
                        time.sleep(wait)
                    # 401/403 认证失败 → 直接抛出
                    elif "401" in err_str or "403" in err_str:
                        raise RuntimeError(f"API Key 无效: {e}") from e
                    else:
                        if attempt < max_retries - 1:
                            wait = 2 ** attempt
                            print(f"[LLM] 调用失败，{wait}s 后重试... ({err_str[:60]})")
                            time.sleep(wait)
                        else:
                            break  # 换模型

        raise RuntimeError(f"所有模型均不可用，请稍后重试")

    def ask_json(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用 LLM 并解析 JSON 响应"""
        result = self.ask(messages, system=system, temperature=0.1)
        try:
            return extract_json_payload(result)
        except ValueError:
            return {"raw": result}


def _is_local_base_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


# 全局单例
llm = LLMClient()

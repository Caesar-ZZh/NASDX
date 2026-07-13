"""
LLM 客户端 — 支持 OpenAI 兼容接口（DeepSeek / Qwen / GPT-4o 等）
无需 API Key 也能通过 Ollama 本地模型运行
"""
import os
import json
import random
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

# 从环境变量读取；不要在仓库中写入真实 API Key。
API_KEY    = os.environ.get("NASDX_API_KEY", "")
BASE_URL   = os.environ.get("NASDX_BASE_URL", "https://api.deepseek.com")
MODEL_NAME = os.environ.get("NASDX_MODEL", "deepseek-chat")
MAX_TOKENS = int(os.environ.get("NASDX_MAX_TOKENS", "4096"))
TEMPERATURE = float(os.environ.get("NASDX_TEMPERATURE", "0.3"))
MAX_TOTAL_ATTEMPTS = int(os.environ.get("NASDX_LLM_MAX_ATTEMPTS", "5"))
MAX_ELAPSED_SECONDS = float(os.environ.get("NASDX_LLM_MAX_ELAPSED_SECONDS", "30"))
MAX_RETRY_DELAY_SECONDS = float(os.environ.get("NASDX_LLM_MAX_RETRY_DELAY_SECONDS", "30"))


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


def _default_fallback_models(base_url: str, model: str) -> List[str]:
    host = (urlparse(base_url).hostname or "").lower()
    if host == "api.deepseek.com" or str(model).lower().startswith("deepseek-"):
        return ["deepseek-reasoner", "deepseek-chat"]
    return []


def _configured_fallback_models(base_url: str, model: str) -> List[str]:
    configured = os.environ.get("NASDX_FALLBACK_MODELS")
    if configured is None:
        return _default_fallback_models(base_url, model)
    return [item.strip() for item in configured.split(",") if item.strip()]


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
    FALLBACK_MODELS = _configured_fallback_models(BASE_URL, MODEL_NAME)

    def ask(
        self,
        messages: List[Dict[str, str]],
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_retries: int = 3,
        max_total_attempts: int | None = None,
        max_elapsed_seconds: float | None = None,
    ) -> str:
        """Call the LLM with typed, bounded retries and explicit fallback logs."""
        if not API_KEY and not _is_local_base_url(BASE_URL):
            raise RuntimeError("请先设置 NASDX_API_KEY，或切换到 Ollama 本地模型")

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        models_to_try = _ordered_unique([self.model, *self.FALLBACK_MODELS])
        retries_per_model = max(1, int(max_retries))
        attempt_budget = max(1, int(max_total_attempts or MAX_TOTAL_ATTEMPTS))
        elapsed_budget = max(0.1, float(max_elapsed_seconds or MAX_ELAPSED_SECONDS))
        started_at = time.monotonic()
        total_attempts = 0
        last_classification = "none"

        for model_index, model in enumerate(models_to_try):
            for attempt in range(1, retries_per_model + 1):
                if total_attempts >= attempt_budget or time.monotonic() - started_at >= elapsed_budget:
                    break
                total_attempts += 1
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
                        print(f"[LLM] model={model} attempt={total_attempts} class=success fallback=true")
                    return content
                except Exception as exc:
                    classification = _classify_api_error(exc)
                    last_classification = classification
                    if classification == "authentication":
                        print(f"[LLM] model={model} attempt={total_attempts} class=authentication action=fail")
                        raise RuntimeError("API 认证或权限失败，请检查本地凭证配置") from None
                    if classification == "invalid_request":
                        print(f"[LLM] model={model} attempt={total_attempts} class=invalid_request action=fail")
                        raise RuntimeError("LLM 请求无效，请检查模型、上下文长度和请求参数") from None
                    if classification == "fatal":
                        print(f"[LLM] model={model} attempt={total_attempts} class=fatal action=fail")
                        raise RuntimeError("LLM 调用发生不可重试错误，请检查提供商配置") from None

                    can_retry_model = attempt < retries_per_model
                    can_retry_total = total_attempts < attempt_budget
                    if can_retry_model and can_retry_total:
                        wait = _retry_delay(exc, attempt)
                        elapsed = time.monotonic() - started_at
                        if elapsed + wait <= elapsed_budget:
                            print(
                                f"[LLM] model={model} attempt={total_attempts} "
                                f"class={classification} action=retry wait={wait:.2f}s"
                            )
                            time.sleep(wait)
                            continue
                    if model_index + 1 < len(models_to_try) and can_retry_total:
                        print(
                            f"[LLM] model={model} attempt={total_attempts} "
                            f"class={classification} action=fallback"
                        )
                    break

        raise RuntimeError(f"所有模型均不可用（错误分类：{last_classification}）")

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


def _ordered_unique(models: List[str]) -> List[str]:
    seen = set()
    ordered = []
    for model in models:
        normalized = str(model or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return int(status) if isinstance(status, int) else None


def _classify_api_error(exc: Exception) -> str:
    status = _status_code(exc)
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)) or status in {401, 403}:
        return "authentication"
    if isinstance(exc, BadRequestError) or status in {400, 404, 405, 413, 415, 422}:
        return "invalid_request"
    if isinstance(exc, RateLimitError) or status == 429:
        return "rate_limit"
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return "transient"
    if status in {408, 409, 425} or (status is not None and 500 <= status <= 599):
        return "transient"
    return "fatal"


def _retry_delay(exc: Exception, attempt: int) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    try:
        if retry_after is not None:
            return min(MAX_RETRY_DELAY_SECONDS, max(0.0, float(retry_after)))
    except (TypeError, ValueError):
        pass
    exponential = min(MAX_RETRY_DELAY_SECONDS, float(2 ** (attempt - 1)))
    jitter = random.uniform(0.0, min(1.0, exponential * 0.25))
    return min(MAX_RETRY_DELAY_SECONDS, exponential + jitter)


# 全局单例
llm = LLMClient()

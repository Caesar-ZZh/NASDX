"""
LLM 客户端 — 支持 OpenAI 兼容接口（DeepSeek / Qwen / GPT-4o 等）
无需 API Key 也能通过 Ollama 本地模型运行
"""
import os
import json
import math
import queue
import random
import re
import threading
import time
from dataclasses import dataclass
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
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOTAL_ATTEMPTS = 5
DEFAULT_MAX_ELAPSED_SECONDS = 30.0
DEFAULT_MAX_RETRY_DELAY_SECONDS = 30.0


class LLMConfigurationError(RuntimeError):
    """Raised on first LLM use when optional numeric settings are invalid."""


class LLMRequestTimeout(TimeoutError):
    """Raised when one provider request exceeds its remaining total budget."""


@dataclass(frozen=True)
class LLMSettings:
    max_tokens: int
    temperature: float
    max_total_attempts: int
    max_elapsed_seconds: float
    max_retry_delay_seconds: float


def load_llm_settings(environ: Dict[str, str] | None = None) -> LLMSettings:
    env = os.environ if environ is None else environ
    return LLMSettings(
        max_tokens=_parse_int_setting(env, "NASDX_MAX_TOKENS", DEFAULT_MAX_TOKENS, 1, 1_000_000),
        temperature=_parse_float_setting(env, "NASDX_TEMPERATURE", DEFAULT_TEMPERATURE, 0.0, 2.0),
        max_total_attempts=_parse_int_setting(env, "NASDX_LLM_MAX_ATTEMPTS", DEFAULT_MAX_TOTAL_ATTEMPTS, 1, 20),
        max_elapsed_seconds=_parse_float_setting(
            env, "NASDX_LLM_MAX_ELAPSED_SECONDS", DEFAULT_MAX_ELAPSED_SECONDS, 0.05, 3600.0
        ),
        max_retry_delay_seconds=_parse_float_setting(
            env, "NASDX_LLM_MAX_RETRY_DELAY_SECONDS", DEFAULT_MAX_RETRY_DELAY_SECONDS, 0.01, 600.0
        ),
    )


def _parse_int_setting(env: Dict[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = env.get(key, str(default))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise LLMConfigurationError(f"{key} 必须是 {minimum} 到 {maximum} 之间的整数") from None
    if not minimum <= value <= maximum:
        raise LLMConfigurationError(f"{key} 必须是 {minimum} 到 {maximum} 之间的整数")
    return value


def _parse_float_setting(
    env: Dict[str, str], key: str, default: float, minimum: float, maximum: float
) -> float:
    raw = env.get(key, str(default))
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        raise LLMConfigurationError(f"{key} 必须是 {minimum:g} 到 {maximum:g} 之间的有限数值") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise LLMConfigurationError(f"{key} 必须是 {minimum:g} 到 {maximum:g} 之间的有限数值")
    return value


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
            instance = super().__new__(cls)
            instance._init()
            cls._instance = instance
        return cls._instance

    def _init(self):
        settings = load_llm_settings()
        # 实时从环境变量读取，使 apply_provider / set_quick_think 在重置单例后生效。
        api_key = os.environ.get("NASDX_API_KEY", "")
        base_url = os.environ.get("NASDX_BASE_URL", "https://api.deepseek.com")
        model_name = os.environ.get("NASDX_MODEL", "deepseek-chat")
        self.client = OpenAI(api_key=api_key or "nasdx-local-placeholder", base_url=base_url)
        self.api_key = api_key
        self.base_url = base_url
        self.model = model_name
        self.max_tokens = settings.max_tokens
        self.temperature = settings.temperature
        self.max_total_attempts = settings.max_total_attempts
        self.max_elapsed_seconds = settings.max_elapsed_seconds
        self.max_retry_delay_seconds = settings.max_retry_delay_seconds
        # 按当前 base_url/model 实时计算备用模型，避免切换 provider 后
        # 仍回退到 import 时算死的 deepseek 模型列表。
        self.FALLBACK_MODELS = _configured_fallback_models(base_url, model_name)

    # 类级默认值（兼容旧引用）；实例会在 _init 中按当前 provider 覆盖。
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
        api_key = getattr(self, "api_key", API_KEY)
        base_url = getattr(self, "base_url", BASE_URL)
        if not api_key and not _is_local_base_url(base_url):
            raise RuntimeError("请先设置 NASDX_API_KEY，或切换到 Ollama 本地模型")

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        models_to_try = _ordered_unique([self.model, *self.FALLBACK_MODELS])
        retries_per_model = max(1, int(max_retries))
        attempt_budget = max(1, int(max_total_attempts or getattr(self, "max_total_attempts", DEFAULT_MAX_TOTAL_ATTEMPTS)))
        elapsed_budget = max(0.05, float(max_elapsed_seconds or getattr(self, "max_elapsed_seconds", DEFAULT_MAX_ELAPSED_SECONDS)))
        max_retry_delay = getattr(self, "max_retry_delay_seconds", DEFAULT_MAX_RETRY_DELAY_SECONDS)
        started_at = time.monotonic()
        total_attempts = 0
        last_classification = "none"

        for model_index, model in enumerate(models_to_try):
            for attempt in range(1, retries_per_model + 1):
                if total_attempts >= attempt_budget or time.monotonic() - started_at >= elapsed_budget:
                    break
                total_attempts += 1
                try:
                    remaining = elapsed_budget - (time.monotonic() - started_at)
                    if remaining <= 0:
                        last_classification = "elapsed_budget_exhausted"
                        print(
                            f"[LLM] model={model} attempt={total_attempts} "
                            "class=elapsed_budget_exhausted action=fail"
                        )
                        break
                    # 推理模型（如 deepseek-reasoner）temperature 必须为 1
                    is_reasoner = any(x in model for x in ("reasoner", "thinking", "r1"))
                    call_kwargs = dict(
                        model=model,
                        messages=full_messages,
                        max_tokens=self.max_tokens,
                        timeout=remaining,
                    )
                    if not is_reasoner:
                        call_kwargs["temperature"] = temperature if temperature is not None else self.temperature
                    resp = _call_with_deadline(
                        self.client.chat.completions.create,
                        call_kwargs,
                        timeout=remaining,
                    )
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
                        wait = _retry_delay(exc, attempt, max_retry_delay)
                        elapsed = time.monotonic() - started_at
                        if elapsed + wait <= elapsed_budget:
                            print(
                                f"[LLM] model={model} attempt={total_attempts} "
                                f"class={classification} action=retry wait={wait:.2f}s"
                            )
                            time.sleep(wait)
                            continue
                    has_time = time.monotonic() - started_at < elapsed_budget
                    if model_index + 1 < len(models_to_try) and can_retry_total and has_time:
                        print(
                            f"[LLM] model={model} attempt={total_attempts} "
                            f"class={classification} action=fallback"
                        )
                    break

        if time.monotonic() - started_at >= elapsed_budget:
            last_classification = "elapsed_budget_exhausted" if last_classification != "request_timeout" else last_classification
            print("[LLM] class=elapsed_budget_exhausted action=fail")
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
    if isinstance(exc, (LLMRequestTimeout, APITimeoutError, TimeoutError)):
        return "request_timeout"
    if isinstance(exc, APIConnectionError):
        return "transient"
    if status in {408, 409, 425} or (status is not None and 500 <= status <= 599):
        return "transient"
    return "fatal"


def _retry_delay(exc: Exception, attempt: int, max_delay: float = DEFAULT_MAX_RETRY_DELAY_SECONDS) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    try:
        if retry_after is not None:
            return min(max_delay, max(0.0, float(retry_after)))
    except (TypeError, ValueError):
        pass
    exponential = min(max_delay, float(2 ** (attempt - 1)))
    jitter = random.uniform(0.0, min(1.0, exponential * 0.25))
    return min(max_delay, exponential + jitter)


def _call_with_deadline(call, kwargs: Dict[str, Any], *, timeout: float):
    outcome: queue.Queue = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            outcome.put((True, call(**kwargs)))
        except BaseException as exc:
            outcome.put((False, exc))

    worker = threading.Thread(target=invoke, name="nasdx-llm-request", daemon=True)
    worker.start()
    worker.join(max(0.01, timeout))
    if worker.is_alive():
        raise LLMRequestTimeout("request exceeded remaining elapsed budget")
    ok, value = outcome.get_nowait()
    if ok:
        return value
    raise value


class _LLMCallCounters:
    """Process-wide LLM call counters used by the #65 performance contracts.

    Only calls routed through the shared ``llm`` proxy are counted; that is the
    seam every agent/environment uses, and it keeps counting valid when tests
    inject a fake client through ``LLMClient._instance``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.failures = 0

    def record(self, failed: bool) -> None:
        with self._lock:
            self.calls += 1
            if failed:
                self.failures += 1

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {"calls": self.calls, "failures": self.failures}

    def reset(self) -> None:
        with self._lock:
            self.calls = 0
            self.failures = 0


_LLM_COUNTERS = _LLMCallCounters()


def llm_counters() -> Dict[str, int]:
    """Return cumulative ``{"calls", "failures"}`` for the shared LLM proxy."""
    return _LLM_COUNTERS.snapshot()


def reset_llm_counters() -> None:
    """Reset the cumulative LLM proxy counters."""
    _LLM_COUNTERS.reset()


class LLMCallMeter:
    """Delta view over the global counters for a single measured section."""

    def __init__(self) -> None:
        self._start = _LLM_COUNTERS.snapshot()
        self._end: Dict[str, int] | None = None

    def stop(self) -> None:
        self._end = _LLM_COUNTERS.snapshot()

    def _current(self) -> Dict[str, int]:
        return self._end if self._end is not None else _LLM_COUNTERS.snapshot()

    @property
    def calls(self) -> int:
        return self._current()["calls"] - self._start["calls"]

    @property
    def failures(self) -> int:
        return self._current()["failures"] - self._start["failures"]


class _LazyLLMClient:
    def __init__(self):
        self._client: LLMClient | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> LLMClient:
        # 始终以 LLMClient._instance 为准：apply_provider / set_quick_think
        # 会把 _instance 置 None，若这里缓存旧实例会导致 provider 切换静默失效。
        client = LLMClient._instance
        if client is None:
            with self._lock:
                client = LLMClient._instance
                if client is None:
                    client = LLMClient()
        self._client = client
        return client

    def ask(self, *args, **kwargs):
        failed = True
        try:
            result = self._get_client().ask(*args, **kwargs)
            failed = False
            return result
        finally:
            _LLM_COUNTERS.record(failed)

    def ask_json(self, *args, **kwargs):
        # 计一次：LLMClient.ask_json 内部调用的是 LLMClient.ask（不经过本代理），
        # 因此这里不会与 ask() 的计数重复。
        failed = True
        try:
            result = self._get_client().ask_json(*args, **kwargs)
            failed = False
            return result
        finally:
            _LLM_COUNTERS.record(failed)


# ---------------------------------------------------------------------------
# Provider 工厂（TradingAgents 借鉴：工厂模式 + 快慢分层）
# 通过环境变量桥接现有 LLMClient，不重写核心；默认不启用，高可逆。
# 用法：apply_provider("qwen") / set_quick_think(True) 切换低成本模型。
# ---------------------------------------------------------------------------
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "deepseek": {"base_url": "https://api.deepseek.com", "default_model": "deepseek-chat",
                 "quick_model": "deepseek-chat", "needs_key": True},
    "openai":   {"base_url": "https://api.openai.com/v1", "default_model": "gpt-4o-mini",
                 "quick_model": "gpt-4o-mini", "needs_key": True},
    "qwen":     {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "default_model": "qwen-plus", "quick_model": "qwen-turbo", "needs_key": True},
    "ollama":   {"base_url": "http://localhost:11434/v1", "default_model": "qwen2.5:7b",
                 "quick_model": "qwen2.5:3b", "needs_key": False},
}
QUICK_THINK_PROVIDERS = ("ollama", "qwen", "deepseek")


def resolve_provider(name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    name = (name or os.environ.get("NASDX_PROVIDER", "")).strip().lower()
    if not name:
        return None
    if name not in PROVIDERS:
        raise ValueError(f"未知 provider: {name}，可选 {list(PROVIDERS)}")
    return PROVIDERS[name]


def _validate_provider_base_url(url: str) -> None:
    """仅允许 http/https 的 base_url，阻止 env 注入非法端点。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"provider base_url 非法: {url}")


def apply_provider(name: Optional[str] = None) -> None:
    """按 provider 名称设置环境变量并重置 LLM 单例（不改动 LLMClient 内部）。"""
    normalized = (name or os.environ.get("NASDX_PROVIDER", "")).strip().lower()
    prov = resolve_provider(normalized)
    if prov is None:
        return
    _validate_provider_base_url(prov["base_url"])
    # 记录当前 provider，保证 set_quick_think 在显式传名时也能工作。
    os.environ["NASDX_PROVIDER"] = normalized
    os.environ["NASDX_BASE_URL"] = prov["base_url"]
    os.environ["NASDX_MODEL"] = prov["default_model"]
    LLMClient._instance = None


def set_quick_think(enabled: bool = True) -> None:
    """切换低成本 quick_think 模型（快慢分层路由）。"""
    name = os.environ.get("NASDX_PROVIDER", "").strip().lower()
    prov = resolve_provider(name) if name else None
    if not prov:
        return
    os.environ["NASDX_MODEL"] = prov["quick_model"] if enabled else prov["default_model"]
    LLMClient._instance = None


def resolve_request_provider(config: Dict[str, Any] | None = None, **kwargs):
    """Resolve one API/CLI/MCP request without mutating the legacy singleton."""
    from nasdx.llm_providers import resolve_provider_config

    return resolve_provider_config(config, **kwargs)


def stream_provider_chat(config: Dict[str, Any] | None, messages, **kwargs):
    """Stream request-scoped provider events while keeping old ``llm`` calls intact."""
    from nasdx.llm_providers import stream_chat_events

    return stream_chat_events(config, messages, **kwargs)


llm = _LazyLLMClient()

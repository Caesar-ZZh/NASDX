"""Request-scoped LLM provider routing for API, local CLI, and MCP hosts.

Credentials are accepted from the request or the documented NASDX_/LLM_
environment variables.  This module never writes them to disk or mutates the
process environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlparse

from openai import OpenAI


class ProviderConfigurationError(ValueError):
    """A provider request is incomplete or names an unsupported route."""


@dataclass(frozen=True)
class ProviderPreset:
    base_url: str
    default_model: str
    needs_key: bool = True


@dataclass(frozen=True)
class ResolvedProvider:
    mode: str
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    executable: str = ""

    def public_dict(self) -> dict[str, Any]:
        """Return diagnostics safe to show in a UI or MCP response."""
        return {
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "configured": bool(
                self.mode in {"cli", "mcp"} or self.api_key or _is_local_url(self.base_url)
            ),
        }


API_PROVIDERS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset("https://api.deepseek.com", "deepseek-chat"),
    "openai": ProviderPreset("https://api.openai.com/v1", "gpt-4o-mini"),
    "qwen": ProviderPreset("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "ollama": ProviderPreset("http://localhost:11434/v1", "qwen2.5:7b", needs_key=False),
}

# Commands and arguments are fixed.  User content is always delivered on stdin;
# it is never interpolated into a shell command.
CLI_PROVIDERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "claude": (("claude", "openclaude"), ("-p", "--output-format", "text")),
    "codex": (("codex",), ("exec", "--skip-git-repo-check", "-")),
    "qwen": (("qwen",), ("-p",)),
}

_KEY_ENV = ("NASDX_API_KEY", "LLM_API_KEY")
_BASE_ENV = ("NASDX_BASE_URL", "LLM_BASE_URL")
_MODEL_ENV = ("NASDX_MODEL", "LLM_MODEL")
_PROVIDER_ENV = ("NASDX_PROVIDER", "LLM_PROVIDER")


def resolve_provider_config(
    request: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> ResolvedProvider:
    """Resolve one request without persisting its key or changing global state."""
    cfg = dict(request or {})
    env = os.environ if environ is None else environ
    provider = _first_text(cfg, "provider") or _first_env(env, _PROVIDER_ENV) or "deepseek"
    provider = provider.lower()

    if provider == "mcp":
        return ResolvedProvider(mode="mcp", provider="mcp")

    if provider.startswith("cli-"):
        kind = provider[4:]
        spec = CLI_PROVIDERS.get(kind)
        if spec is None:
            raise ProviderConfigurationError(
                f"不支持的 CLI provider：{kind}；可选 {', '.join(sorted(CLI_PROVIDERS))}"
            )
        executable = next((path for name in spec[0] if (path := which(name))), None)
        if not executable:
            raise ProviderConfigurationError(f"未检测到已安装并登录的 {kind} CLI")
        return ResolvedProvider(mode="cli", provider=kind, model=kind, executable=executable)

    preset = API_PROVIDERS.get(provider)
    if preset is None and provider != "custom":
        raise ProviderConfigurationError(
            f"未知 API provider：{provider}；可选 {', '.join(sorted(API_PROVIDERS))} 或 custom"
        )

    base_url = (
        _first_text(cfg, "base_url", "baseURL")
        or _first_env(env, _BASE_ENV)
        or (preset.base_url if preset else "")
    )
    model = (
        _first_text(cfg, "model")
        or _first_env(env, _MODEL_ENV)
        or (preset.default_model if preset else "")
    )
    api_key = _first_text(cfg, "api_key", "apiKey") or _first_env(env, _KEY_ENV)

    _validate_base_url(base_url)
    if not model:
        raise ProviderConfigurationError("缺少模型名称")
    needs_key = preset.needs_key if preset else not _is_local_url(base_url)
    if needs_key and not api_key:
        raise ProviderConfigurationError("缺少 API Key；请通过请求或 NASDX_API_KEY/LLM_API_KEY 提供")
    return ResolvedProvider(
        mode="api",
        provider=provider,
        model=model,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
    )


def stream_chat_events(
    request: Mapping[str, Any] | None,
    messages: Sequence[Mapping[str, str]],
    *,
    context: str = "",
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] = OpenAI,
    cli_runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> Iterator[dict[str, Any]]:
    """Yield ``delta``/``done``/``error`` events without leaking credentials."""
    try:
        resolved = resolve_provider_config(request, environ=environ, which=which)
        normalized = _normalize_messages(messages, context=context)
        if resolved.mode == "api":
            yield from _stream_api(resolved, normalized, client_factory=client_factory)
        elif resolved.mode == "cli":
            yield from _run_cli(resolved, normalized, runner=cli_runner)
        else:
            yield {
                "type": "done",
                "mode": "mcp",
                "attachment": mcp_attachment_config(),
            }
    except ProviderConfigurationError as exc:
        yield {"type": "error", "code": "configuration", "message": str(exc)}
    except Exception as exc:  # Runtime failures become stream events, not endpoint crashes.
        yield {
            "type": "error",
            "code": "provider_runtime",
            "message": f"模型调用失败（{type(exc).__name__}）",
        }


def mcp_attachment_config(python_executable: str | None = None) -> dict[str, Any]:
    """Return a stdio MCP attachment descriptor; it contains no credentials."""
    executable = python_executable or sys.executable
    return {
        "mcpServers": {
            "nasdx-llm": {
                "command": executable,
                "args": ["-m", "nasdx.llm_providers", "--mcp"],
            }
        }
    }


def _stream_api(
    resolved: ResolvedProvider,
    messages: list[dict[str, str]],
    *,
    client_factory: Callable[..., Any],
) -> Iterator[dict[str, Any]]:
    client = client_factory(
        api_key=resolved.api_key or resolved.provider,
        base_url=resolved.base_url,
    )
    response = client.chat.completions.create(
        model=resolved.model,
        messages=messages,
        temperature=0.3,
        stream=True,
    )
    for chunk in response:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        text = getattr(delta, "content", None) if delta is not None else None
        if text:
            yield {"type": "delta", "text": text}
    yield {"type": "done", "mode": "api", "provider": resolved.provider, "model": resolved.model}


def _run_cli(
    resolved: ResolvedProvider,
    messages: list[dict[str, str]],
    *,
    runner: Callable[..., Any],
) -> Iterator[dict[str, Any]]:
    _bins, args = CLI_PROVIDERS[resolved.provider]
    prompt = "\n\n".join(f"{item['role']}: {item['content']}" for item in messages)
    with tempfile.TemporaryDirectory(prefix="nasdx-llm-") as temp_dir:
        completed = runner(
            [resolved.executable, *args],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=temp_dir,
            timeout=300,
            shell=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"{resolved.provider} CLI 退出码 {completed.returncode}")
    output = (completed.stdout or "").strip()
    if output:
        yield {"type": "delta", "text": output}
    yield {"type": "done", "mode": "cli", "provider": resolved.provider}


def _normalize_messages(messages: Sequence[Mapping[str, str]], *, context: str) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if context.strip():
        normalized.append({"role": "system", "content": f"当前页面客观数据：\n{context.strip()}"})
    for item in messages:
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    if not normalized or not any(item["role"] == "user" for item in normalized):
        raise ProviderConfigurationError("messages 至少需要一条非空 user 消息")
    return normalized


def _validate_base_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderConfigurationError("缺少合法的 Base URL（仅支持 http/https）")


def _is_local_url(value: str) -> bool:
    host = (urlparse(value).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _first_text(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _first_env(source: Mapping[str, str], keys: Iterable[str]) -> str:
    return _first_text(source, *keys)


def _mcp_result(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def handle_mcp_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Handle the minimal MCP lifecycle and a credential-safe provider-status tool."""
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        protocol = (message.get("params") or {}).get("protocolVersion", "2024-11-05")
        return _mcp_result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "nasdx-llm", "version": "1.0"},
            },
        )
    if method == "ping":
        return _mcp_result(request_id, {})
    if method == "tools/list":
        return _mcp_result(
            request_id,
            {
                "tools": [
                    {
                        "name": "llm_provider_status",
                        "description": "列出 NASDX 可用的 API 与本机 CLI 路由；不返回凭据。",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    }
                ]
            },
        )
    if method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != "llm_provider_status":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "未知工具"},
            }
        status = {
            "api": sorted(API_PROVIDERS),
            "cli": {name: any(shutil.which(binary) for binary in bins) for name, (bins, _args) in CLI_PROVIDERS.items()},
        }
        return _mcp_result(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(status, ensure_ascii=False)}], "isError": False},
        )
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "未知方法"}}


def run_mcp_stdio() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_mcp_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__" and "--mcp" in sys.argv:
    run_mcp_stdio()

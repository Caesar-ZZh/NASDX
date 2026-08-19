# LLM 多模型接入

NASDX 保留原有 `nasdx.llm.llm` 调用，同时提供请求级路由：API、已登录的本机 CLI、MCP。
请求中的凭据只用于当次调用，不写配置文件，也不修改进程环境。

## API

```python
from nasdx.llm import stream_provider_chat

events = stream_provider_chat(
    {"provider": "deepseek", "apiKey": user_supplied_key},
    [{"role": "user", "content": "请整理当前页面数据"}],
    context=page_context,
)
```

内置 provider 为 `deepseek`、`openai`、`qwen`、`ollama`，会自动填充 Base URL 和默认模型。
自定义 OpenAI 兼容端点使用 `provider=custom`，并随请求传入 `baseURL`、`model`、`apiKey`。
也可使用既有 `NASDX_*` 或对应 `LLM_*` 环境变量；Ollama 本机端点不要求 key。

## 本机 CLI 订阅

使用 `cli-claude`、`cli-codex` 或 `cli-qwen`。NASDX 只调用 PATH 中已安装、已登录的固定命令，
不接受自定义可执行文件或参数，提示词通过 stdin 传入。CLI 不具备 NASDX API 模式的工具调用能力，
因此调用方应把页面客观数据放入 `context`。

## MCP

```python
from nasdx.llm_providers import mcp_attachment_config

print(mcp_attachment_config())
```

返回值可合并到支持 stdio MCP 的客户端配置。该 MCP 服务只报告 provider/CLI 可用性，
不会暴露 API Key；模型调用仍由 API 或 CLI 路由完成。

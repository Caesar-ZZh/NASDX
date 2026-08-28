# NASDX 子代理协作工作流

## 目标

用五个窄边界子代理降低长线程上下文污染，让 NASDX 的实现、审计、网页验证和交付收口可以重复执行。

## 五个子代理

| 子代理 | 主要用途 | 默认是否可写 |
|---|---|---|
| 上游方案拆解代理 | 审阅外部 skill、API、投研框架，并映射到 NASDX 模块 | 否 |
| 单功能实现代理 | 按一个明确需求做 TDD 实现和局部验证 | 是，限授权文件 |
| 契约审计代理 | 审查 `tests/`、`scripts/run_final_audit.py`、业务边界和安全风险 | 否 |
| Streamlit 验证代理 | 用真实页面检查入口、状态边界、控制台错误和截图 | 否 |
| 交付收口代理 | 同步 `README.md`、`CONTEXT.md`、`CHANGELOG.md` 和最终摘要 | 是，限文档 |

## 推荐顺序

1. 主会话拆任务，明确允许修改文件、禁止事项和验收命令。
2. 上游方案拆解代理先只读判断外部方案是否值得接入。
3. 单功能实现代理一次只做一个模块。
4. 契约审计代理检查实现是否符合测试、最终审计和投资边界。
5. Streamlit 验证代理检查真实页面。
6. 交付收口代理同步文档和 `CONTEXT.md`。
7. 主会话运行 `python -B scripts/run_product_readiness.py` 或直接运行 `python -B -m unittest discover -s tests`、`python -B scripts/run_final_audit.py`。

## 自动化边界

- 每两小时巡检可以运行 `scripts/run_product_readiness.py` 做测试、最终审计和可选 LLM smoke。
- API Key 只能来自环境变量，例如 `NASDX_API_KEY`；不写入文件、不写入 git、不写入自动化 prompt。
- 自动巡检默认只报告问题。若需要自动改代码，应使用独立分支或 worktree，并由人工复核后再 PR。
- PR 前必须重新运行 `scripts/run_product_readiness.py`，并确认 `scripts/run_final_audit.py` 通过。

## 验收入口

| 命令 | 用途 |
|---|---|
| `python -B -m unittest discover -s tests` | 单元和契约测试 |
| `python -B scripts/run_final_audit.py` | 最终版交付审计 |
| `python -B scripts/run_product_readiness.py` | 产品化巡检聚合入口 |
| `python -B scripts/run_product_readiness.py --llm-smoke` | 在环境变量提供 API Key 时追加 LLM 验证 |

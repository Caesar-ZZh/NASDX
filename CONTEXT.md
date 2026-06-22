# CONTEXT

- 当前：新增 5 个 `.claude/agents` 子代理模板、`docs/SUBAGENT_WORKFLOW.md` 和 `run_product_readiness.py` 产品化巡检入口；已创建两小时自动化 `nasdx`。
- 上次停在：已切到分支 `codex/productization-subagents`；`run_product_readiness.py` 通过，单测 15/0、最终审计 20/0；带 DeepSeek 环境变量的 `--llm-smoke` 通过；`git diff --check` 仅 CRLF 警告。
- 关键决定：API Key 只通过环境变量参与验证，不写入文件、自动化 prompt 或提交；自动循环默认只审计/报告，自动改代码需另行确认隔离分支或 worktree。
- 原因：当前工作树改动大且在 `master`，创建分支/PR/长期自动化属于高风险动作，按项目规则需用户确认后再执行。

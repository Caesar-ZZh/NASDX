# CONTEXT

- 当前：本轮手动运行一次产品化巡检并修复交付资产问题；`requirements_nasdx.txt` 已补核心依赖并解除忽略，最终审计新增“依赖清单”检查。
- 上次停在：分支 `codex/productization-subagents`；`run_product_readiness.py` 通过，单测 17/0、最终审计 21/0；带 DeepSeek 环境变量的 `--llm-smoke` 通过，3/0。
- 关键决定：依赖安装入口统一为 `pip install -r requirements_nasdx.txt`；API Key 只通过环境变量参与验证，不写入文件、自动化 prompt 或提交。
- 原因：README 已把 requirements 作为安装入口，依赖清单必须可入库且被最终审计覆盖，否则自动化和新环境部署会漏依赖。

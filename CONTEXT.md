# CONTEXT

- 当前：根据外部 `NASDX深度审阅.md` 落地仍适用的小范围修复：默认模型改 `deepseek-chat`，移除内置私人中转 preset，Agent 旧文本信号解析收口到 `BaseAgent`，`vnpy_bridge` 去掉强制 `ImportError` 占位并保留 pandas fallback。
- 上次停在：全量 pytest、ruff、轻量安全扫描、desktop doctor、launcher dry-run 和 selector workflow dry-run 均通过；待提交并推送 GitHub。
- 关键决定：以当前真实仓库为准处理审阅项；报告里“无 tests/pyproject/CI”等过时结论不作为修改依据。
- 原因：本轮优先修安全/配置/DRY/占位问题，不重写 Streamlit、不删除 CLI、不展开 UI 组件抽取和报告路径重构。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

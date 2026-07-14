# CONTEXT

- 当前：GitHub issue #26 的计划页 HTML 表格抽离已完成代码、全量门禁和真实页面验证；下一阶段处理浏览器发现的 sidebar 空颜色警告 issue #27。
- 上次停在：10 个表格 helper 已移入 `nasdx/ui/plan_tables.py`，`app.py` 减少约 380 行；pytest 208/208、最终审计 22/22、桌面发布 11/11、安全扫描 1/1、Ruff 均通过。
- 关键决定：入口页保留原 helper 别名和调用点；共享模块不依赖 Streamlit，普通单元格统一转义，颜色/代码/安全链接只通过内部受控 HTML 类型输出。
- 原因：重复拼接表头、表体、空状态和富文本会放大维护成本及 HTML 注入回归风险，且内联函数会在每次 rerun 中重新定义。
- 浏览器证据：`/?page=plan` 渲染 10 张 `.plan-table` 表格，Streamlit 异常 0、控制台错误 0；9 条 sidebar 主题空颜色警告不属于本轮代码，已进入 #27。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

# CONTEXT

- 当前：修复“今日选股”启动后状态丢失；扫描与深度分析改用 fragment 轮询，任务注册表迁入 `nasdx.ui_tasks`；新增 Agnes AI / `agnes-2.0-flash` 预设。
- 上次停在：pytest 139/139、ruff、`run_final_audit.py` 22/22、desktop doctor 和安全扫描通过；Playwright 验证扫描状态跨多次刷新保持、Agnes UI 已连接、浏览器错误 0；8502 已加载本地用户配置。
- 关键决定：Streamlit session 只保存 task_id，进程内任务表由独立模块持久化；Agnes 密钥只存用户目录 `config.toml`，仓库仅保存公开端点和模型名。
- 原因：整页 JS 刷新会创建新 session，且 `app.py` 每次 rerun 会重建模块级任务表；模型密钥不能进入源码或 Git 历史。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

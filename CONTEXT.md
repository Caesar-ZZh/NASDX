# CONTEXT

- 当前：GitHub issue #27 已通过 CI、回复并关闭；issue #28 的 Node 20 action 弃用警告也已修复并通过无注解 CI，待文档收口、回复和关闭。
- 上次停在：`.github/workflows/windows-desktop.yml` 已升级到 `actions/checkout@v5` 和 `actions/setup-python@v6`；pytest 210/210、最终审计 22/22、Windows CI 均通过，check-run annotations 为 `[]`。
- 关键决定：只升级官方 action 主版本，保留 `windows-latest`、Python 3.11.9、密钥扫描、依赖锁校验和桌面发布命令不变。
- 原因：#27 的成功 CI run 仍提示 Node.js 20 已弃用，并由 GitHub 强制转换到 Node 24；官方 Node 24 action 已消除兼容路径和注解噪音。
- 前序证据：Streamlit 1.59.2 下 `/?page=plan` 在 1440x900 和 390x844 正常渲染，控制台均为 0 errors / 0 warnings；pytest 210/210、最终审计 22/22、桌面发布 11/11。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

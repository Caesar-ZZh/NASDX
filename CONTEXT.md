# CONTEXT

- 当前：GitHub issue #27 的 Streamlit 侧边栏空颜色控制台警告已完成根因修复、全量门禁和真实浏览器回归，待提交推送、CI 和关闭 issue。
- 上次停在：运行依赖限定为 `Streamlit >=1.59.2,<1.60.0`，兼容约束和两个 Windows 哈希锁文件同步固定到 `1.59.2`；pytest 210/210、最终审计 22/22、桌面发布 11/11、安全扫描 1/1、Ruff 均通过。
- 关键决定：不向 `.streamlit/config.toml` 添加无效占位颜色，也不在业务代码里过滤警告；修复当前 Streamlit 1.52.2 的前后端主题协议缺陷，并让开发与发布依赖保持一致。
- 原因：1.52.2 即使显式传入全部官方 sidebar 颜色仍产生 36 条警告；同一页面在 1.59.2 下为 0 errors / 0 warnings。
- 浏览器证据：`/?page=plan` 在 1440x900 和 390x844 下正常渲染，暗色侧边栏和移动布局保持，控制台均为 0 errors / 0 warnings。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

# CONTEXT

- 当前：修复 GitHub issues #6/#7/#8：新增 runtime path helpers 和报告历史服务，Streamlit/CLI/扫描/投研报告模块尊重 `NASDX_REPORTS_DIR`，数据快照尊重 `NASDX_DATA_DIR`/`NASDX_RUNTIME_DIR`，Stocks60 与 selector 扫描不再阻塞主 UI。
- 上次停在：全量 pytest 128 个通过，ruff 通过，`run_final_audit.py` 22/22 通过，desktop doctor PASS；待提交、推送，并在 GitHub issues 回复验证结果后关闭。
- 关键决定：保留源码 checkout 默认目录兼容；仅在 launcher/env 指定 runtime/reports/data 时切换写入位置；`plan` 路由升级为投研工作台但不重写 Streamlit。
- 原因：本轮集中关闭 P1 的桌面运行路径、扫描卡顿和投研闭环入口问题，不触碰交易、策略重写或 UI 框架迁移。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

# CONTEXT

- 当前：第一轮系统审计文档已新增：`PERFORMANCE_AUDIT.md`、`STRATEGY_AUDIT.md`、`PRODUCT_FLOW_AUDIT.md`、`DESKTOP_RELEASE_AUDIT.md`、`ARCHITECTURE_AUDIT.md`、`TEST_COVERAGE_AUDIT.md`、`MASTER_AUDIT.md`；未改业务代码。
- 上次停在：待验证、提交、推送，并把 P0/P1 审计问题登记到 GitHub Issues。
- 关键决定：第一轮只做审计和文档；继续保留 Streamlit 和现有 CLI，不重写 `app.py`，不迁移 Electron/Tauri/PySide6。
- 原因：审计确认桌面 MVP 基本成熟，但策略可信度、报告/runtime 路径、性能阻塞和产品闭环仍需分阶段修复；当前正式 portable 包 release evidence 因 `.venv` 内 `__pycache__` 失败，属于发布前 P0。

## Desktop Packaging

- 当前：Milestone 8.34 已完成；本机 `D:\Inno Setup 7\ISCC.exe` 已被自动发现，真实 installer roundtrip 已通过并写入 proof；当前 Python 环境已安装 `pywebview 6.2.1`，`run_desktop_doctor.py --json` 和 `run_desktop_completion_audit.py --strict` 的 optional WebView 检查均为 PASS。
- 关键决定：ISCC 自动发现覆盖 PATH、Inno Setup 7/6 常见目录和 Windows 卸载注册表；full package 在依赖安装后保留 `.venv`，但二次清理 `__pycache__/`、`*.pyc`、`*.pyo`；Inno 卸载器删除 app-owned `{app}`，用户配置/报告/历史库仍留在外部。
- 原因：不能靠手工 `-IsccPath` 才能发布；发行包和安装包都不能带 Python 缓存、报告、日志、密钥或本地配置，卸载也不能残留运行后生成的 `.venv` 缓存；仍不改 `app.py` 和投研业务逻辑。

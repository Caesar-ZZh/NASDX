# CONTEXT

- 当前：Milestone 8.34 已完成；Inno Setup 7 的 ISCC 自动发现、安装包编译、真实安装/启动烟测/卸载回环均已验证通过，`streamlit run app.py` 临时端口探活返回 HTTP 200。
- 上次停在：安装包 `dist\installer\NASDX-Desktop-Setup.exe` 哈希为 `1583641be78bb3130801ecb218a0f707c9cbab16844cdbf4069c59a4512ba995`，portable zip 哈希为 `4db117b15d000fe4c55bfe3730fb972b8d229e5e961f4f3c8444320590371f81`。
- 关键决定：继续保留 Streamlit 和现有 CLI，桌面化先走 launcher + portable zip + Inno Setup installer；selector 只能按独立模块/页面方式渐进迁移，不能覆盖当前投研 workflow。
- 原因：当前目标是可交付 Windows 桌面入口和可复现打包链路；不重写 `app.py`，避免破坏既有工作流。已修正当前树里 selector workflow 把 `stock_code=None` 传给深度分析的问题：dry-run 显示占位候选，真实运行从 selector latest 报告选择首个候选，无候选则安全停止。

## Desktop Packaging

- 当前：Milestone 8.34 已完成；本机 `D:\Inno Setup 7\ISCC.exe` 已被自动发现，真实 installer roundtrip 已通过并写入 proof；当前 Python 环境已安装 `pywebview 6.2.1`，`run_desktop_doctor.py --json` 和 `run_desktop_completion_audit.py --strict` 的 optional WebView 检查均为 PASS。
- 关键决定：ISCC 自动发现覆盖 PATH、Inno Setup 7/6 常见目录和 Windows 卸载注册表；full package 在依赖安装后保留 `.venv`，但二次清理 `__pycache__/`、`*.pyc`、`*.pyo`；Inno 卸载器删除 app-owned `{app}`，用户配置/报告/历史库仍留在外部。
- 原因：不能靠手工 `-IsccPath` 才能发布；发行包和安装包都不能带 Python 缓存、报告、日志、密钥或本地配置，卸载也不能残留运行后生成的 `.venv` 缓存；仍不改 `app.py` 和投研业务逻辑。

# CONTEXT

- 当前：GitHub issue #14-#20 的代码修复已完成，覆盖 ETF 扫描发布门禁、HTML/URL 安全、Windows 依赖锁、历史库事务、CSV 注入、快照校验与 LLM 重试预算。
- 上次停在：全量 pytest、ruff、安全扫描、`run_final_audit.py` 22/22、desktop release check 10/10、依赖锁检查 2/2 均通过。
- 关键决定：不发布低于 80% 覆盖率的 ETF 扫描；历史记录采用单份规范 payload + 外键事务；快照先校验再原子替换；Windows 构建固定 Python/pip/uv 和带哈希依赖锁；LLM 重试按错误类型、次数和总耗时共同限流。
- 原因：把失败显式化并在发布、落盘和外部调用边界阻断，可同时减少错误结果、重复数据、界面卡顿和供应链漂移。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

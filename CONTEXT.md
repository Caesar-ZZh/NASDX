# CONTEXT

- 当前：GitHub issue #25 的选股历史快路径已实现并通过完整验证，待提交、推送和 issue 回执。
- 上次停在：无磁盘缓存的 30 股历史从腾讯 34-58 秒降至 8.22 秒；真实完整选股覆盖 5528 只股票并在 32.1 秒内产出报告，30 股历史因子阶段为 6.8 秒。
- 关键决定：沪深股票和指数使用单个 `TdxHqClient` 顺序获取前复权日线，北交所与缺失项并行走腾讯补缺；新增依赖必须进入 Windows 哈希锁。
- 原因：20 路逐股 HTTP 会触发数据源限流，单请求被放大到 17-31 秒；复用通达信连接比继续加线程更快、更稳。
- 验证：pytest 202/202、最终审计 22/22、桌面发布检查 11/11、依赖锁 2/2、安全扫描 1/1、Ruff 全部通过。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

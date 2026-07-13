# CONTEXT

- 当前：网页“今日选股”和 ETF50 超时/失败已修复；GitHub issue #21、#22 的 LLM 请求时限与数值配置校验已实现。
- 上次停在：真实浏览器已完成今日选股和 ETF50 按钮到结果的闭环验证，ETF 覆盖 50/50；pytest 189/189、final audit 22/22、desktop release check 10/10 均通过，待推送并关闭 issues。
- 关键决定：交互扫描统一走腾讯批量报价和直接 K 线接口，缺失项有界重试；股票列表缓存 7 天，历史 K 线缓存 10 分钟；LLM 配置延迟校验且每次底层请求受剩余总预算约束。
- 原因：原 AkShare 接口已移除且 Eastmoney 请求会无界阻塞；把超时、并发、缓存放在统一边界能同时避免崩溃、空报告和重复等待。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

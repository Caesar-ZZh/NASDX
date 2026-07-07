# CONTEXT

- 当前：根据第一轮审计落地 P0 修复：Backtester 信号只看执行日前历史数据，ETF50 量化回测改为滚动 TopN 调仓，桌面 release gate 改为产物生成后再跑 evidence。
- 上次停在：P0 修复已通过全量 pytest、ruff、doctor、completion audit、release gate 快速门禁和正式包污染回归；已推送并关闭 GitHub Issues #3、#4、#5。
- 关键决定：先修策略可信度和发布证据阻塞；暂不展开 P1 的报告路径统一、selector 性能和产品工作台重构。
- 原因：P0 会直接影响回测可信度和正式发布门禁；P1 涉及 UI/路径/服务层，需分阶段做，避免一次改动过散。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

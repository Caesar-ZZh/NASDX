# CONTEXT

- 当前：修复 issue #13：`scan_and_sync.py` 不再切换当前分支，改由 `nasdx.cloud_sync` 用独立临时 clone、跨进程锁和 ETF50 严格白名单发布；补强 issue #12 的无 Key 前置失败与 CI 密钥扫描。
- 上次停在：pytest 152/152、ruff、`run_final_audit.py` 22/22、安全扫描、desktop release check 10/10 和 GitHub Actions 全通过；待合并 PR #1、关闭 #13，并在 #12 记录仍需供应商撤销旧密钥及授权后才能重写历史。
- 关键决定：只发布最新 `etf50_YYYYMMDD_HHMM.json`，提交前校验 schema、2 MB 上限、6 小时时效和递归敏感字段；任何 Git 失败均返回非零。
- 原因：原脚本原地 checkout、通配符 `git add -f`、无并发锁且吞掉 push 失败，会干扰用户工作树并可能误上传报告；已进入历史的密钥不能靠删除当前代码完成撤销。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

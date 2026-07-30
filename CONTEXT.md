# CONTEXT

- 当前（2026-07-30 巡检 run 10）：#64 已修复（P1 回测正确性/配置安全）：`Backtester` 的执行/成本参数此前只校验 initial_capital，其余直接赋值——slippage=-2 会算出负执行价让恒定价回测净值 10万→30万→70万（凭空造钱），commission_rate=-1 等于全额返现，min_shares=0 下单时才抛 ZeroDivisionError，NaN 滑点跑到 int() 才崩。新增集中式 `validate_backtester_config()` 在 `__init__` fail-fast：费率/滑点必须有限且 0<=x<1，min_shares 正整数（拒 bool/float/str），max_stale_days 非负整数，normalize_weights 严格 bool；类文档写明参数契约表。合法默认值与自定义配置结果逐位不变。测试 tests/test_backtest_correctness_p1 76/76（+17 新用例）。剩余开放：仅 #61（待 PR #54 合并自动关，勿重复动）。
- 历史（run 9）：#63 已修复并关闭（commit f0f30ab）：决策记忆改 NASDX_MEMORY_ENABLED opt-in，关闭时 recall 返回 []；summary 复用 sanitize_text() 脱敏 + 截断；NASDX_MEMORY_MAX_RECORDS 条数淘汰；clear_memory/memory_status + CLI。测试 test_memory_privacy 19/19。
- 历史（run 8）：#62 已修复并关闭（commit 54a1c53 已推送 master）：决策日志改 opt-in 默认关闭（NASDX_DECISION_LOG=1 显式开启）；写盘前递归脱敏（敏感键→[REDACTED]，字符串内 Bearer/sk-/key=value 抹除，可用 NASDX_DECISION_LOG_REDACT_KEYS 扩展）；弃用 repr()（未知对象只记类型摘要）；5MB 单备份轮转。测试 tests/test_decision_log_privacy.py 14/14 + 存量契约 28/28。剩余开放：#61（待 PR #54 合并自动关）、#63（P2 决策记忆保留策略，下轮建议）。
- 历史（run 7）：#61 已修复（commit 07f32d4，落在 PR #54 分支 feat/audit-harness——audit_loop.py 只存在于该分支，master 无此文件）：phase_fix 不再在 PR 创建时关 Issue；新增 phase_verify（PR 合并+merge commit 可达默认分支才标 fixed+关闭；closed-unmerged 清状态可重试并重开误关 Issue）；_apply_and_test 全链路 git 返回码检查；状态 prs/fixed 分层+旧 schema 自动迁移。测试 tests/test_audit_loop_lifecycle.py 23/23（全 mock gh/git）。PR #54 body 已挂 Closes #61，#61 保持开放待 PR 合并。剩余开放：#62、#63（P2）、PR #53/#54。run 6 已补账推送 6f06815/0c93c4f/d30f734 并关闭 #43/#45/#48。
- 历史（run 5）：#45 已修复（fail-fast 权重校验 + opt-in 归一化），本地提交 0c93c4f；连同上轮 6f06815（#43 引擎级重复时间戳测试）共 2 个提交**待推送**——本机到 GitHub 全路径网络阻断（443/22 均被断，fake-IP 198.18.x 连接即关），恢复后先 `git push origin master`，再到 #43/#45 回帖修复报告并关闭。
- 测试：tests/test_backtest_correctness_p1 51/51 通过；存量失败仅 test_ohlcv_standardization_from_chinese_columns（环境 dtype，与改动无关）。
- 关键决定（#45）：validation 与 normalization 分层——默认 WeightValidationError fail-fast（NaN/inf/bool/负值/未知标的/单权重>1/Σ>1+1e-6 容差），归一化仅 `Backtester(normalize_weights=True)` 显式 opt-in 并记诊断；买入顺序 (-权重, 代码) 确定性排序；新增 result.weight_allocations 暴露 requested vs executed。

## 历史（前一阶段）
- GitHub issue #27 已通过 CI、回复并关闭；issue #28 的 Node 20 action 弃用警告也已修复并通过无注解 CI，待文档收口、回复和关闭。
- 上次停在：`.github/workflows/windows-desktop.yml` 已升级到 `actions/checkout@v5` 和 `actions/setup-python@v6`；pytest 210/210、最终审计 22/22、Windows CI 均通过，check-run annotations 为 `[]`。
- 关键决定：只升级官方 action 主版本，保留 `windows-latest`、Python 3.11.9、密钥扫描、依赖锁校验和桌面发布命令不变。
- 原因：#27 的成功 CI run 仍提示 Node.js 20 已弃用，并由 GitHub 强制转换到 Node 24；官方 Node 24 action 已消除兼容路径和注解噪音。
- 前序证据：Streamlit 1.59.2 下 `/?page=plan` 在 1440x900 和 390x844 正常渲染，控制台均为 0 errors / 0 warnings；pytest 210/210、最终审计 22/22、桌面发布 11/11。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

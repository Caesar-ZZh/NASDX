# CONTEXT

- 当前：GitHub issue #24 的北交所覆盖修复已完成并通过发布门禁，本阶段同步代码与 issue 回执。
- 上次停在：真实网络验证沪深北列表/报价 5528/5528、北交所 327/327，旧 `430/830` 代码可取得历史行情；pytest 198/198、final audit 22/22、release check 11/11 通过。
- 关键决定：交易所识别统一收敛到 `market_symbols.py`；股票列表缓存包含版本与覆盖元数据；报告和网页显式显示 SSE/SZSE/BSE 列表及报价覆盖。
- 原因：原实现把北交所 `4/8` 误路由到深市、`920` 误路由到沪市，且股票列表只含沪深，数据缺失时也没有用户可见提示。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

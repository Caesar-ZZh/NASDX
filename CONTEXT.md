# CONTEXT

- 当前：完成 Streamlit 响应修复：导航改为 callback 单次 rerun，URL/session 路由统一；首页报告读取缓存；快速选股降为 3 个 widget；扫描任务显示成功/失败/超时；分析轮询不再阻塞 UI；量化页延迟 pandas 导入。
- 上次停在：pytest 134/134、ruff、`run_final_audit.py` 22/22、desktop doctor 全通过；Playwright 热态页面切换约 0.5-1.6 秒、浏览器错误 0；8502 已重启为单一新实例，待提交推送并关闭 issue #10。
- 关键决定：保留 Streamlit/CLI 和现有后台 task_id 边界；用 widget callback 更新路由，用任务结果字典向 UI 反馈子进程状态，不把线程放入 session state。
- 原因：根因是导航显式二次 rerun、阻塞式 sleep、每次渲染数十个股票按钮、扫描错误被吞掉和量化入口无条件重型导入。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

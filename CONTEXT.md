# CONTEXT

- 当前：完成 Streamlit 前端 UI refresh：统一 `static/style.css` token、卡片/表格/Tab/按钮/页面头样式，首页新增扫描-路线-复核-追踪工作流视图，今日选股页中文化并统一数据表。
- 上次停在：ruff 通过；pytest 相关契约 51 个通过；`run_final_audit.py` 22/22 通过；Playwright 验证 home/selector/quant 页面标题渲染且无错误文本；待提交并推送。
- 关键决定：继续保留 Streamlit 与现有 CLI，不引入新前端框架；UI 优化集中在样式层和少量 HTML class；顺手修正量化页 `tab6` 缩进导致的页面级错误。
- 原因：本轮目标是美化前端渲染和可读性，不触碰量化、数据、交易核心逻辑。

## Desktop Packaging

- 当前：smoke 脚本会在收尾清理包内 `__pycache__/`、`*.pyc`、`*.pyo`；`run_desktop_release_check.py` 会在 zip/installer 输入之后再汇总 release evidence。
- 关键决定：保留 smoke 对真实包/安装布局的启动验证，但验证结束后把由 Python 运行生成的缓存从 `{app}`/portable 包内移除。
- 原因：正式包不能因为验证动作本身留下禁入缓存；release evidence 必须检查本轮刚生成并 smoke 过的产物。

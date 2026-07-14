# NASDX — A股热门板块多智能体量化分析系统

> 基于 [FinGenius](https://github.com/HuaYaoAI/FinGenius) 架构设计  
> 无需付费行情 API，使用腾讯行情与 AkShare 免费数据源，支持任意 OpenAI 兼容接口

---

## ✨ 功能特性

- 📊 **ETF50 全量扫描** — 50只主流ETF每日技术面评分排行，自动打开浏览器报告
- 📈 **60只个股扫描** — 10大热门板块龙头，均线/MACD/RSI/布林带综合评分
- 🤖 **多智能体分析** — 5个专家Agent（技术面/资金流/风险/板块/供应链瓶颈）+ Battle多空辩论
- 🧱 **LLM 结构化输出** — Agent 要求模型返回 JSON 信号、置信度、结论和关键依据，减少文本解析漂移
- 🧠 **规则深度报告** — 无 API Key 时自动生成同结构单票深度报告，仍进入组合路线和最终简报
- 🔁 **一键投研闭环** — 可选串联行情刷新、ETF/个股规则扫描、多智能体深度分析
- 🧩 **低延迟行情通道** — ETF/动态选股使用腾讯批量报价、tdxrs 单连接前复权历史、腾讯补缺与短时本地缓存
- 🏦 **沪深北全 A 覆盖** — 合并三家交易所官方股票列表，兼容北交所 `4/8/920` 代码并在报告中显示覆盖状态
- 🧭 **组合级投资路线** — 聚合扫描榜单和深度报告，输出 ETF 主线、个股卫星、观察/回避池
- 🧾 **最终投资简报** — 无 API Key 时也能生成方向、仓位、候选剧本、情景和风险边界
- 🔎 **候选证据核查** — 每个候选标记试错/观察/补报告/回避，并列出待人工复核项
- 🗓️ **盘前/盘中/盘后执行队列** — 把候选审计转成补报告、复核、观察和盘后刷新动作
- 🔗 **外部复核包** — 为每个候选列出公告、行情、交易所入口和必须通过的复核条件
- 💰 **资金仓位换算** — 输入临时总资金和已有仓位，换算剩余额度、候选上限和第一笔试错金额
- 🧭 **建议漂移追踪** — 对比本次和上次简报，标出新增、移除、状态变化和下次复盘重点
- 📈 **建议结果复盘** — 用最新行情和扫描验证候选信号是否延续、降级、仍待补证据或缺数据
- 📦 **复盘快照包** — 一键导出路线、简报、候选审计、执行队列、外部复核包和来源清单
- 🗄️ **SQLite 历史库** — 自动追加 `nasdx_history.db`，索引简报、报告、扫描和 ETF 池历史
- 🔮 **未来情景推演** — 按数据闸门、市场强弱、主题轮动生成后续加仓/降仓/观望规则
- 🧭 **行动计划** — 把多维信号转成方向、仓位区间、入场条件、止损/复核触发
- 🛡️ **风险画像与数据质量闸门** — 保守/均衡/进取三档仓位纪律，过期行情或低覆盖扫描会自动降仓/阻断执行
- ⏰ **工作日定时** — 早10:00 + 下午14:30 自动扫描，浏览器实时查看
- 🌐 **Streamlit 网页** — 输入股票代码一键分析，暗色专业UI

---

## 🏗️ 架构

```
NASDX
├── nasdx/                   # 核心包（多智能体框架）
│   ├── agents/              # 5个专家 Agent
│   │   ├── technical.py     # 技术面（MA/MACD/RSI/布林带）
│   │   ├── fund_flow.py     # 资金流向（主力/超大单）
│   │   ├── risk.py          # 风险评估（超买/背离）
│   │   ├── sector.py        # 板块轮动
│   │   ├── chokepoint.py    # Serenity供应链瓶颈/需求冲击/贝叶斯更新
│   │   └── synthesis.py     # 综合研判
│   ├── environments/
│   │   ├── research.py      # 研究环境（5 Agent 并发分析，可配置顺序回退）
│   │   └── battle.py        # 辩论环境（多空博弈 + 投票）
│   ├── llm.py               # LLM 客户端（支持 DeepSeek/Claude/Qwen）
│   ├── data_loader.py       # 数据加载与格式化
│   ├── fast_market.py       # 交互扫描的批量报价、并发 K 线与短时缓存
│   ├── market_sources.py    # A股/ETF K线多数据源回退与字段归一
│   ├── market_symbols.py    # 沪深北交易所识别与行情代码路由
│   ├── data_quality.py      # 行情数据新鲜度检查
│   ├── decision.py          # 投资决策层（方向/仓位/风险纪律/复核触发）
│   ├── portfolio.py         # 组合级投资路线（ETF主线/个股卫星/未来情景）
│   ├── investment_brief.py  # 最终投资简报（方向/候选剧本/风险边界）
│   ├── candidate_audit.py   # 候选证据核查（数据/深度报告/人工复核项）
│   ├── position_sizing.py   # 资金仓位换算（临时输入，不保存账户信息）
│   ├── recommendation_tracker.py # 建议漂移追踪（跨次简报变化）
│   ├── recommendation_review.py # 建议结果复盘（信号延续/降级）
│   ├── account_review.py    # 真实账户复盘（成交 CSV/盈亏/路线匹配）
│   ├── execution_queue.py   # 盘前/盘中/盘后执行队列
│   ├── external_review.py   # 外部复核包（公告/行情/交易所入口与通过条件）
│   ├── review_snapshot.py   # 复盘快照包导出（ZIP/manifest/CSV）
│   ├── history_store.py     # SQLite 历史库（报告/扫描/ETF池/简报索引）
│   ├── cloud_sync.py        # ETF50 白名单校验、并发锁与隔离发布
│   ├── rule_based_analysis.py # 无API规则深度报告
│   ├── analyzer.py          # 主分析器（三阶段管道）
│   └── report.py            # HTML 报告生成
│
├── scan_etf50.py            # ETF50 全量扫描（纯规则，无需API）
├── scan_and_sync.py         # 扫描后通过独立临时 clone 安全发布 deploy
├── scan_stocks_full.py      # 60只个股完整扫描
├── fetch_stock_data.py      # AkShare 数据抓取
├── run_analysis.py          # 单只股票多智能体分析
├── run_investment_workflow.py # 一键投研闭环（刷新/扫描/深度分析）
├── run_portfolio_plan.py    # 生成组合级投资路线
├── run_investment_brief.py  # 生成最终投资简报
├── run_position_sizing.py   # 按临时资金输入换算仓位金额
├── run_recommendation_tracker.py # 生成建议漂移追踪
├── run_recommendation_review.py # 生成建议结果复盘
├── run_account_review.py    # 导入成交 CSV 生成真实账户复盘
├── run_review_snapshot.py   # 导出复盘快照包
├── run_final_audit.py       # 最终版交付自检
├── app.py                   # Streamlit 网页入口
├── etf50_pool.json          # 50只ETF池配置
├── stocks.json              # 股票池配置（6板块30股+39ETF）
└── 启动网页.bat             # Windows 一键启动 Streamlit
```

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements_nasdx.txt
```

Windows PowerShell 推荐使用独立虚拟环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements_nasdx.txt
```

开发/测试工具单独安装，避免把 lint、测试和 hooks 混进普通运行环境：

```powershell
python -m pip install -r requirements-dev.txt
```

常用开发检查：

```powershell
python -m pytest
python -m ruff check --no-cache .
python -B -m unittest discover -s tests
python -B -m unittest tests.test_desktop_launcher_contracts -v
python -B run_security_checks.py --skip-optional
python -B run_desktop_doctor.py
python -B run_desktop_completion_audit.py
python -B run_desktop_release_evidence.py --json
python -B run_final_audit.py
python -B run_product_readiness.py
python -B run_desktop_release_check.py
```

可选安装本地提交前检查：

```powershell
pre-commit install
pre-commit run --all-files
```

轻量安全检查默认只扫描可入库文本文件里的疑似密钥，并跳过未安装的外部工具：

```powershell
python -B run_security_checks.py --skip-optional
```

若已单独安装 `pip-audit`、`bandit`、`detect-secrets`，可以显式运行可选检查：

```powershell
python -B run_security_checks.py --run-optional
```

API Key 只通过环境变量或网页会话输入，不写入 Git 跟踪文件：

```powershell
$env:NASDX_API_KEY="sk-xxxx"
$env:NASDX_BASE_URL="https://api.deepseek.com"
$env:NASDX_MODEL="deepseek-chat"
$env:NASDX_FALLBACK_MODELS="deepseek-reasoner,deepseek-chat" # 可选；非 DeepSeek 默认不跨模型降级
```

Windows 桌面启动器也支持本地用户配置文件。推荐路径：

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\NASDX"
Copy-Item config.example.toml "$env:APPDATA\NASDX\config.toml"
notepad "$env:APPDATA\NASDX\config.toml"
python -B desktop\launcher.py --dry-run --page plan
```

也可以显式指定配置文件：

```powershell
$env:NASDX_CONFIG_FILE="D:\secure\nasdx\config.toml"
python -B desktop\launcher.py --page plan
```

优先级是：当前进程环境变量 > `NASDX_CONFIG_FILE` 指定文件 > `%APPDATA%\NASDX\config.toml` > 项目内被忽略的 `config.toml`。`--dry-run` 只显示配置路径和已加载字段名，不打印 API Key 值。

### 2. 获取数据（无需 API Key）

```bash
python fetch_stock_data.py
```

定时 ETF50 扫描与云端数据同步使用：

```powershell
python scan_and_sync.py
python scan_and_sync.py --no-sync
```

同步过程不会切换当前工作树分支。它会先确认当前跟踪文件无未提交修改，再通过跨进程锁和独立临时 clone 发布到 `deploy`；仅最新的 `etf50_YYYYMMDD_HHMM.json` 可进入发布流程，并在提交前校验 JSON schema、2 MB 大小上限、6 小时时效和敏感字段。扫描成功与发布成功分别返回状态，任何 Git 提交或推送失败都会使脚本返回非零退出码。

### 3. 运行扫描（无需 API Key）

```bash
# ETF50 技术面扫描（纯规则，秒出结果）
python scan_etf50.py

# 60只热门个股扫描
python scan_stocks_full.py

# 动态选股（网页默认预筛 50 只进入历史因子）
python run_stock_selector.py --limit 50
```

ETF50 与动态选股不会等待无上限的 Eastmoney 请求：实时报价批量获取，历史 K 线并发执行且带单请求超时；缺失项会以较低并发重试一次。股票列表缓存 7 天，历史 K 线缓存 10 分钟，均写入用户本地数据目录而不是仓库。

### 4. 深度分析（LLM 可选）

```bash
# 可选：设置 API Key（支持 DeepSeek / Claude / Qwen 任意 OpenAI 兼容接口）
export NASDX_API_KEY=sk-xxxx
export NASDX_BASE_URL=https://api.deepseek.com   # 可选
export NASDX_MODEL=deepseek-chat                  # 可选
export NASDX_LLM_MAX_ATTEMPTS=3                   # 可选，1-20
export NASDX_LLM_MAX_ELAPSED_SECONDS=30           # 可选，单次完整请求总时限
export NASDX_LLM_MAX_RETRY_DELAY_SECONDS=8        # 可选，最大重试等待

# 分析单只股票：默认 auto，有 API/本地模型则用 LLM，否则自动用规则深度报告
python run_analysis.py 603501 --risk-profile balanced

# 强制无 API 规则版
python run_analysis.py 603501 --mode rules --risk-profile balanced

# 强制 LLM 版
python run_analysis.py 603501 --mode llm --risk-profile balanced

# 一键闭环：仅深度分析（默认，最快）
python run_investment_workflow.py 603501

# 一键闭环：先刷新行情和 ETF50 扫描，再深度分析
python run_investment_workflow.py 603501 --workflow quick --risk-profile balanced

# 一键闭环：刷新行情 + ETF50/个股双扫描 + 深度分析（较慢）
python run_investment_workflow.py 603501 --workflow full --rounds 1 --risk-profile conservative

# 一键闭环：动态选股后对 Top 候选做深度分析（无候选时会安全停止）
python run_investment_workflow.py --workflow selector --analysis-mode rules

# 只生成组合级投资路线（读取最新本地扫描和深度报告）
python run_portfolio_plan.py --risk-profile balanced

# 生成最终投资简报（读取最新路线并输出 Markdown/JSON）
python run_investment_brief.py --risk-profile balanced

# 临时资金仓位换算（不写入账户数据）
python run_position_sizing.py --capital 100000 --current-etf 10000 --current-stock 5000 --risk-profile balanced

# 生成建议漂移追踪（对比最新简报与上一份不同时间的简报）
python run_recommendation_tracker.py --print

# 生成建议结果复盘（用最新行情/扫描验证上一份建议）
python run_recommendation_review.py --print

# 真实账户复盘（从成交 CSV 计算已实现/浮动盈亏和路线匹配）
python run_account_review.py --ledger trades.csv --capital 100000 --print

# 导出复盘快照包（ZIP，含简报/路线/候选审计/执行队列/外部复核包）
python run_review_snapshot.py --risk-profile balanced

# 最终版交付自检（语法、安全、路线契约、SQLite历史库、网页入口、桌面交付资产、文档覆盖）
python run_final_audit.py

# 产品化巡检聚合入口（单测 + 最终审计）
python run_product_readiness.py

# 若当前 shell 已设置 NASDX_API_KEY，可追加一次 LLM smoke 验证
python run_product_readiness.py --llm-smoke

# 启动网页界面
双击 启动网页.bat
# 或: streamlit run app.py
```

### 5. Windows 桌面启动器 MVP

当前桌面入口仍复用现有 Streamlit UI，不迁移前端、不删除 `.bat` 和 CLI 脚本。桌面控制面板提供 Start、Stop、Open App、Settings、Logs、Data Refresh 入口；底层启动器会在本机启动 `app.py`，默认绑定 `127.0.0.1`，可打开指定页面：

完整 Windows 桌面使用说明见 `docs/WINDOWS_DESKTOP.md`。

```powershell
# 打开桌面控制面板
python -B desktop\control_panel.py

# 只读检查桌面环境、配置元数据和可选打包工具
python -B run_desktop_doctor.py

# 输出桌面化完成度证据矩阵，显式标出 installer 未闭环项
python -B run_desktop_completion_audit.py

# 需要确认 runtime/report 路径可写时再显式加写入探针
python -B run_desktop_doctor.py --check-write

# 或直接双击/运行桌面批处理
.\启动NASDX桌面.bat

# 验证批处理入口，不打开 GUI
.\启动NASDX桌面.bat --dry-run --page plan

# 预览当前用户快捷方式，不写入
powershell -ExecutionPolicy Bypass -File packaging\windows\create_shortcuts.ps1 -Desktop

# 确认后创建开始菜单和桌面快捷方式
powershell -ExecutionPolicy Bypass -File packaging\windows\create_shortcuts.ps1 -Desktop -Apply

# 查看将要执行的 Streamlit 命令，不启动服务
python -B desktop\launcher.py --dry-run --page plan

# 启动后检查就绪再自动关闭，适合发布前 smoke
python -B desktop\launcher.py --headless-smoke --timeout 30 --no-browser

# 正常打开投资路线页
python -B desktop\launcher.py --page plan
```

启动器会读取安全本地配置，并把允许字段转换为子进程环境变量；父进程中的 `NASDX_API_KEY`、`NASDX_BASE_URL`、`NASDX_MODEL`、`NASDX_HISTORY_DB` 等显式环境变量优先。启动器不会创建或写入 `.env`、`config.toml` 或报告目录。
源码 checkout 默认使用项目目录作为运行目录；便携包或只读安装场景可显式指定运行目录：

```powershell
$env:NASDX_RUNTIME_DIR="$env:LOCALAPPDATA\NASDX"
python -B desktop\launcher.py --page plan
```

启动器会设置 `NASDX_HISTORY_DB` 默认指向运行目录下的 `nasdx_history.db`，并把 `NASDX_REPORTS_DIR` 指向运行目录下的 `reports`；如果父进程已设置这些变量，则保留用户指定值。核心 Streamlit 页面、扫描脚本、投研 CLI 和报告模块会尊重该 reports 目录；`fetch_stock_data.py` 与数据加载器会优先使用 `NASDX_DATA_DIR`，未设置时使用 `NASDX_RUNTIME_DIR`，源码 checkout 无环境变量时仍保持原项目目录兼容。
可在本地配置的 `[paths]` 中设置 `runtime_dir`、`history_db`、`reports_dir`；相对路径按配置文件所在目录解析。
控制面板的 Settings 按同一规则打开或创建用户级 `config.toml`，Logs 打开运行目录下的 `desktop_logs`，Data Refresh 只调用现有 `fetch_stock_data.py`，不会自动触发扫描、交易或部署同步。

如果需要原生桌面窗口体验，可单独安装可选 WebView 依赖：

```powershell
python -m pip install -r requirements_desktop.txt
python -B desktop\launcher.py --webview --page plan
```

`--webview` 依赖本机 WebView2/pywebview；不可用时会回退到普通浏览器，不影响 `.bat`、Streamlit 或 CLI 使用。

### 6. Windows 便携包骨架

当前打包策略先生成便携文件夹，不做 one-file 全量打包：

```powershell
# 快速验证复制/排除规则，不安装依赖
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -SkipDependencyInstall

# 可选：只构建启动器 exe 的计划检查，不打包 app.py/量化依赖，也不安装 PyInstaller
powershell -ExecutionPolicy Bypass -File packaging\windows\build_launcher_exe.ps1 -SkipBuild

# 从包目录启动 Streamlit 并检查 plan 页、静态 CSS、runtime 路径和残留进程
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable.ps1 -PackageDir dist\NASDX-Desktop

# 给 portable 包创建当前用户快捷方式，先预览再显式 -Apply
powershell -ExecutionPolicy Bypass -File dist\NASDX-Desktop\packaging\windows\create_shortcuts.ps1 -Desktop
powershell -ExecutionPolicy Bypass -File dist\NASDX-Desktop\packaging\windows\create_shortcuts.ps1 -Desktop -Apply

# 用便携包模拟已安装目录形态，检查控制面板、runtime 和 plan 页
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir dist\NASDX-Desktop -Timeout 60

# 把已验证的便携目录打成 zip，并解压到临时目录后再次 smoke
# 会同时生成 dist\NASDX-Desktop-portable.zip.sha256 和 dist\NASDX-Desktop-portable.manifest.json
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable_zip.ps1 -PackageDir dist\NASDX-Desktop -OutputZip dist\NASDX-Desktop-portable.zip
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_portable_zip.ps1 -ZipPath dist\NASDX-Desktop-portable.zip -Timeout 60

# 真正准备便携目录，会在 dist\NASDX-Desktop\.venv 安装核心运行依赖
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1

# 如需把 pywebview 一并装进包内 venv，再显式打开可选项
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -IncludeWebView

# 网络不稳定时，先构建本地 wheelhouse，再离线安装到便携包
powershell -ExecutionPolicy Bypass -File packaging\windows\build_wheelhouse.ps1
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1 -WheelhouseDir wheelhouse\nasdx-win-py311

# 验证安装器输入，不编译、不安装
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild -SkipCompile

# Inno Setup 编译器引导默认只预览，不安装系统软件
powershell -ExecutionPolicy Bypass -File packaging\windows\install_inno_setup.ps1
powershell -ExecutionPolicy Bypass -File packaging\windows\install_inno_setup.ps1 -Install -AcceptAgreements

# 安装器发布预检，只读检查 portable/zip/hash/manifest/ISCC 和下一步命令
powershell -ExecutionPolicy Bypass -File packaging\windows\preflight_installer_release.ps1 -RequireVenv

# 本机已安装 Inno Setup 7/6 时，编译安装器到 dist\installer\
powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild

# 在一次性 Windows 用户或 VM 里安装后，验证真实安装目录
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installed.ps1 -InstallDir "$env:LOCALAPPDATA\Programs\NASDX Desktop" -Timeout 60

# 在一次性 Windows 用户或 VM 里执行真实安装/验证/卸载闭环，并证明使用包内 .venv
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installer_roundtrip.ps1 -InstallerPath dist\installer\NASDX-Desktop-Setup.exe -AllowInstall -CheckShortcuts -RequireVenv -Timeout 60

# 桌面发布前聚合检查：lint、桌面合同、轻量安全检查、隔离 portable 包、smoke、installer 输入、final audit
python -B run_desktop_release_check.py
python -B run_desktop_release_check.py --write-evidence

# 汇总桌面发布证据；可用 --package-dir 指向本次被测包
python -B run_desktop_release_evidence.py --json
python -B run_desktop_release_evidence.py --json --package-dir dist\NASDX-Desktop-check

# 真正准备带 .venv 的包时显式打开；慢网络可调大 package/pip 超时；已安装 Inno Setup 7/6 时再编译 installer
python -B run_desktop_release_check.py --full-package --package-timeout 1200 --pip-timeout 120 --pip-retries 3
python -B run_desktop_release_check.py --full-package --zip-package --package-timeout 1200 --zip-timeout 900 --pip-timeout 120 --pip-retries 3
python -B run_desktop_release_check.py --full-package --compile-installer
```

输出目录 `dist\NASDX-Desktop\`、默认快速门禁目录 `dist\NASDX-Desktop-check\` 和可选 `dist\launcher-exe\` 都被 Git 忽略。脚本只复制源码、配置模板、依赖清单、池配置和桌面启动脚本，排除并 scrub `reports/`、`stock_data_*.json`、`nasdx_history.db`、`.env`、`config.toml`、日志、`__pycache__/`、`*.pyc`、缓存和构建产物；依赖安装完成后会保留包内 `.venv`，但再次清理 `.venv` 中的 Python 缓存。
Windows 正式包固定使用 `packaging/windows/toolchain-win.json`、核心/含 WebView 两份完整哈希锁和 `pip --require-hashes`。运行 `python -B run_dependency_lock_check.py` 可验证两套 Windows 依赖图；升级依赖时使用固定 `uv 0.10.2` 执行 `packaging\windows\refresh_dependency_locks.ps1`，并提交两份锁文件。`PACKAGING_MANIFEST.json` 会记录 Python/pip 版本、锁文件 SHA-256 和实际 `pip freeze --all` 清单。
包内 `PACKAGING_MANIFEST.json` 使用 `path_policy=relative-or-redacted`，只记录相对路径或 `<source-checkout>` / `<external-path>` 占位符，不写入打包机的 `C:\Users\...` 绝对目录。
`启动NASDX桌面.bat` 默认打开桌面控制面板；如果控制面板不可用，会回退到 direct launcher。批处理会透传 `--dry-run`、`--page`、`--timeout` 等参数给控制面板，便于 smoke 验证真实入口。`create_shortcuts.ps1` 默认只预览，传 `-Apply` 才会给当前用户写入开始菜单或桌面快捷方式。`pywebview` 不是默认打包依赖；没有它时 direct launcher 会回退到普通浏览器，仍然复用现有 Streamlit UI。
`build_launcher_exe.ps1` 是可选 PyInstaller 路径，只冻结 `desktop\exe_launcher.py` 这个很薄的启动器；生成的 exe 仍会调用 portable 包内 `.venv\Scripts\python.exe -B desktop\control_panel.py`，不会把 `app.py`、AkShare、pandas 或投研逻辑打进单文件 exe。默认先用 `-SkipBuild` 做计划检查；只有打包机已安装 PyInstaller 时再去掉该参数。
GitHub Actions 的 Windows 桌面检查会运行 `python -B run_desktop_release_check.py --skip-final-audit --fail-fast`，默认把快速 portable 包写到 `dist\NASDX-Desktop-check\`，避免覆盖带 `.venv` 的正式发行包；完整投资数据审计仍以本地 `run_product_readiness.py` 为准。
显式运行 `--full-package` 时，portable 和 installed-layout smoke 会要求使用 `dist\NASDX-Desktop\.venv\Scripts\python.exe`，避免用开发机全局 Python 掩盖缺依赖问题。
显式运行 `--zip-package` 时，会生成被 Git 忽略的 `dist\NASDX-Desktop-portable.zip`、`dist\NASDX-Desktop-portable.zip.sha256` 和 `dist\NASDX-Desktop-portable.manifest.json`，再校验 SHA256/`nasdx_portable_release.v1` manifest、解压到临时目录并复用包内 `smoke_installed.ps1` 验证。
`build_portable_zip.ps1` 会在压缩前拒绝包内禁入文件，`smoke_portable_zip.ps1` 会在解压后复查 `__pycache__/`、`*.pyc`、`.env`、`config.toml`、`reports/`、日志和本地 DB；release evidence 也会把 zip entry 的禁入路径计入 `zip_forbidden_failures`。
`run_desktop_completion_audit.py` 是只读完成度证据矩阵，会单独报告 portable runtime bundle 状态，把 `pywebview` 缺失标为 WARN，把本机缺少 `ISCC.exe` 或真实 installer roundtrip 未验证标为 INCOMPLETE；它不会启动应用、安装依赖或运行安装器。`ISCC.exe` 会从 PATH、Inno Setup 7/6 常见目录和 Windows 卸载注册表中自动发现；特殊安装位置仍可传 `-IsccPath`。
`run_desktop_release_check.py --write-evidence` 会在 build/smoke 之后写入 ignored `dist\release-evidence\NASDX-desktop-release-evidence.json`；也可以用 `--evidence-output` 指向其他 ignored 路径。默认 release gate 仍只打印 evidence，不落盘。
默认 release gate 没有运行 `--zip-package` 时会用 `--skip-zip` 避免把历史遗留 zip 当成本轮验证对象；显式 `--zip-package` 时才把刚构建并 smoke 过的 zip 纳入 release evidence。
`run_desktop_release_evidence.py` 是只读发布证据包汇总，会聚合 completion audit、desktop doctor、portable/zip/installer artifact 元数据、ignored path 检查和下一步命令；默认只打印 JSON，`--package-dir` 可指向本次 release gate 验证的 `dist\NASDX-Desktop-check` 或正式 `dist\NASDX-Desktop`，传 `--write` 时才写入 ignored `dist\release-evidence\NASDX-desktop-release-evidence.json`。它还会在 `forbidden_present` 中列出包内禁入相对路径，并把数量计入 `package_forbidden_failures`；发现 `.env`、`config.toml`、`reports/`、日志、`__pycache__/`、`*.pyc`、本地数据库或构建输出时会失败，但不会读取或打印文件内容。
`smoke_installer_roundtrip.ps1` 默认只输出 plan-only 预检，不会运行安装器；只有显式传入 `-AllowInstall` 才会安装、调用 `smoke_installed.ps1`、再运行 Inno 卸载器，适合一次性 Windows 用户或 VM。安装器卸载时会删除 `{app}` 这个应用安装目录，包括运行后生成的 Python 缓存；用户配置、报告和历史库仍在 `%APPDATA%\NASDX` 或外部目录。正式交付验证建议同时传 `-RequireVenv`，证明安装目录使用包内 `.venv` 而不是开发机全局 Python。真实 roundtrip 成功后会在 ignored `dist\installer\NASDX-Desktop-roundtrip-proof.json` 写入 proof；`run_desktop_completion_audit.py` 只有在 proof 的 installer SHA256、安装 smoke、卸载、`RequireVenv` 和快捷方式检查都匹配当前 setup 时才会把 installer roundtrip 视为 PASS。

| 工作流 | 做什么 | 适合场景 |
|---|---|---|
| `analysis-only` | 直接使用最新本地行情做 5 Agent 深度分析 | 已有新数据，只想看行动计划 |
| `quick` | 刷新行情、跑 ETF50 扫描、再深度分析 | 想先看大盘/ETF 强弱，再看单票 |
| `full` | 刷新行情、跑 ETF50 和 60只个股扫描、再深度分析 | 做完整复盘或盘后筛选 |

`run_investment_workflow.py` 默认会在结束时生成 `reports/portfolio_plan_latest.md/json` 和 `reports/investment_brief_latest.md/json`，网页端“投资路线”页会直接读取这两份产物。`--analysis-mode auto|rules|llm` 可控制深度分析通道；无 API Key 时推荐保持 `auto`。

研究阶段默认使用 `ThreadPoolExecutor` 并发运行技术面、资金流、风险、板块、供应链瓶颈 5 个 Agent，以减少 LLM 等待时间；如需调试或限流，可设置 `NASDX_RESEARCH_MAX_WORKERS=1` 退回顺序执行。Streamlit 入口、行情抓取和量化模块导入时都不改写全局 `requests.get`，HTTP 连接使用 `requests` 原生环境代理和数据源回退。

Streamlit 页面里的 API Key / Base URL / 模型名只保存在当前会话，并通过子进程环境传给深度分析任务，不写回全局 `os.environ` 或 `nasdx.llm` 单例。后台分析和 ETF 扫描用 `task_id` 追踪，线程对象只保存在进程内任务表，`session_state` 只保存可序列化状态；分析日志文件名包含 `task_id`，避免同一股票并发任务互相覆盖。

LLM Agent 会在提示词末尾追加统一 JSON 契约，优先读取 `signal`、`confidence`、`conclusion`、`key_points` 字段；若模型未返回合法 JSON，才回退到旧的 `【信号】/【置信度】` 文本解析。最终审计会检查这条结构化输出链路。

保存报告、最终简报、建议复盘、账户复盘和扫描结果时，会同步追加到本地 `nasdx_history.db`。它只做历史索引，不替代 `reports/` 下的 Markdown/JSON；如需改位置，可设置 `NASDX_HISTORY_DB`。

### 子代理协作

项目内置 5 个 `.claude/agents` 子代理模板：上游方案拆解、单功能实现、契约审计、Streamlit 验证、交付收口。协作方式见 `docs/SUBAGENT_WORKFLOW.md`。默认规则是：审计和验证代理只读，单功能实现代理只改授权文件，API Key 只从环境变量读取且不写入文件。

组合路线包含：

- 仓位框架：总仓位上限、ETF预算、个股预算、现金缓冲。
- 候选分层：ETF主线、个股卫星、观察名单、回避/减仓池。
- 未来情景：数据恢复、强势延续、信号转弱，或顺势偏多、结构轮动、防守下行。
- 执行规则：何时刷新、何时只观察、何时试错、何时降仓。
- 深度报告：过期报告只作为重跑提醒，不参与当前候选排序。
- 规则深度报告：无 API Key 时仍生成 `report_CODE_DATE.json/html`，用于候选升级、仓位纪律和最终简报。
- 扫描覆盖率：若个股或 ETF 榜单有效数据低于池子 50%，该榜单不参与加仓候选，组合路线自动进入 `position_cap`。
- 最终简报：把路线压缩为方向、仓位动作、ETF/个股候选剧本、未来情景和风险控制，适合盘后复盘。
- 候选证据核查：对每个前排候选列出数据闸门、扫描排序、深度报告、风险红灯、公告/成交人工复核和仓位纪律；缺报告候选只能观察或补报告，不能直接进入试错。
- 资金仓位换算：网页端“投资路线”页或 `run_position_sizing.py` 可把总仓位、ETF/个股预算、单票上限换算成金额；账户资金只做本次计算，不保存到文件。
- 建议漂移追踪：网页端“投资路线”页或 `run_recommendation_tracker.py` 会对比最新简报和上一份不同时间简报，标出新增/移除/状态变化和下次复盘重点。
- 建议结果复盘：网页端“投资路线”页或 `run_recommendation_review.py` 会用最新行情、ETF/个股扫描和当前简报状态复盘上一份建议，输出信号延续、降级复核、仍待补证据或缺当前数据。
- 真实账户复盘：网页端“投资路线”页或 `run_account_review.py` 可导入成交 CSV，计算已实现盈亏、浮动盈亏、当前仓位和路线匹配；没有账户流水时不会计算真实收益。
- 执行队列：把候选审计拆成盘前补报告、盘中人工复核/观察、盘后刷新简报三类动作；任何阻断项存在时都不会写成试错许可。
- 外部复核包：为每个候选给出巨潮资讯、行情页和交易所入口，并列明公告/财报、成交/折溢价、账户仓位等必须人工确认的通过条件；链接存在不代表复核已通过。
- 复盘快照包：网页端“导出复盘包”或 `run_review_snapshot.py` 会先验证 latest 简报/路线 schema，再原子输出 ZIP；三个 CSV 会把公式前缀按文本净化，manifest 使用 `nasdx_review_snapshot.v2` 并记录 `validation_status=valid`。
- SQLite 历史库：`nasdx.history_store` 使用 v2 schema，在 `artifacts` 中只保存一份完整正文，报告/扫描/ETF 池专表通过外键索引；同一逻辑事件在单事务内写入，旧 v1 数据库会就地无损迁移。

---

## 📊 监控池

### 股票（6板块 × 5只）

| 板块 | 代表标的 |
|---|---|
| 半导体 | 中芯国际、韦尔股份、兆易创新、华虹半导体、北京君正 |
| 半导体设备 | 北方华创、中微公司、华海清科、芯源微、盛美上海 |
| 通信 | 中兴通讯、中际旭创、烽火通信、华工科技、新易盛 |
| 电力 | 长江电力、国电南瑞、三峡能源、中国核电、特变电工 |
| AI算力 | 寒武纪、海光信息、科大讯飞、中科曙光、海康威视 |
| 军工 | 中航西飞、航发动力、中航光电、紫光国微、振华科技 |

### ETF池（50只）

涵盖：半导体芯片、科创板、通信5G、电力电网、AI算力、军工、海外纳指、港股科技、红利低波、机器人等主题

---

## 🤖 多智能体架构

```
用户输入股票代码
        ↓
  ┌─────────────────────────────────┐
  │  Phase 1: Research 研究阶段     │
  │   技术面Agent → 资金流Agent     │
  │   风险Agent  → 板块Agent        │
  │   供应链瓶颈Agent               │
  ├─────────────────────────────────┤
  │  Phase 2: Battle 辩论阶段       │
  │   多头辩手 ←→ 空头辩手          │
  │   裁判综合 → 5位投票者          │
  ├─────────────────────────────────┤
  │  Phase 3: Synthesis 综合研判    │
  │   行动计划 + 仓位纪律 + 风险复核│
  └─────────────────────────────────┘
        ↓
  HTML / JSON 报告（自动打开浏览器）
```

---

## ⚙️ LLM 配置

支持任何 OpenAI 兼容接口：

| 服务 | base_url | 推荐模型 |
|---|---|---|
| DeepSeek（推荐） | `https://api.deepseek.com` | `deepseek-chat` |
| 阿里通义 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| Ollama 本地 | `http://localhost:11434/v1` | `qwen2.5:14b` |

---

## ⚠️ 免责声明

本项目仅供学习研究，所有分析结果为技术规则计算或 AI 推演，**不构成任何投资建议**。股市有风险，投资需谨慎。

---

## 📄 License

MIT License — 自由使用，欢迎 Star ⭐ 和 PR

---

*基于 [FinGenius](https://github.com/HuaYaoAI/FinGenius) 开源架构 · 数据来自 [AkShare](https://github.com/akfamily/akshare)*

## 参考来源

- Serenity Chokepoint Investing Skill：用于新增供应链瓶颈、需求冲击、贝叶斯更新研究维度，项目内只作为研究框架，不作为投资建议。

# Cosmos（原 NASDX）— Goal Prompt：修复加载卡死 + 提速 + 新增量化策略板块

> 这是一份给 Codex（或其他自主编码 Agent）的 **Goal 级任务书**。执行时请严格遵守「Phase 0 先审阅」的要求，不要跳过上下文直接改代码。所有路径相对于仓库根目录。仓库：https://github.com/Caesar-ZZh/cosmos （分支 `master`）。

---

## Phase 0 — 先快速审阅项目，建立上下文（必须第一步）

目标：在改任何代码前，先用 30 分钟把项目跑起来并读懂数据流。请依次：

1. **读架构与运行方式**
   - 读 `README.md`、`docs/`（本仓库把详细文档下沉到 `docs/`，README 保持简洁）。
   - 看 `scripts/` 与 `packaging/` 里的启动/构建脚本，弄清本地怎么起后端和前端。
   - 后端入口：`server/main.py`（FastAPI，`app` 对象）；核心数据 API 在 `server/stock/base_app.py`，数据层在 `server/stock/astock.py`、`server/stock/market.py`、`server/stock/gstock.py`。
   - 前端：`frontend/`（React 19 + Vite + TS + Tailwind）。SPA 由后端在 `8900` 端口托管（生产），开发用 `vite` 代理 `/api` 到 `8900`。

2. **把项目跑起来并验证现状**
   - 后端：`pip install -r server/requirements.txt`，再起 `uvicorn`（确认 `server/main.py` 里的 app 对象名，按需 `uvicorn server.main:app --port 8900 --reload`）。**确认 akshare 已装**（`pip install akshare`，否则市场数据端点会 501 降级）。
   - 前端：`cd frontend && npm install && npm run dev`（或 build 后由后端托管）。
   - 用 curl 逐一把问题端点打一遍，记录响应时延：
     - `curl -m 30 -s http://localhost:8900/api/health`
     - `curl -m 30 -s "http://localhost:8900/api/market/overview"`   ← 重点，预期会卡
     - `curl -m 30 -s "http://localhost:8900/api/industry?top=30"`
     - `curl -m 30 -s "http://localhost:8900/api/market/emotion"`
   - 打开浏览器复现用户反馈的卡死：每日复盘（市场情绪 / 板块资金趋势榜 / 资金轮动）、实时驾驶舱（大盘指数 / 涨跌家数 / 板块热力）。

3. **读懂关键文件（至少通读以下）**
   - 前端：`frontend/src/lib/api.ts`（API 客户端，`request()` 无超时）、`frontend/src/pages/DailyReview.tsx`、`frontend/src/pages/Cockpit.tsx`、`frontend/src/hooks/useMarketPulse.ts`。
   - 后端：`server/stock/market.py`（市场总览/情绪/板块资金/短线情绪，注意 `_sentiment()` 与 `_sectors()` 直接调 akshare 且无超时）、`server/stock/astock.py`（注意 `em_get()` 有超时，但 `_akshare()` 直调的函数没有）、`server/stock/base_app.py` 的 `/api/market/overview`、`/api/industry` 等同步端点。
   - 量化库：`quant/`（`backtest.py`、`factors.py`、`signal_engine.py`、`etf50_quant.py`、`position_advisor.py`、`anti_overfit.py`、`confidence_trainer.py`、`rl_strategy.py`、`ml_model.py`、`data.py`）—— Phase 2 要复用。

4. **产出一份 5 行内的「上下文摘要」**（贴在你自己的执行日志里）：技术栈、请求链路（前端→vite proxy→FastAPI→akshare/东财）、卡死根因假设、Phase 2 候选复用模块。

---

## Phase 1 — 修复「加载中」永久卡死 + 提升加载速度

### 现象（用户报告，需全部消除）
- 每日复盘页：**市场情绪**、**板块资金趋势榜**、**资金轮动** 一直显示「加载中…」出不来。
- 实时驾驶舱页：**大盘指数**、**涨跌家数**、**板块热力** 一直显示「加载中」/空白。
- 要求：不仅修好，还要**明显提速**（首屏与轮询）。

### 根因（审阅后请核对，已高度确信）
- 根因是**后端同步端点被 akshare 网络调用永久挂起**，不是前端逻辑错。
- `server/stock/market.py`：
  - `_sentiment()` 直调 `ak.stock_market_activity_legu()`（legu/乐股源，常年不稳）。
  - `_sectors()` 直调 `ak.stock_fund_flow_industry(symbol="即时")`（东财资金流）。
  - 这两处都是**原生 akshare 调用，没有超时**；akshare 底层 `requests` 默认不超时，上游慢/不可达时无限挂起。
- 这两个调用所在的端点 `/api/market/overview` 是**同步 `def` 端点** → 跑在 FastAPI 线程池里；一次挂起占用一个线程，多次并发请求 → **线程池耗尽 → 所有相关端点（overview / emotion / industry / turnover-top）一起卡死**。
- 5 分钟缓存（`_cached`）只在 `fn()` 返回后才写入，所以挂起期间**没有任何短路**，每次请求都重新挂起（惊群）。
- 前端 `api.ts` 的 `request()` 用 `fetch` **没有 AbortController 超时**，后端不返回就永久等待；且页面只在 `.finally()`（成功或失败）后才把「加载中」翻成「暂无数据」，挂起时永远停在「加载中」。
- 实时驾驶舱的 `useMarketPulse` 用 `Promise.all([indices, overview, industry])`：只要 `overview` 或 `industry` 挂起，三者一起不 resolve → 大盘指数、涨跌家数、板块热力全空。
- 附带：当前跑在 8900 的进程是**陈旧构建**（health 仍返回 `vibe-research-api`/`0.2.2`，早于本次重命名与 akshare 重装），需重启生效。

### 修复方案（请按此实现，可调整细节）
**A. 后端：给所有 akshare 直调加硬性超时（核心修复）**
- 在 `server/stock/astock.py` 里把 `_sentiment`/`_sectors` 用到的 akshare 调用统一包一层：用 `concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=8)` 包裹，超时即抛 `TimeoutError` → 被现有 `try/except Exception` 捕获 → 返回空 `{}`/`[]` → 前端显示「暂无数据」而非无限「加载中」。
- 更彻底：给 akshare 底层 `requests` 设全局默认超时（monkey-patch `requests.adapters.HTTPAdapter` 或 `requests` 默认 `timeout`），让所有经 akshare 的调用都受约束。注意 `em_get()` 已有超时，不要破坏它。
- 单飞（single-flight）合并：对同一 endpoint 的并发请求，只放一个真实抓取，其余共享结果，避免惊群耗尽线程。

**B. 后端：端点韧性 + 负缓存**
- `/api/market/overview`、`/api/industry` 等建议改为 `async def` + `run_in_threadpool`，并对整体加一个服务端超时（如 uvicorn 层面或请求包装，单请求不超过 ~12s 即返回降级结果）。
- 对「抓取失败/超时」做**短负缓存**（如 60s），避免反复挂起；成功结果仍按 5 分钟 TTL。

**C. 前端：请求超时 + 更好的加载态**
- `frontend/src/lib/api.ts` 的 `request()` 加 `AbortController`，超时（建议 15s）则抛 `ApiError("请求超时，请刷新重试", 0)`。
- 「加载中」区分三态：**加载中（< 阈值，转圈）→ 超时/失败（显示「加载超时，点此重试」按钮）→ 暂无数据（已 resolved 但为空）**。目前 `DailyReview.tsx` 的 `pending(done)` 与 `useMarketPulse` 的 `error` 已有一部分基础，请补全超时分支与重试入口。

**D. 提速**
- 后端启动后用后台任务**预热缓存**（预抓 overview / industry / indices），让首屏直接命中缓存。
- 驾驶舱轮询复用现有 `useMarketPulse` 的 5s 节奏与交易时段判定；确认非交易时段自动暂停、手动刷新可用。
- 可选：对实时报价用 SSE/WebSocket 替代轮询（非必须，先保证不卡）。

**E. 运维**
- 改完后**重启 8900 后端**（用最新代码），确认 `health` 返回 `cosmos-api`/`0.2.0` 级别的新标识（即确认不是陈旧进程）；用 Phase 0 的 curl 复核 `/api/market/overview` 在数秒内返回（成功或「暂无数据」都算通过，重点是**不再永久挂起**）。

### 验收（必须全部满足）
- 上述 6 个卡死组件在交易时段或非交易时段都**不再永久「加载中」**：成功则出数据，失败/超时则出「暂无数据 / 点此重试」。
- `curl -m 15 /api/market/overview` 在 15s 内必有响应（200 或 502/降级），不再挂起。
- 首屏加载明显变快（缓存预热生效）。
- 原有功能（AI 复盘、资讯雷达、自选股、持仓等）不受影响、不回归。
- 跑一遍 `frontend` 的 type-check / build 通过。

---

## Phase 2 — 新增「量化策略」板块（复用本地 quant/ + 可选 GitHub 策略）

### 目标
在现有产品里新增一个**只读、客观、不荐股**的量化策略板块（导航新增一项，例如「策略实验室」），把本地已有的 `quant/` 引擎能力以可视化方式呈现，并可从 GitHub 引入 1–2 个许可干净（MIT / Apache-2.0）的量化策略实现来补充。

### 复用本地 `quant/` 模块（优先，已在仓库内，无需联网）
- `quant/backtest.py`：`Backtester`、`strategy_momentum`、`strategy_mean_reversion`、`strategy_factor_rank` —— 可做策略回测与对比。
- `quant/etf50_quant.py`：`run_etf50_quant` / `ETFQuantResult` —— ETF/50 成分量化评分。
- `quant/factors.py`、`quant/signal_engine.py` —— 因子与信号合成。
- `quant/anti_overfit.py`：`walk_forward_backtest`、`overfit_diagnosis`、`calc_ic`、`SignalVoter` —— 过拟合诊断（很有展示价值）。
- `quant/confidence_trainer.py`、`quant/position_advisor.py`、`quant/rl_strategy.py`、`quant/ml_model.py` —— 置信度/仓位/强化学习/机器学习信号（按需取用）。
- 数据走 `quant/data.py`（`get_ohlcv` / `get_batch_ohlcv`，已封装 akshare+mootdx+腾讯，带缓存与超时）。

### 可选：从 GitHub 补充策略（仅限许可干净、可移植的实现）
- 自行检索 1–2 个 MIT / Apache-2.0 的 A股/通用量化策略仓库（如动量、均值回归、因子研究的纯函数实现），**只移植其可计算的策略逻辑**到 `quant/`（新文件，注明出处与 LICENSE），不要整库拖入。
- 红线：禁止引入任何涉及实盘下单、账户、密钥的策略代码；只保留「输入历史数据 → 输出客观指标/信号」的纯计算部分。

### 后端（新增路由，建议 `server/stock/quant_router.py`，在 `main.py` 挂载）
- 例如：
  - `POST /api/quant/backtest`：入参 `{universe, strategy, start, end, initial_capital, rebalance}` → 调 `Backtester`/`strategy_*` → 返回净值曲线 + 指标（年化、Sharpe、最大回撤、胜率、IC）。
  - `GET /api/quant/etf50`：调 `run_etf50_quant` → 返回成分评分排名。
  - `POST /api/quant/overfit`：调 `walk_forward_backtest`/`overfit_diagnosis` → 返回过拟合诊断。
- 全部**同步或异步端点都要加超时与负缓存**（沿用 Phase 1 的韧性规范），回测可能慢，建议用后台任务 + 轮询或给前端明确进度/超时反馈。
- 端点返回**只含客观计算结果**，不得返回任何「买/卖某只股票」的指令性建议。

### 前端（新增页面 + 导航项）
- 在侧边栏（参考 `frontend/src/components/layout/Layout.tsx` 的导航结构）新增「策略实验室」页。
- 页面能力（按优先级，至少做前两项）：
  1. **策略回测对比**：选股票池/指数、策略（动量/均值回归/因子排名）、区间 → 跑回测 → ECharts 画净值曲线，表格列指标；多策略可叠加对比。
  2. **ETF/50 量化评分**：展示 `run_etf50_quant` 排名（条形/表格）。
  3. **过拟合诊断**：展示 walk-forward / IC / 参数稳健性结论。
- 视觉沿用现有冷色深色主题（`frontend/src/index.css` 的 `--primary` 冰蓝、`--accent` 青、`--danger` 红涨 / `--success` 绿跌），字体 Inter + Noto Sans SC（已在 `tailwind.config.ts` 与 `@fontsource` 配置，勿改回）。
- 遵守产品红线：页面显著位置标注「客观计算、非荐股、不构成投资建议」（参考现有 `Disclaimer` 组件，可复用）。

### 验收
- 导航出现新板块，三种核心能力至少前两项可用且返回客观结果。
- 后端回测/评分端点有超时与降级，长任务不卡死前端。
- 不引入任何实盘交易/账户/密钥逻辑；不荐股。
- type-check / build 通过，无 console 报错。

---

## Phase 3 — GitHub 仓库整理（Issues 梳理 + PR 清仓）

> 目标：让 `Caesar-ZZh/cosmos` 的 Issue/PR 列表回归「只反映真实待办」，关闭已完成/重复的，合并利于完整性的，剔除过时噪声。**本阶段以「不破坏 master、不重写历史、不静默丢弃贡献」为铁律。**

### 当前库存（执行前先 `gh` 拉一遍核实，以下为快照 2026-08-23）
- **Open Issues（4）**：`#124` 事件概率数据源(已知待做)、`#72` 安全基线(Dependabot/CodeQL/Ruleset)、`#61` 自动化可靠性、`#12` 历史中硬编码 DeepSeek API Key(高危)。
- **Open PRs（54）**：`Auto-align #92–#121`（共 26 个，机器人把 base 功能复刻进 NASDX 的 PR）、`dependabot` 一批（前端 npm / github-actions / pip）、`#54` 自主审查 harness、`#53` 修复 `--help` 秒回。

### 执行协议（严格按顺序）
1. **先建快照，再动手**：`gh issue list -s open`、`gh pr list -s open` 各存一份到 `docs/_triage-<date>.md`，作为审计底稿（必须，方便回滚与复盘）。
2. **Issues 处置**
   - 逐条读 issue 正文与关联 PR/commit；**只能关闭满足以下之一的**：① 功能已合入 master（附 commit/PR 证据）；② 明确过时/被取代；③ 重复（指向 canonical issue）；④ 与产品方向不符且用户已决策放弃。
   - 关闭必须带中文评论，说明「关闭原因 + 证据(commit/PR 链接)」，不得无理由批量关闭。
   - **`#12`（历史硬编码 Key）单独处理、不得自动执行**：它需改写 git 历史（BFG / git filter-repo）并 force-push。这属于破坏性操作——**只产出一份处置方案文档（步骤+风险+回滚），提交给用户确认后由人工执行**。Phase 3 内不得改历史、不得 force-push。
   - 其余仍在做/有价值的（`#124`、`#72`、`#61`）保持 OPEN，可在本轮一并推进（如 #124 可在 Phase 1/2 顺手落地）。
3. **PRs 处置（清仓，但非盲目全合）**
   - **`Auto-align #92–#121`**：先逐个核对「该 PR 对应的功能是否已存在于 master」（我们有过基础收敛提交，多数应已存在）。
     - 若功能已在 master → **关闭该 PR**，评论「此能力已由收敛提交合入 master（附 commit），本 PR 已过时，关闭而非合并，避免冲突/回归」。
     - 若功能确实缺失且 PR 干净可合 → 走下面「合并前校验」再合。
     - 若 PR 与 master 冲突 → **关闭并评论**说明冲突原因与替代（已收敛），不要强行 rebase 一堆 PR。
   - **`dependabot` PRs**：前端 npm 小版本（如 `react-router`、`postcss`）若 `npm ci` + build 通过、无 breaking change → 可合并；github-actions 版本（checkout/setup-python/codeql）若 CI 仍绿 → 可合并；**pip 依赖**（pywebview/tenacity/pydantic/pytest/tomli）须先确认不破坏 `uvicorn/fastapi/akshare` 运行链，否则留在 OPEN 并评论原因。
   - **`#54` / `#53`**：读内容与现状，若仍对完整性有益且构建通过 → 合并；若已被后续提交覆盖 → 关闭并说明。
4. **合并前校验（每个要合的 PR 必做）**：
   - `gh pr checks <n>` 必须全绿（CI 通过）；
   - `git fetch` 后本地 `git merge --no-commit` 验证**无冲突**（冲突即放弃该 PR，走关闭路径）；
   - 合并后跑：`frontend` type-check + build，以及后端 `python -c "import server.main"` 启动冒烟；任一项失败则**回退该合并**并评论原因。
   - 用 `gh pr merge --squash`（保持 master 线性、便于审阅）；不要 `--rebase` 一堆独立分支。
5. **禁止项**：禁止 force-push master（含 filter-repo/BFG）；禁止无评论批量关闭；禁止合并不通过 CI 或冲突的 PR；禁止改动 `server/stock/` 的 MIT 署名头。

### 验收
- 留一份 `docs/_triage-<date>.md` 底稿，记录每个 Issue/PR 的处置动作与理由。
- 剩余 OPEN 项均有「为何仍开」的明确理由（在 issue/PR 评论或底稿中）。
- master 仍可正常 build/启动，无回归；无历史改写。

---

## 全局约束（必读，违反即算不合格）
1. **MIT 署名**：`server/stock/`（及 `_reverse_miaoou/`）源自 Vibe-Research（MIT，作者 simonlin1212），其文件头 MIT 声明**必须保留**，只置换产品自身标识（产品名 Cosmos、作者 Caesar-ZZh、版本）。
2. **红涨绿跌**：A股语义固定——上涨=红（`--danger`），下跌=绿（`--success`），全球指数也沿用红涨（产品有意选择，勿改）。
3. **不荐股红线**：所有市场/策略功能只呈现客观数据与计算结果，不得给买卖时机、个股推荐、收益预测或投资建议。涉及个股清单处须标注「客观公开榜单，非推荐」。
4. **中文优先**：UI 文案、注释、提交信息用中文；代码标识符用英文。
5. **不破坏现有功能**：Phase 1 修复不得引入回归；Phase 2 为增量，不改动既有页面逻辑（除非修复需要）。
6. **提交规范**：单 Issue/次聚焦；提交信息中文、简洁；改完跑 `frontend` type-check + build 与后端启动冒烟；推到 `Caesar-ZZh/cosmos` 的 `master`。版本号在 `frontend/src/components/layout/Layout.tsx` 的 `APP_VERSION` 与后端 `base_app.py` 的 `version` 同步递增（本次目标 `0.2.1` 或 `0.3.0`，由你按改动量级决定并保持一致）。
7. **可验证**：每步改动都要能本地复现（curl + 浏览器），不要只凭推理声称修好。

---

## 交付物
- Phase 1 修复后：`/api/market/overview` 等端点不再挂起，6 个卡死组件恢复，首屏提速。
- Phase 2 完成后：新增「策略实验室」板块，复用 `quant/` 并可含少量 GitHub 移植策略。
- Phase 3 完成后：一份 `docs/_triage-<date>.md` 底稿（每个 Issue/PR 处置动作+理由）；过时的 Auto-align PR 关闭并注明已被收敛提交取代；dependabot/feature PR 在 CI 绿且无冲突下合并；`#12` 仅产出处置方案、不自动改写历史。
- 全部改动 commit 并 push 到 `Caesar-ZZh/cosmos` `master`，附中文提交说明；在 PR/commit 里用「结论先行」简述根因与方案。

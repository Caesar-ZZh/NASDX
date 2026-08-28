# NASDX / Cosmos 运行态应用评审（2026-08-25）

> 范围：用户正在运行的应用（http://127.0.0.1:8901/daily-review 及同源整套）及其相关代码（前端 `frontend/src`、后端 `server/stock/`、前后端对接）。
> 触发：用户指出第一轮评审（主工作树 master 后端 pytest）不是其"正在做/正在运行的"，要求对齐运行态。

## 0. 核心结论：运行态 ≠ 本地主工作树

| 实例 | 端口 | health 返回 | 对应代码 |
|---|---|---|---|
| **正在跑的应用** | 8901 | `cosmos-api` **0.3.0** | `.worktrees/cosmos-goal-2026-08-23/`（Cosmos 0.3.0 工作树，含 #125/#126/#129） |
| 残留僵尸后端 | 8900 | `vibe-research-api` **0.2.2** | 改名前更古老的检查点（pre-rebrand） |
| 主工作树源码 | — | `base_app.py` 写死 **0.2.0** | `master`（落后 origin 3 提交） |

**关键事实**：8901 跑的是 `cosmos-goal` worktree 的 0.3.0 代码，不是主工作树 master（0.2.0）。第一轮 pytest 评审针对主工作树，因此与用户实际运行态错位——用户的纠正成立。

## 1. 运行态实测（端到端）

- `GET 8901/api/health` → `{"ok":true,"service":"cosmos-api","version":"0.3.0"}` ✅
- `GET 8901/api/portfolio` → 正常返回结构（空持仓），后端逻辑可用 ✅
- `GET 8900/api/health` → `vibe-research-api 0.2.2`（僵尸，应杀）⚠️
- 8901 与 8900 根响应均为同一份 SPA `index.html`，且 8901 自带 `/openapi.json` → **同一 uvicorn 同源托管 API + SPA**（设计正确，无 CORS）。

## 2. 架构与对接质量（正面）

- **前后端契约完全对齐**：后端 `server/stock/base_app.py` 暴露 61 个 `/api/*` 端点，与前端 `frontend/src/lib/api.ts` 的调用一一对应（health/chat/debate/portfolio/radar/market/quote/valuation/margin/dragon-tiger…），无漂移。
- **`api.ts` 客户端规范**：统一 `request<T>` 封装、401 友好降级、`ApiError` 类、类型接口齐全、localStorage 鉴权键容错。
- **DailyReview 页面质量好**：加载/错误/空数据三态区分（`pending(done)` 区分"加载中"vs"数据源不可用"）；AI 复盘走 `chatStream` 流式；红涨绿跌有注释说明（A 股惯例，有意选择）。
- **前端结构清晰**：37 个 ts/tsx、5144 行，分层 `pages/components/lib/hooks/data`，无巨型文件（最大 StockData 606 / DailyReview 476）。

## 3. 发现的问题（按严重度）

### P0 — 运行态与代码/进程错乱
1. **双后端并存且版本互不一致**：8901=Cosmos 0.3.0（worktree）、8900=Vibe-Research 0.2.2（僵尸）。8900 是改名前的古老残留进程，仍占用端口并存活，应kill。
2. **前端 vite 代理默认目标 = 8900**（`vite.config.ts: apiTarget = VITE_API_URL || "http://127.0.0.1:8900"`）。任何 `npm run dev` 会话会打到**旧僵尸后端**而非当前 0.3.0，开发态与运行态后端不一致。
3. **运行态 8901 来自 worktree，非主工作树**：用户实际开发/运行在 `cosmos-goal` worktree（0.3.0），而主工作树 master 还是 0.2.0 且落后 origin 3。评审/修改若在主工作树进行会与运行态脱节。

### P1 — 代码小瑕疵
4. `frontend/src/lib/api.ts:66` 报错文案写死 `uvicorn app:app --port 8900`，应泛化为"请先启动 backend"。
5. `api.ts` 多处 `any`/`Record<string, any>`（如 `payload:any`、Chinese-keyed `NewsItem{新闻标题…}`），TS 严格性可加强；`NewsItem` 用中文属性名属风格不一致。
6. 后端 CORS `allow_origins=["*"]` + `allow_credentials=True`（main.py:22-28）属反模式；本地单用户桌面可接受，但公网/多用户部署前必须收紧。
7. `dist/index.html` 构建于 2026-08-23 18:27，主工作树 `frontend/src` 有文件（Layout.tsx 等）晚于此时间 → 若在主工作树重建并起服务，dist 会滞后于 src（对 worktree 的 8901 不影响，因其用自身 dist）。

### P2 — 卫生
8. `codex/cosmos-goal-2026-08-23` worktree 的远端已 gone（`[origin/codex/cosmos-goal-2026-08-23: gone]`），且 `.validation/cosmos-goal-clean-20260823-1/` 是未跟踪的源码快照副本——待清理。

## 4. 改进建议（下一步）

1. **杀掉 8900 僵尸**：`netstat -ano | findstr 8900` 取到 PID 后结束；确认无人依赖旧 Vibe-Research 后端。
2. **统一端口语义**：将 vite 代理默认目标改为 8901（或环境变量 `VITE_API_URL`），使 dev 与运行态打同一后端；或在文档明确"运行态用 8901，dev 代理需指向当前后端"。
3. **对齐工作树**：明确你"正在做"的是 `cosmos-goal` worktree 的 0.3.0。后续评审/修改应在该 worktree 内进行，或先把 origin 的 3 个 0.3.0 提交 pull 进 master 再统一。
4. **修 `api.ts:66` 文案** + 收紧 CORS（区分本地/公网配置）。
5. **补 CI 跑 pytest**（见 `CODE_REVIEW_2026-08-25.md` P0）：当前契约测试因重构路径问题 + CI 不跑 pytest 而腐化，需先修测试路径再接门禁。

## 5. 验证记录
- curl 8901/8900 health、8901/portfolio：均成功（版本号见上）。
- 前端路由/后端路由 grep 比对：61 端点一一对应。
- `base_app.py` 源码版本=0.2.0；worktree 同文件=0.3.0 → 证实 8901 源于 worktree。
- `dist` mtime 2026-08-23 18:27；主工作树 src 有更新文件 → 主树 dist 潜在滞后（对 8901 无影响）。

#!/usr/bin/env bash
# 2026-08-27 项目审阅产出的 P1/P2 issue 批量创建脚本（用后即删）
# 用法：bash scripts/create_review_issues_2026-08-27.sh
set -euo pipefail
REPO="Caesar-ZZh/cosmos"

gh issue create -R "$REPO" -t "P1 前端：上传研报走默认 30s 超时，大文件必被掐断" -b "$(cat <<'EOF'
## 问题
- `frontend/src/lib/api.ts:354` `uploadReport` 未传 `timeoutMs`，走 `request` 默认 30s；对照 `api.ts:95` 回测已专门设 65s，说明知道该坑但漏了上传。
- UI 文案承诺「单个 ≤ 25MB」（`pages/MyReports.tsx:119`），但 25MB 经 base64 膨胀约 33%，家宽上行（≤10 Mbps）普遍超 30s；且前端无任何 size 校验。

## 失败场景
用户拖入 20MB PDF → 30s 后 AbortController 触发报「请求超时」→ 重试永远同样失败。

## 修复方向
- 上传单独设 120s+ 超时
- 客户端 25MB 硬校验，超限直接提示不发请求

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅（代码审查代理 + 实测）*
EOF
)"

gh issue create -R "$REPO" -t "P1 前端：Intel「一键提炼全部要点」不可中止、无超时，可永久卡死" -b "$(cat <<'EOF'
## 问题
- `frontend/src/pages/Intel.tsx:60-87`：`genAll` 串行 `await genDigest(ind)` 循环 12 个赛道；调用 `chatStream` 时没传 `AbortSignal`（`llm.ts:61` 明明支持），也无超时。
- `lib/llm.ts` / `lib/ndjson.ts` 流式通道整体无 idle-timeout（REST 有 30s，流没有）。

## 失败场景
某赛道流挂起（后端 LLM 卡住/代理断流不发 FIN）→ 「提炼中 3/12」永久卡死、刷新按钮 disabled，只能刷新页面；期间订阅额度继续被消耗。`Notes.tsx:31-48` 反思流同样卸载不 abort。

## 修复方向
- Intel/Notes 传 AbortController，卸载与重置时 abort
- chatStream 加 90s 无 chunk 的 idle-timeout
- 对照 `AskAiButton.tsx:134` 的 abortRef 既有正确实现

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P1 后端：东财直连 latch 后失去回退 + 空结果被长缓存，瞬时故障放大成半小时静默无数据" -b "$(cat <<'EOF'
## 问题
- `server/stock/astock.py:487-510`：auto 探测固定 `direct` 后，`mode != \"auto\"` 分支只发一次请求、异常直接抛，永不再尝试代理；网络环境变化（开/关 Clash/VPN）后所有东财接口持续失败直到重启进程。
- `server/stock/base_app.py:568-575`：`_cached` 不区分「空是事实」与「空是失败」，`[]` 被当有效数据缓存 30 分钟；`astock.py:376-377` valuation_percentile 逐指标吞异常返回空壳也被缓存。

## 失败场景
启动时直连可用 → 用户午间开代理 → 融资融券/龙虎榜/解禁/大宗全部返回空并各缓存 30 分钟，UI 显示「无记录」——对投研工具比报错更危险。`debate.py:66-92` 已写 `_payload_empty` 防这个坑，但 HTTP 缓存层没有同款判断。

## 修复方向
- 参考 `market.py` 的负缓存（60s 失败短缓存）补进 base_app `_cached`
- 「确有内容」才长缓存，失败/空壳写短负缓存
- em_get latch 定期（如 10 分钟）重探

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P1 部署：run_server.py / 云部署不注入 VR_API_KEY，公网部署零鉴权" -b "$(cat <<'EOF'
## 问题
- `base_app.py:55-70` 支持 `VR_API_KEY` Bearer 鉴权，但真正的启动器 `run_server.py` 与 `deploy/cloud/start_nasdx.sh` 都只注入 `NASDX_*`（LLM 凭证），从不设置 `VR_API_KEY`。
- 结果：云部署后 /api/*（持仓、研报上传、chat、量化）对全网开放。

## 修复方向
- `run_server.py` / `start_nasdx.sh` 支持 `[auth] api_key` 配置段，存在即注入 `VR_API_KEY`
- deploy/cloud/README.md 部署步骤加「设置访问密钥」为必选项
- （关联：2026-08-27 已修复 CORS 双层架空与 SPA 路径穿越，本 issue 只剩鉴权接线）

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 前端：visibilitychange 与在飞请求竞态，轮询循环可翻倍叠加" -b "$(cat <<'EOF'
## 问题
- `frontend/src/hooks/useLiveQuotes.ts:141-178`、`hooks/useMarketPulse.ts:120-156`（两处同构）：`onVisible` 里 `clear(); void loop();` 只清定时器，不清正在 await 的 loop。快速 alt-tab 两次可叠加更多链，请求频率翻倍直至离开页面。

## 修复方向
loop 入口加 `if (timer !== null) return;` 或改「单定时器 + 代数计数」。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 前端：行业兜底数据领涨/领跌完全重叠" -b "$(cat <<'EOF'
## 问题
- `frontend/src/hooks/useMarketPulse.ts:15-33`：`top: descending.slice(0,30)` 与 `bottom: descending.slice(-30)`，行业数 ≤30 时两个 slice 是同一个数组。该函数正是 `/industry` 失败时的 fallback（`:73-79`）。

## 失败场景
盘中 `/industry` 持续超时 → SectorHeatBoard 左「领跌」右「领涨」显示完全相同的板块。

## 修复方向
按中位数切成互斥两半；或 sectors < 60 时只给 top 不给 bottom。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 前端：后端启动指引三处硬编码且互相矛盾（8900 vs 8901）" -b "$(cat <<'EOF'
## 问题
- `lib/api.ts:89`：`uvicorn server.main:app --port 8900`
- `lib/llm.ts:75`、`lib/ndjson.ts:30`：`uvicorn app:app --port 8900`（模块路径还是错的）
- `vite.config.ts:11`：代理默认 `127.0.0.1:8901`
- 401 提示文案同样三处复制（api.ts:79 / llm.ts:82 / ndjson.ts:37）

## 失败场景
新用户按报错提示执行 `uvicorn app:app --port 8900` → 模块路径错 + 前端代理指向 8901 → 永远「连接不到后端」。

## 修复方向
抽一个 `lib/errors.ts` 常量模块（启动指引 + 401 文案），端口从 env 读默认 8901。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 后端：行情源故障时持仓按 0 价计算，显示「全额浮亏」" -b "$(cat <<'EOF'
## 问题
- `server/stock/portfolio.py:131-146`：`tencent_quote` 抛异常被吞成 `quotes={}`，`price` 默认 0.0 → `market_value=0`、`pnl=-成本`，汇总按全额亏损计。

## 失败场景
腾讯行情源抖动 30 秒 → 持仓页显示账户接近亏光，引发恐慌。

## 修复方向
行情失败时该持仓标记 `unavailable`（价格显示 —、不计入汇总 pnl），而不是用 0 价计算。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 后端：AI 工具 query_market 的字段名与数据层不匹配，模型拿到 None" -b "$(cat <<'EOF'
## 问题
- `server/stock/tools.py:311`：emotion 取 `tiers/limitUp/limitDown/brokenRate/promoteRate`——实际字段是 `ladder/zt_count/dt_count/break_rate/promotion_rate`（靠 `{} or d` 侥幸回退，emotion 分支为死代码）。
- `tools.py:314`：turnover 取 `name/code/turnover/changePct`——实际是 `price/pct/amount`（`astock.py:563-568`），成交额榜没有成交额。

## 影响
AI 调工具拿到全 None 字段，回答质量静默劣化。

## 修复方向
对齐字段名 + 给 tools↔数据层加字段契约测试。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 后端：ETF 5 开头沪市代码被当深市（三处简化前缀判定）" -b "$(cat <<'EOF'
## 问题
`server/stock/astock.py` 三处用 `code.startswith(\"6\")` 简化判定市场前缀，而 `get_prefix`（:27-33）明确 5 开头是沪市基金：
- `:649` stock_fund_flow_120d
- `:757` concept_blocks
- `:779` hot_concepts

## 失败场景
查 510300 等沪 ETF 资金流/板块归属时 secid 拼成 `0.510300`，东财返回空 → 前端与 AI 工具显示「无数据」。

## 修复方向
三处统一改调 `get_prefix`。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 后端：Windows 下 CLI arg 投递超 CreateProcess 32K 限制" -b "$(cat <<'EOF'
## 问题
- `server/stock/cli_runtime.py:59`：`_MAX_ARG_BYTES = 110_000` 按 macOS/Linux 经验设定；Windows CreateProcess 单参数上限 32K。

## 失败场景
deepseek arg 投递在提示词超 ~32K 字符（system prompt 2.5K + 复盘 context 很容易超）时抛 `WinError 206 文件名或扩展名太长`，被包装成含糊的运行时错误。

## 修复方向
按平台区分阈值（win32 降到 30KB），或超限时自动改走 stdin 投递。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 服务端健壮性杂项（HEAD 405 / 流式断连不关上游 / 东财节流竞态 / 缓存无上限 / sys.path 双实例）" -b "$(cat <<'EOF'
2026-08-27 审阅发现的 P2 级健壮性小项，集中一个 issue 跟踪：

1. **HEAD 全 405**：所有路由只注册 GET，uptime 探活工具（默认 HEAD）误报服务挂了。
2. **流式断连不关上游**：`chat.py:209-216` `_call_llm_stream` 返回的 Response 无 try/finally close；客户端中途断连后与 LLM 上游的连接悬挂等 GC。对照 `cli_runtime.run_cli_stream` 有完整 finally 清理。
3. **东财 1 秒节流非线程安全**：`astock.py:493-495` `_em_last_call` 读-判断-sleep-写无锁；market 层预热/用户触发可并发击穿限流触发风控封 IP（debate.py 内部已串行规避，market 层没有）。
4. **内存缓存无上限无淘汰**：`base_app.py:417/437/455/565` 四个 `_CACHE` 只覆盖同 key 从不清理，长期运行缓慢增长。
5. **sys.path hack 模块双实例**：`server/main.py:14` 把 `server/stock` 插 sys.path 首位，`base_app` 可同时以两种身份被导入（双份缓存/锁/scheduler）；改为包内相对导入。
6. **盈利预测年份硬编码**：`astock.py:411-419` 只认 2026/2027，2028 年起 eps 字段静默变 null；按当前年动态推算。
7. **api_key 比较非常量时间**：`base_app.py:68` 用 `!=` 比对 Bearer，改 `hmac.compare_digest`。
8. **/api/quote codes 无数量上限**：`base_app.py:405-409` 可拼超长 URL 打腾讯源。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P2 DailyReview：非交易时段文案承诺「开盘后自动更新」，但页面无轮询机制" -b "$(cat <<'EOF'
## 问题
- `frontend/src/pages/DailyReview.tsx` `renderOverviewEmpty` 的非交易时段文案：「下一交易日开盘（X）后**自动更新**」——但页面 `useEffect([])` 只在挂载时加载一次，无任何轮询；开盘后数据不会自动刷新，除非用户手动刷新页面。

## 修复方向
- 交易时段内加定时轮询（对照 `useLiveQuotes` 的交易时段+可见性判定），或
- 文案改为「开盘后请手动刷新」

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

gh issue create -R "$REPO" -t "P3 文档：CHANGELOG 停在 2026-06-22，0.2.0→0.3.0 重大变更无记录" -b "$(cat <<'EOF'
## 问题
`CHANGELOG.md` 最新条目 2026-06-22。此后发生的重大变更均无记录：
- Vibe-Research → Cosmos 品牌迁移（0.2.0）
- 0.3.0：响应性收口、只读策略实验室（quant_service/quant_router）、市场看板韧性、iOS 客户端加入后又移除
- 本次 2026-08-27 的 P0 安全修复（SPA 路径穿越、merge key 锁定、CORS 单层化）

## 修复方向
按现有格式补三段 release 记录，并把「发版必更新 CHANGELOG」加进发布 checklist（`run_final_audit.py` 可顺带断言最新版本号出现在 CHANGELOG）。

---
*来源：2026-08-27 Cosmos 0.3.0 全面审阅*
EOF
)"

echo "ALL ISSUES CREATED"

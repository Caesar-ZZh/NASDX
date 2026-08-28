# Cosmos / NASDX 0.3.0 全面审阅报告（2026-08-27）

> 审阅对象：运行中的 `http://localhost:8901`（cosmos-api 0.3.0），代码位于
> `.worktrees/cosmos-goal-2026-08-23`（分支 `codex/cosmos-goal-2026-08-23`），
> 对照 GitHub `Caesar-ZZh/cosmos`。
> 方法：后端全量审查 + 前端全量审查 + 8901 运行态 33 项实测 + GitHub 状态核对 +
> DailyReview 数据链路精读 + 安全漏洞实证复现 + 契约测试执行。

## 总体结论

产品功能和工程质量明显高于个人项目平均水准（数据层缓存设计、错误降级、契约测试
文化都是亮点），daily-review 页面数据链路实测全部健康。但发现 **3 个 P0 级安全问题**，
在「发给朋友用的云部署」场景下会直接泄露 API key 和本地文件——上云前必须先修
（本报告随 P0 修复提交一并入库，修复见文末）。

---

## 一、P0 安全问题（实测/代码级确认）

### P0-1 SPA 静态托管路径遍历 → 任意文件读取【已修复】

`server/main.py`：`candidate = _DIST / full_path` 后直接 `is_file()` 返回，
无路径归一化校验。实测 4 种编码变体全部命中：

```
/%2e%2e%2f%2e%2e%2fserver%2fmain.py → 200，返回仓库根目录的服务器源码
/../package.json → 200，返回 dist 之外的文件
```

云部署时 `deploy/cloud/config.toml`（真实 key）与 `nasdx_history.db`（全部用户
数据）均可被公网读取。

**修复**：`(_DIST / full_path).resolve()` 后 `is_relative_to(_DIST)` 校验，
越界一律 404；契约测试 `tests/test_server_security_contracts.py`。

### P0-2 内置 LLM key 可被发送到攻击者任意 URL【已修复】

`server/stock/llm_cfg.py` merge 分支缺陷：请求换了 `baseURL` 但 `apiKey` 留空时
落入 else 分支——得到「服务端真实 key + 攻击者 URL」组合，`chat.py` 把
`Authorization: Bearer <内置key>` 发往该 URL。完整利用链（默认部署即可触发）：

1. 启动器不设 `VR_API_KEY` → `/api/chat` 无鉴权；
2. CORS 反射任意 Origin → 用户浏览器打开的任意恶意网页可直接 POST；
3. SSRF 防线只拦云元数据/内网 IP，公网攻击者域名放行；
4. 服务端主动把 key 送到攻击者服务器。

**修复**：换 `baseURL` 且未自带 key → `LlmConfigError`（上层转 HTTP 400）。
**保留约束**：前端不填任何配置时永远回退服务端默认 LLM（内置 key 兜底链路
不受影响，有专项契约测试锁定）。

### P0-3 真实 API key 明文出现在部署文档【已修复】

`deploy/cloud/README.md` 两处明文真实 Agnes key。文件未被 git 跟踪、
`config.toml` 已 ignore，但 README 不在 ignore 清单——一次 `git add deploy/`
就会提交进公开仓库。已替换为占位符。

---

## 二、P1 严重问题（已建 issue 跟踪）

| # | 问题 | 位置 |
|---|---|---|
| P1-1 | 双层 CORS 互相架空：main.py 无条件叠加 `allow_origins=["*"]`+`allow_credentials=True` 在外层，把 `VR_ALLOW_ORIGINS` 白名单收紧逻辑架空【已随 P0 修复：删除外层】 | server/main.py vs base_app.py |
| P1-2 | 部署链路零鉴权：启动器从不设置 `VR_API_KEY` | run_server.py / start_nasdx.sh |
| P1-3 | 东财直连 latch 后失去回退 + 空结果被当有效数据缓存 30 分钟，瞬时故障放大成半小时静默无数据 | astock.py / base_app.py `_cached` |
| P1-4 | 上传研报走默认 30s 超时，25MB 承诺必被掐断且前端无大小校验 | api.ts / MyReports.tsx |
| P1-5 | Intel「一键提炼」串行 12 赛道不可中止、流式通道无超时，可永久卡死 | Intel.tsx / llm.ts |

## 三、P2 问题（已建 issue 跟踪）

轮询 visibilitychange 双链叠加（useLiveQuotes/useMarketPulse）、行业兜底
领涨领跌重叠、启动指引三处硬编码矛盾（8900 vs 8901）、行情故障持仓按 0 价算
全额浮亏（portfolio.py）、AI 工具字段名与数据层不匹配（tools.py）、ETF 5 开头
前缀判定三处错误（astock.py）、Windows argv 32K 限制（cli_runtime.py）、
HEAD 全 405、流式断连不关上游、东财节流非线程安全、内存缓存无上限、
sys.path 双实例、盈利预测年份硬编码、DailyReview「自动更新」文案无轮询支撑。

## 四、DailyReview 页面专项

**数据链路（实测全部健康）**：页面无专用后端端点，由前端编排
`/api/indices` + `/api/global/indices` + `/api/market/overview` +
`/api/market/emotion` + `/api/market/turnover-top` + `/api/chat`。

| 端点 | 实测 | 耗时 |
|---|---|---|
| /api/indices | 200，4 指数 | 0.17–0.31s |
| /api/global/indices | 200，5 指数 | 11ms |
| /api/market/overview | 200（缓存） | 34ms |
| /api/market/emotion | 200，连板梯队 | 26ms |
| /api/market/turnover-top | 200 | 32ms |
| /api/chat（空配置） | 200 流式，内置 key 兜底 | 1.6s |

正确性验证：`_sectors()` 按净额**降序**，前端 `slice(0,6)` 流入 /
`slice(-6).reverse()` 流出语义正确；三态占位与四态空状态设计规范。

页面小问题：非交易时段文案承诺「自动更新」但无轮询（已建 issue）、
`globalIndices` 静默吞错、连板股 `+` 号硬编码、温度计未知档位静默降级。

## 五、GitHub 仓库与版本管理

1. **0.3.0 分支此前从未推送**（本次提交推送解决）；本地 master 带未推送的
   iOS 提交而 0.3.0 又移除了 iOS——两条线朝相反方向走，需合并决策（本次以
   0.3.0 工作树为准收口）。
2. 开放 issue：#12（P0 历史密钥轮换，与 secret scanning 0 告警不一致待人工
   确认）、#61、#72、#124。
3. Auto-Align 定时任务连续 12 次失败（缺 LLM secrets）——本次已移除 cron
   触发，保留手动 dispatch。
4. 主干 CI 健康：CodeQL / Security Scan / Final Audit Gate / Windows Checks
   全绿。

## 六、测试

- 0.3.0 新增契约测试（market resilience / quant API / quant batch）22/22；
- 新增安全契约测试 `test_server_security_contracts.py` 15/15；
- 全量套件仅 4 个失败，全部环境性（server venv 未装 streamlit 等桌面依赖，
  doctor/release-evidence 测试在 server-only 环境误报），无代码回归；
- `nasdx/overseas_sources.py:307` 的 `datetime.utcnow()` DeprecationWarning
  建议改 `datetime.now(timezone.utc)`。

## 七、亮点（值得保持）

- `market.py` 的 TTL + single-flight + 负缓存三件套是教科书级设计；
- 前端 `workspaceState` 版本号 + 逐字段白名单校验、AskAiButton 的 abort
  生命周期管理；
- 实测错误处理质量高：33 项无 500、无堆栈泄露、坏参数优雅降级；
- 合规意识（不荐股、零标的红线、免责声明）贯彻到字段级；
- 决策记录 / 三类复盘分离 / insert-only 契约等量化治理设计。

## 八、本次随报告落地的修复清单

1. `server/main.py`：SPA 路径穿越防护（resolve + is_relative_to，越界 404）；
   删除外层重复 CORS（保留 base_app 的 VR_ALLOW_ORIGINS 白名单单层）。
2. `server/stock/llm_cfg.py`：merge 收紧——换 baseURL 必须自带 key，否则
   `LlmConfigError`；空配置默认回退服务端 LLM 的链路原样保留。
3. `server/stock/base_app.py`：`_check_llm` 捕获 `LlmConfigError` 转 HTTP 400。
4. `tests/test_server_security_contracts.py`：上述全部行为的契约测试（15 项）。
5. `.github/workflows/auto_align.yml`：移除 cron 定时（缺 secrets 空跑失败），
   保留手动 dispatch。
6. `deploy/cloud/README.md`：真实 key 替换为占位符（该目录不入库）。

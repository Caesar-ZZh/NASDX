# NASDX × base 融合计划（本地化 · 自主产品）

> 基线：三仓库 zhangsa69 remote 已移除，纯本地。不与任何第三方账号交互。
> base = Vibe-Research(MIT)，融合时保留其 LICENSE 与原作者署名；web-system/platform 仅本地研究，不纳入对外产品。
> 不删现有 Streamlit/量化功能，双轨过渡，等价物验证通过后再弃用。

## 1. 融合后目标架构

| 层 | 技术 | 来源 | 说明 |
|---|---|---|---|
| 前端层 | React 19 + Vite | base（迁入 `frontend/`） | 取代 Streamlit 作为主 UI |
| API 层 | FastAPI（新增 `server/`） | base 接口模块并入 | 吸收 astock/market/portfolio/debate/gstock/newsradar/myreports/reflection |
| 内核层 | `nasdx/` + `quant/` | NASDX 现有 | 多 agent 分析、决策、组合、行情源、因子/回测/信号，被 API 与前端复用 |
| LLM 接入 | 统一 `nasdx/llm.py` | NASDX 现有 | 替代 base 各自的环境变量接入，AI 功能统一走此入口 |
| 桌面层 | `desktop/webview_shell` | NASDX 现有改造 | 改为加载 React 构建产物，替代 Streamlit |

## 2. 目标目录布局

```
NASDX/
├── server/                 # 新增：FastAPI 后端
│   ├── main.py             # FastAPI app + CORS + 挂载路由
│   └── stock/              # 从 base/backend 迁入（去 .git，保留 LICENSE 头）
│       ├── astock.py  market.py  portfolio.py  debate.py
│       ├── gstock.py  newsradar.py  myreports.py  reflection.py
│       ├── chat.py  tools.py  app.py(合并入 main)
├── frontend/               # 新增：base React 源码迁入（去 .git）
├── nasdx/                  # 保留：agent/决策/组合/行情源/llm
├── quant/                  # 保留：因子/回测/信号/ML/RL
├── desktop/                # 保留并改造：webview 加载 React
├── app.py  (Streamlit)     # 暂保留，Phase4 后降级/弃用
└── config.example.toml     # LLM 配置（统一接入）
```

## 3. 分阶段路线

| 阶段 | 内容 | 可验证产出 | 风险 |
|---|---|---|---|
| 0 本地化 | 移除 remote | 三仓库无 remote | 无（已完成） |
| 1 FastAPI 骨架 | 建 `server/`，并入 base 股票接口，复用 `nasdx/llm` | `/api/health` `/api/indices` `/api/quote` 通 | 低（已完成） |
| 2 React 接入 | base 前端迁入 `frontend/`，`server/main.py` 同进程托管前端静态 | 单命令起整套：`/` 返回 SPA、`/api/*` 真实行情、`/assets/*` 正确流式 | 中（已完成） |
| 3 内核复用 | debate/reflection/newsradar 改用 `nasdx/llm`+`nasdx/agents` | AI 功能走统一 LLM | 中（配置统一已完成） |
| 4 桌面切换 | `desktop/webview_shell` 加载 React，Streamlit 降级 | 桌面启动即 React 产品 | 中（已完成） |
| 5 清理 | .gitignore 排除第三方副本/运行数据，更新 AGENTS/README | 版本边界清晰、文档同步 | 低（已完成） |

## 4. 阶段 1 立即可做（不碰现有功能）

1. 建 `server/` 包，写最小 `main.py`（FastAPI + CORS 允许前端源）。
2. 把 base `backend/` 的接口模块复制进 `server/stock/`，保留文件头 LICENSE 注释。
3. 在 `server/main.py` 挂载这些路由（valuation/financials/margin/dragon-tiger/lockup/blocks/investor-qa/market/overview/emotion/turnover-top/global/*/radar/debate/reflect/portfolio/myreports/quote/indices…）。
4. LLM 入口改用 `nasdx/llm.py`（先桥接 base 的 chat 调用，Phase3 再深整合）。
5. 启动验证：`uvicorn server.main:app`，curl `/api/health`、`/api/indices`、`/api/quote?codes=600519`。

## 4.5 本地运行方式（Phase1+2 已就绪）

**单命令启动整套产品**（API + React 前端，同域免代理免 CORS）：

```bash
# 后端依赖（一次性）
python -m venv .venv && .venv/Scripts/activate
pip install -r server/requirements.txt

# 前端依赖 + 构建（一次性；dev 模式可跳过构建用 npm run dev）
cd frontend && npm install && npm run build && cd ..

# 启动（默认 8900）
python -m uvicorn server.main:app --host 127.0.0.1 --port 8900
# 浏览器打开 http://127.0.0.1:8900  → React 投研系统，调用 /api 实时行情
```

开发态（热更新）：另开一个终端 `cd frontend && npm run dev`（Vite 跑在 5899，其 `/api` 代理已默认指向 `127.0.0.1:8900`，即上面的 uvicorn）。

> 验证记录（沙箱内）：`/` 返回 Vibe-Research SPA 入口；`/api/health`→ok；`/api/indices` 与 `/api/quote?codes=600519` 返回真实实时行情；`/assets/index-*.js` 经 python 全量拉取 566686 字节、Range 分片 206 正常（curl 拉大文件在本沙箱有客户端限制，非服务端问题）。

## 4.6 LLM 统一配置（阶段3）

base 的 AI 功能（chat / debate / reflect）原本要求前端「接入 AI」页逐项填写 baseURL/apiKey/model 随请求传入。阶段3 新增服务端统一配置桥 **`server/stock/llm_cfg.py`**，优先级：

1. **请求体**（前端设置页自填，原能力保留）
2. **`NASDX_API_KEY` / `NASDX_BASE_URL` / `NASDX_MODEL`**（NASDX 统一凭证，与 `nasdx/llm.py` 同源）
3. **`VR_API_KEY` / `VR_BASE_URL` / `VR_MODEL`**（base 原有，兼容回退）
4. 都未配置 → 维持原 400「缺少模型配置」降级

实现：`_check_llm`（chat/debate/reflect 三端点共用）在请求体缺配置时用 `llm_cfg.merge_llm_cfg` 补齐；CLI 接入（cli-*）不参与兜底，原行为不变；base 的 function-calling 管道（chat.py requests 直连 + tools）完全未动。newsradar 本身无 LLM 调用（AI 提炼走 /api/chat），无需改造。

> 验证（沙箱内，假端点 `http://127.0.0.1:9/v1`）：无任何配置 → chat/debate 均 400；设 NASDX_* → chat/reflect 200+流内 error（错误含 port=9，证明配置注入并真实发起调用）、debate 走完 13 个底稿 section 后各阶段优雅降级 stage_done failed + done，不崩。

## 4.7 桌面 React 模式（阶段4）

现有 `desktop/launcher.py` 增加 `--mode react`（默认仍 streamlit，原行为不变）：

- **streamlit**：`python -m streamlit run app.py`（8501，原行为）。
- **react**：`python -m uvicorn server.main:app`（默认 8900），pywebview/浏览器加载 `http://127.0.0.1:8900/`（API + 前端同源）；启动前校验 `frontend/dist/index.html` 存在，未构建则提示先 `npm run build`；就绪探测用 `/api/health`。
- `desktop/runtime.py` 新增 `DEFAULT_REACT_PORT` / `build_server_command` / `react_frontend_ready` / `wait_for_server_ready`；`LaunchPlan` 增加 `mode` 字段。
- 新入口 **`启动NASDX桌面React.bat`**（`launcher.py --webview --mode react`），原 `启动NASDX桌面.bat`（control_panel + streamlit）不动。
- **LLM 闭环**：`build_desktop_env` 会把 `config.toml` 的 `[llm]`（api_key/base_url/model）注入子进程为 `NASDX_*` 环境变量，react 后端的 `llm_cfg` 直接读到——桌面 React 产品的 AI 功能（chat/debate/reflect）开箱即用。

> 验证（沙箱内）：`--dry-run --mode react` 输出 port 8900 + uvicorn 命令，且 `config_loaded_keys` 显示 NASDX_API_KEY/BASE_URL/MODEL 已从 `%APPDATA%\NASDX\config.toml` 加载；`--headless-smoke --mode react --timeout 60` 起服务→/api/health 就绪→页面 200→停，exit 0；默认 streamlit dry-run 保持原样。

## 4.8 版本管理边界（阶段5）

- `.gitignore` 新增排除：`_reverse_miaoou/`（第三方研究副本）、`.workbuddy/`、`deliverables/`、`.audit_state.json`、`.cache/`、`.data/`。`frontend/` 自带的 .gitignore 排除 node_modules/dist。
- **不删 `_reverse_miaoou` 三个子 .git**：它们是第三方项目 clone 的本地副本，保留 git 元数据便于对照上游与 LICENSE；整目录排除即可，删除属多余破坏（与"不碰别人作品"基线一致）。
- 应纳入版本库（当前 untracked，待提交）：`frontend/`、`server/`、`docs/convergence-plan.md`、`启动NASDX桌面React.bat`；已修改：`desktop/launcher.py`、`desktop/runtime.py`、`AGENTS.md`、`README.md`、`.gitignore`。
- 注：`nasdx/overseas_sources.py` + `tests/test_overseas_sources_contracts.py` 为 #106 内容（已推送 fix/auto-align-106-recover 分支），master 未含，保持现状由用户决定去留。
- 验证：`git -c gc.auto=0 status --short` 仅剩上述待提交项；`git check-ignore` 抽查全部命中（_reverse_miaoou/.workbuddy/deliverables/.audit_state.json/.cache/.data/frontend-node_modules/frontend-dist）。

## 5. 合规与风险

- base MIT：保留 LICENSE 与原作者署名（simonlin1212 / Vibe-Research）。
- 外部依赖：akshare/mootdx 缺失时端点 501 降级（base 已设计），不影响启动。
- 数据合规：沿用 base 红线（零标的、客观榜单只呈现、AI 只喂数据）。

## 6. 明确不做

- 不 push 到任何远程（含 Caesar-ZZh 也仅本地，除非你后续显式要求）。
- 不删现有 Streamlit 页面，直到 React 等价物验证通过。
- 不把 web-system/platform 代码纳入 NASDX。
- 不与 zhangsa69 / simonlin1212 账号产生任何交互。

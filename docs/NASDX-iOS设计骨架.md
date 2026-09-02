# NASDX iOS · 整体设计与制作骨架

> 配套资源库：`NASDX/.workbuddy/skills/IOS开发/`（已克隆 psx_sockets / design-foundation / Hero / awesome-ios-design-md）
> 配套方法论：`docs/NASDX-iOS开发路线图.md`（三维度梳理 + 合规红线）
>
> **核心判断（贯穿全文）**：iOS 是**复用现有 FastAPI 后端的原生 SwiftUI 客户端**，不是把 Python 重计算搬进手机。AkShare/因子/回测/LLM 全留服务端；客户端只做「展示 + 轻交互 + 本地缓存」。

---

## 1. 架构原则

| 原则 | 说明 |
|---|---|
| 服务端centric | 行情/因子/回测/LLM 全在服务端；客户端通过 `/api/v1/ios/*` 取已加工数据 |
| 原生优先 | SwiftUI + Swift 6 严格并发；跨平台框架（Flutter/RN）不适合金融实时与审核安全 |
| 可离线 | URLCache + SwiftData 行情快照，弱网/行情间隙仍可看历史 |
| 合规前置 | 客户端不持有 LLM key；金融内容标注「非投资建议」；隐私清单齐全 |
| 设计系统驱动 | token 化配色/字体/间距，取自 `awesome-ios-design-md` 同类 App，红涨绿跌按中国惯例反转 |

---

## 2. 技术栈与版本基线

| 项 | 选择 | 约束 |
|---|---|---|
| 语言 | Swift 6（严格并发，Sendable 红线） |  |
| UI | SwiftUI（iOS 17+） | `@Observable` / `#Predicate` / `SwiftData` |
| 架构 | MVVM + `@Observable`；复杂流程再上 TCA | 起步不上 TCA，避免过度设计 |
| 网络 | `URLSession` + `async/await`；复杂场景 Alamofire | 实时行情用 WebSocket（Starscream） |
| 图表 | 官方 `Swift Charts` | K线/分时/组合净值 |
| 持久化 | `SwiftData`（iOS 17+） | 复杂 SQL 才用 GRDB |
| 动效 | `Hero`（列表→详情过渡）+ `lottie-ios`（引导） |  |
| 安全 | `KeychainAccess` 存 token；`LocalAuthentication` 生物识别 |  |
| 构建/上架 | Xcode 26 SDK（2026-04 起强制） | 最低部署 iOS 17 |
| 分析 | `TelemetryDeck`（隐私优先，替代 Firebase） |  |

---

## 3. 模块分层（客户端）

```
NASDX (App)
├─ App/            启动、环境装配、根导航、路由
├─ Core/
│  ├─ Network/     APIClient、Endpoint、APIError、Auth
│  ├─ Models/      Stock / Quote / KLine / WatchlistItem / MarketOverview
│  ├─ Design/      DesignTokens（颜色/字体/间距）、SemanticColor
│  ├─ Storage/     Persistence（SwiftData 容器 + 缓存模型）
│  └─ Realtime/    QuoteSocket（Starscream 封装）
└─ Features/
   ├─ Market/      行情总览、涨幅榜、指数
   ├─ Quote/       个股详情（K线/分时/财务/新闻）
   ├─ Watchlist/   自选管理
   ├─ Portfolio/   持仓（可选，v2）
   └─ Settings/    关于/免责/数据源切换
```

单向数据流：`View → ViewModel(@Observable) → APIClient/QuoteSocket → Model → SwiftData(缓存)`。ViewModel 永不直接持有 UIKit。

---

## 4. 数据流（iOS ↔ FastAPI）

```
[iOS ViewModel]
   │  async/await
   ▼
APIClient (URLSession,  bearer token from Keychain)
   │  HTTPS
   ▼
FastAPI /api/v1/ios/*   ← 新增契约层，复用 server/stock 的 astock/market/portfolio
   │
   ├─ astock.tencent_quote(codes)      实时行情
   ├─ astock.kline(code, category)     K线
   ├─ market.get_overview()            市场总览
   ├─ market.get_turnover_top()        成交额榜
   ├─ portfolio.get_portfolio()        持仓
   └─ (LLM 走 /api/chat 经服务端转发)
```

**契约层职责**：把服务端 snake_case / 嵌套结构 reshaped 成移动端友好的 camelCase、分页、字段收敛，减少客户端解析负担；并对缺失数据源做兜底（返回 `partial: true`）。

---

## 5. 设计系统（DesignTokens）

- 配色取自 `IOS开发/repos/awesome-ios-design-md/design-md/finance/`（robinhood/webull/binance/coinbase 的 `DESIGN-swiftui.md`）→ 抄 `Color`/`Font` 扩展代码。
- **红涨绿跌（中国惯例）必须反转**：国际 App 多为绿涨红跌，落地时把 `up` 语义绑定红色、`down` 绑定绿色（见 `Core/Design/DesignTokens.swift` 的 `semanticUp`/`semanticDown`）。
- 暗色模式：服务端 UI 用暗色为主（金融 App 惯例），token 同时定义 light/dark。
- 设计感与丝滑：列表→详情用 `Hero` 的 `hero.id` 配对做 Magic Move 过渡；关键数值用 `.contentTransition(.numericText())` 做实时跳动。

---

## 6. API 契约（v1，移动端友好）

| 方法 | 路径 | 说明 | 复用 |
|---|---|---|---|
| GET | `/api/v1/ios/health` | 健康检查 + 服务版本 | — |
| GET | `/api/v1/ios/quote?codes=600519,000001` | 批量实时行情（camelCase） | `astock.tencent_quote` |
| GET | `/api/v1/ios/kline/{code}?category=4&offset=60` | K线序列 | `astock.kline` |
| GET | `/api/v1/ios/market/overview` | 指数 + 市场情绪 + 成交额榜 | `market.get_overview` 等 |
| GET | `/api/v1/ios/watchlist?codes=...` | 自选快照（批量 quote 聚合） | `astock.tencent_quote` |
| GET | `/api/v1/ios/portfolio` | 持仓概览 | `portfolio.get_portfolio` |
| WS  | `/api/v1/ios/stream?codes=...` | 实时推送（未来；当前先用轮询 quote） | — |

> 实时性策略：v1 用「定时轮询 `/quote`」即可撑住原型；若需真正 push，再在后端加 WebSocket 端点，iOS 端用 `QuoteSocket` 接入。

---

## 7. 合规要点（上架前必查）

- **隐私清单**：所有第三方库须有 `PrivacyInfo.xcprivacy`，否则 ITMS-91053 拒。
- **5.1.2(i)**：若数据/内容经第三方 AI，须披露 + 用户同意；本项目 LLM 走服务端转发，隐私标签写明。
- **5.1.1 高度监管**：金融 App 中国区可能需证券投资咨询资质；定位做成「数据展示 + 自研工具 + 信息聚合」，避免明示买卖建议，附免责声明。
- **行情数据版权**：腾讯/东方财富行情有使用条款，商用前评估授权。
- **4.3(b) 低质清理**：确保功能独特、持续更新。
- **4.5.3**：价格预警用 APNs 通知，不滥用 Live Activity。
- **Sign in with Apple**：若有第三方登录必须提供。

---

## 8. 制作骨架交付物

| 路径 | 内容 |
|---|---|
| `server/ios_api.py` | 后端 iOS 契约路由（可运行，已接入 base_app） |
| `ios/NASDX/...` | Swift 源文件骨架（App/Network/Models/Design/Storage/Realtime/Features） |
| `ios/README.md` | 如何在 Xcode 打开、SPM 依赖清单、指向后端地址 |
| `ios/Package.swift` | 本地 Core 层 SPM 包（可选，便于单元验证） |

---

## 9. 里程碑（建议顺序）

1. **M0 后端契约**：`/api/v1/ios/*` 跑通 + 设计文档定稿（本次交付）。
2. **M1 最小客户端**：行情总览 + 个股详情(K线) + 自选 + SwiftData 缓存（骨架已含）。
3. **M2 丝滑体验**：Hero 转场 + 实时轮询 + 暗色设计系统。
4. **M3 闭环**：持仓/组合、推送预警、Widget、上架合规自查。

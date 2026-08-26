# NASDX → iOS App 全面开发梳理与 GitHub 资源清单

> 适用对象：把 NASDX（A 股量化 / 多 Agent 投研系统，Python + Streamlit + FastAPI）做成 iOS App
> 生成日期：2026-08-26
> 核心判断：**iOS App 是原生客户端，复用现有 FastAPI 后端；重计算（AkShare / 因子 / 回测 / LLM）留在服务端。**

---

## 0. 项目现状与核心架构判断（必须先看）

### 0.1 NASDX 当前技术面（已确认）
- 语言/框架：Python + Streamlit（UI）+ FastAPI（`server/main.py`，同源托管于 8900）
- 数据层：AkShare / mootdx 拉 A 股行情（**需 Python + 中国网络，无法在 iOS 端直接调用**）
- 智能层：多 Agent 投研通过 Agnes AI（OpenAI 兼容 LLM）在后端完成
- 已有后端接口（`server/stock/`）：`astock`(A股)、`market`(行情)、`portfolio`(组合)、`debate`(多Agent辩论)、`gstock`(全球)、`chat`(对话)、`newsradar`(新闻)、`myreports`(报告)、`reflection`(反思)
- 前端已有 React 19 / Vite 版（`frontend/`），与后端通过 `/api` 通信

### 0.2 推荐目标架构（一句话：thin native client）
```
┌─────────────┐   HTTPS/JSON    ┌──────────────────────┐   AkShare/mootdx
│  iOS App    │ ──────────────► │  NASDX FastAPI :8900  │ ─────────────► (A股行情)
│ SwiftUI     │ ◄────────────── │  已有，几乎不用改     │      Agnes AI
│ SwiftData   │   (可加 /ios/*  │  + 鉴权/限流/缓存     │ ─────────────► (LLM分析)
└─────────────┘   专用契约)     └──────────────────────┘
```
**为什么必须这样**：① AkShare 依赖 Python 与中国网络环境；② 因子/回测吃算力，手机跑不动也不该跑；③ LLM key 不能进客户端；④ 后端已存在，复用成本最低、风险最小。

### 0.3 关键合规红线（A 股金融 App 特有，务必先评估）
| 风险点 | 说明 | 建议 |
|---|---|---|
| 证券投资咨询资质 | 在中国区上架「提供个股分析/买卖建议」类 App，可能被要求《证券投资咨询业务资格》 | 定位为「数据展示 + 自研工具 + 信息聚合」，避免明示买卖建议；加免责声明 |
| 行情数据版权 | AkShare 抓取公开行情，再分发可能涉及交易所数据版权 | 后端仅服务自有用户、不转售；咨询法务 |
| App Store 5.1.1 | 金融/加密交易属「高度监管领域」，需资质证明或明确免责 | 上架前准备资质/免责材料 |
| 5.1.2(i) 第三方 AI | 若把用户数据发往第三方 AI（Agnes/OpenAI 等），须明确披露并取得同意 | 经自有后端转发，隐私标签写明，加同意弹窗 |

---

## 1. iOS 开发 Skills（技术实现）

### 1.1 技术选型：原生 vs 跨平台
| 维度 | 原生 Swift + SwiftUI（**推荐**） | Flutter / React Native |
|---|---|---|
| 性能 | 最佳，A股实时刷新/K线流畅 | 中，复杂图表/动画有损耗 |
| 平台特性 | 完整（Live Activities、Widget、SharePlay） | 延迟，需桥接 |
| 审核安全 | 合规风险低 | 跨平台曾被质疑「模板化」(4.3(b)) |
| 团队匹配 | 需学 Swift（你现有是 Python） | 可复用 Web 经验（已有 React） |
| 结论 | **金融数据 App 首选原生** | 仅当要同步 Android 且人手紧才考虑 |

> 补充：你已有 React 前端，理论上 React Native 能复用一部分，但 A 股 App 对实时/K线/系统特性要求高，**原生 SwiftUI 长期更稳**。若坚持跨端，Flutter 的图表生态（fl_chart）比 RN 更成熟。

**部署基线（2026 强制）**：
- 最低部署目标建议 **iOS 17+**（解锁 SwiftData / @Observable / #Predicate）
- 自 **2026-04 起所有提交必须用 Xcode 26 SDK 构建**（否则自动拒）
- Swift 6 严格并发（Sendable）已成红线，选库先看是否支持

### 1.2 架构设计
- **默认 MVVM + @Observable（iOS 17+）**：最贴合 SwiftUI 响应式，样板少、可测试。
- **何时上 TCA（pointfreeco/swift-composable-architecture）**：复杂多步流程（如开户向导、下单）、>5 人团队、需穷尽测试。代价是代码量 +40~60%。NASDX 初期用 MVVM 足够。
- **分层（建议目录）**：
  ```
  NASDX/
  ├─ Views/            # SwiftUI 视图，只渲染+转发事件
  ├─ ViewModels/       # @Observable，状态+业务编排
  ├─ Services/         # NetworkService(调 FastAPI)、AuthService
  ├─ Models/          # Decodable 结构体（与后端 JSON 对齐）
  ├─ Data/            # SwiftData ModelContainer、本地缓存
  ├─ DesignSystem/    # 颜色/字体/间距 token（统一视觉）
  └─ Resources/       # Assets、Info.plist、PrivacyInfo.xcprivacy
  ```
- **依赖注入**：用 `.environment(service)` 注入，避免全局单例（难测试）。
- **导航**：`NavigationStack` + `NavigationPath`（类型安全、支持 deep link）。

### 1.3 内存管理与异步处理
- **Swift Concurrency 为主**：`async/await` 替代回调；重活放进 `actor` 或 `Task.detached`。
- **@MainActor** 标注 UI 更新；网络/解析标 `@Sendable`，满足 Swift 6 严格并发。
- **大列表**：`LazyVStack` / `List` + `.id`，避免一次性建全量视图。
- **图片**：`Kingfisher`/`Nuke` 异步加载 + 内存/磁盘双缓存。
- **后台导入/批量写库**：用独立 `ModelContext`（actor 内），分批 `save()`，防内存膨胀。
- **防主线程阻塞**：模型加载、JSON 大解析、SwiftData 批量插都不要放主线程。

### 1.4 NASDX 专属技术注意点
| 功能 | 实现建议 | 关联后端接口 |
|---|---|---|
| 实时行情 | WebSocket（`Starscream`）或轮询 + SwiftData 本地缓存快照 | `market` / `astock` |
| K线/图表 | **Swift Charts（官方，iOS 16+）** 画 K 线/均线/成交量；复杂自定义用 Metal | `astock` |
| 多 Agent 分析报告 | 后端 `debate`/`reflection`/`myreports` 返回 Markdown → `MarkdownUI` 渲染 | `debate`/`reflection`/`myreports` |
| 新闻雷达 | 列表 + 下拉刷新 + 已读状态本地存 | `newsradar` |
| 自选/组合 | SwiftData 存本地自选，定期向后端拉最新值 | `portfolio` |
| LLM 对话 | **必须经后端转发**，客户端不持 key；引 5.1.2(i) 披露 | `chat` |

### 1.5 GitHub 技术资源清单（核心用途 + 适用场景）
> ⚠️ Star 数为参考量级，会漂移，以 GitHub 实时为准；优先选 SPM 分发、近 6 个月有提交、支持 Swift 6 的库。

| 资源 | 类型 | 核心用途 | 适用场景 | 许可 |
|---|---|---|---|---|
| [apple/swift-composable-architecture](https://github.com/pointfreeco/swift-composable-architecture) | 架构 | TCA 单向数据流、强测试 | 复杂流程/大团队 | MIT |
| [Alamofire/Alamofire](https://github.com/Alamofire/Alamofire) | 网络 | 复杂 HTTP（重试/证书/上传） | 简单用 URLSession 即可，复杂才上 | MIT |
| [daltoniam/Starscream](https://github.com/daltoniam/Starscream) | 网络 | WebSocket 实时行情 | 实时推送/K线流 | MIT |
| [onevcat/Kingfisher](https://github.com/onevcat/Kingfisher) | 图片 | 异步图片加载+缓存 | 头像/资讯配图 | MIT |
| [kean/Nuke](https://github.com/kean/Nuke) | 图片 | 高性能图片（NukeUI） | 性能敏感列表 | MIT |
| [siteline/SwiftUI-Introspect](https://github.com/siteline/SwiftUI-Introspect) | UI 桥接 | 访问底层 UIKit 做细控 | 标准 SwiftUI 不够时 | MIT |
| [airbnb/lottie-ios](https://github.com/airbnb/lottie-ios) | 动效 | 设计师矢量动画 | 引导/Loading/庆祝 | Apache-2.0 |
| [kishikawakats/KeychainAccess](https://github.com/kishikawakats/KeychainAccess) | 安全 | Keychain 简洁封装 | 存 token/密钥 | MIT |
| [RevenueCat/purchases-ios](https://github.com/RevenueCat/purchases-ios) | 支付 | 内购/订阅管理 | 若做付费会员 | MIT |
| [TelemetryDeck/telemetrydeck-swift](https://github.com/TelemetryDeck/telemetrydeck-swift) | 分析 | 隐私优先埋点 | 产品指标（替代 Firebase） | MIT |
| [MacPaw/OpenAI](https://github.com/MacPaw/OpenAI) | LLM | 直连 OpenAI 兼容 API | **仅当**客户端直连 LLM（需 5.1.2(i) 披露） | MIT |
| [apple/swift-log](https://github.com/apple/swift-log) | 日志 | 统一日志 + OSLog | 全项目 | Apache-2.0 |
| [pointfreeco/swift-snapshot-testing](https://github.com/pointfreeco/swift-snapshot-testing) | 测试 | UI 快照测试 | 防视觉回归 | MIT |
| [nalexn/ViewInspector](https://github.com/nalexn/ViewInspector) | 测试 | SwiftUI 单元测试 | ViewModel/视图断言 | MIT |
| [JohnEstropia/GRDB.swift](https://github.com/JohnEstropia/GRDB.swift) | 数据库 | SQLite（SQL 级控制） | 需复杂 SQL/既有 schema | MIT |
| [vsouza/awesome-ios](https://github.com/vsouza/awesome-ios) | 索引 | iOS 资源总目录 | 查库/找方案 | CC-BY |
| [matteocrippa/awesome-swift](https://github.com/matteocrippa/awesome-swift) | 索引 | Swift 资源总目录 | 查库/找方案 | MIT |
| [Toni77777/awesome-swiftui-libraries](https://github.com/Toni77777/awesome-swiftui-libraries) | 索引 | SwiftUI 组件库大全 | 找 UI 组件 | — |

### 1.6 模板 / 示例项目（直接学架构）
| 项目 | 核心用途 | 适用场景 |
|---|---|---|
| [nalexn/clean-architecture-swiftui](https://github.com/nalexn/clean-architecture-swiftui) | Clean Arch + MVVM 范例 | 学习分层/单向流 |
| [dimillian/MovieSwiftUI](https://github.com/dimillian/MovieSwiftUI) | Combine + 单向数据流实战 | 学网络/状态管理 |
| [sarimk80/psx_sockets](https://github.com/sarimk80/psx_sockets) | **实时股票 SwiftUI + WebSocket + 图表 + Core Data** | **最贴近 NASDX 的参考案例** |
| [dkhamsing/open-source-ios-apps](https://github.com/dkhamsing/open-source-ios-apps) | 5万★ 开源 App 索引（按 Finance/Health 分类） | 找同类 App 抄架构 |
| [FinSightAI](https://flutdev.blogspot.com/2026/02/ios-development-from-first-principles.html) | AI 金融教育 App（MVVM + Gemini + Markdown 渲染） | 学「LLM+金融+SwiftUI」集成 |

---

## 2. UI/UX 设计 Skills（界面体验）

### 2.1 必须遵循的 Apple HIG 要点
- **三大原则**：清晰度（Clarity）、依从性（Deference，内容优先）、深度（Depth，层级/模糊）。
- **系统字体与动态类型**：用 `Text` 默认样式 + `Dynamic Type`，支持用户放大字号（无障碍合规）。
- **SF Symbols**：图标优先用系统符号，风格统一、自动适配粗细/尺寸。
- **深色模式**：用 `Color(.systemBackground)` 等语义色，避免硬编码；`@Environment(\.colorScheme)` 自适应。
- **安全区**：用 `safeAreaInset` / `.toolbar`，不挡刘海/灵动岛/Home Indicator。
- **触觉反馈（Haptics）**：关键操作（加自选、预警触发）给 `.feedback`，提升质感。
- **手势**：遵循系统惯例（边缘返回、下拉刷新），不自定义反直觉手势。

### 2.2 组件库 / 设计系统 / 图标资源
| 资源 | 核心用途 | 适用场景 | 许可 |
|---|---|---|---|
| [SwiftUIX/SwiftUIX](https://github.com/SwiftUIX/SwiftUIX) | 补齐标准库缺失的 SwiftUI API | 统一控件/便捷修饰符 | MIT |
| [NerdSnipe-Inc/design-foundation](https://github.com/NerdSnipe-Inc/design-foundation) | **token 化主题引擎 + 25 组件**，一套主题统全盘 | 快速建立一致设计系统（含 AGENTS.md 给 AI 用） | MIT |
| [Salesforce/SharedUI-iOS](https://github.com/salesforce/SharedUI-iOS) | 完整设计系统 + 无障碍优先组件 | 企业级一致 UI、Dark Mode、Dynamic Type | BSD |
| [NoIdentity-AG/Kolibri](https://github.com/NoIdentity-AG/kolibri-swiftui) | 轻量可复用 SwiftUI 组件 | 快速搭原型 | 开源 |
| [alexaubry/BulletinBoard](https://github.com/alexaubry/BulletinBoard) | 底部卡片式引导/配置 | 新手引导、权限申请 | MIT |
| [AlertToast](https://github.com/elai950/AlertToast) | Apple 风格 Toast/HUD | 操作反馈 | MIT |
| [SwiftKickMobile/SPAlert](https://github.com/SimformSolutionsPvtLtd/...) | 原生感 Alert/Toast | 提示 | MIT |
| [airbnb/lottie-ios](https://github.com/airbnb/lottie-ios) | 设计师动画 | 引导/空状态 | Apache-2.0 |
| **SF Symbols（Apple 官方）** | 系统图标 | 全 App 图标 | Apple 许可 |
| [SwiftGen/SwiftGen](https://github.com/SwiftGen/SwiftGen) | 资源（颜色/字符串/图片）类型安全生成 | 多语言/主题管理 | MIT |

> 建议：NASDX 直接基于 **DesignFoundation 或自建 token 体系**（颜色/字号/间距集中管理），避免 AI 辅助生成时组件样式漂移——这点对长期维护极关键。

### 2.3 多尺寸屏幕适配
- **设备矩阵**：iPhone SE(4.7") → 标准 → Pro Max(6.9")；iPad(11"/13") + Stage Manager 多窗口。
- **策略**：
  - 用 `GeometryReader` / `Grid` / `HStack` 自适应，而非固定 frame。
  - `size classes`（`@Environment(\.horizontalSizeClass)`）：iPad 上转双栏（列表+详情），iPhone 单栏。
  - iPad：支持 `NavigationSplitView`、多窗口（`Scene` 多实例）、Apple Pencil 可选。
  - 横竖屏：行情/K线允许横屏全屏；设置类竖屏。
  - 大屏字号/对比度：用语义色 + Dynamic Type，避免写死 px。
- **测试**：Xcode Preview 同时挂 SE / Pro Max / iPad 尺寸；真机覆盖最小/最大屏。

---

## 3. 产品设计 Skills（功能与体验）

### 3.1 App Store 上架与 2026 审核新规
| 规则 | 要点（2025–2026 更新） | 对 NASDX 的影响 |
|---|---|---|
| **Xcode 26 SDK** | 2026-04 起强制，新提交/更新必须用 iOS 26 SDK 构建 | 构建管线需升级到 Xcode 26 |
| **Privacy Manifest** | `PrivacyInfo.xcprivacy` 每个第三方库都要有，否则 `ITMS-91053` 自动拒 | 提交前 `Product → Generate Privacy Report` 全量核查 |
| **5.1.2(i) 第三方 AI** | 用户数据发往第三方 AI 须披露+同意 | LLM 走后端转发，隐私标签写明 |
| **5.1.1 高度监管** | 金融/加密交易需资质/免责 | 准备金融类免责 + 必要资质 |
| **4.3(b) 低质清理** | 2026-06 起苹果可下架「无差异」存量 App | 确保功能独特、持续更新，避免模板感 |
| **4.5.3 Live Activities** | 禁止用 Live Activity 发垃圾/推广 | 价格预警用通知而非 Live Activity 刷屏 |
| **年龄分级** | 2025-07 更新 13+/16+/18+，2026-01-31 前须完成 | 金融 App 通常 4+ 或 12+，按时填问卷 |
| **Sign in with Apple** | 若提供第三方登录（微信/Google）则必须同时提供 | 建议直接用 Sign in with Apple |
| **审核周期** | 常规 1–2 天，高峰期 2–4 周 | 预留缓冲，先 TestFlight 内测 |

> 关键动作：上架前准备 **隐私营养标签**、**隐私清单**、**测试账号**、**金融免责声明**、**AI 数据使用同意页**。

### 3.2 功能设计要点
| 功能 | 设计要点 | 推荐实现 |
|---|---|---|
| **用户引导** | 首启解释「这是什么/能做什么」，权限（通知）按需申请 | `BulletinBoard` 卡片式引导 |
| **数据持久化** | 自选股/阅读进度/设置本地存，行情可缓存快照离线看 | **SwiftData（iOS17+）**，`@Query` 自动刷新 UI；需 SQL 级控制用 GRDB |
| **推送通知** | 价格预警、组合异动、Agent 报告完成 | APNs（需自有后端推）；注意 4.5.3 不滥用 |
| **认证** | 账号体系 + Face ID / Touch ID 二次验证 | `LocalAuthentication` + Keychain 存凭证 |
| **离线/缓存** | 地铁里也能看已加载行情 | `URLCache` + SwiftData 本地行情快照 |
| **分享** | 报告/个股页可分享 | `UIActivityViewController` / ShareLink |
| **Widget** | 自选股涨跌幅桌面组件 | `WidgetKit` + SwiftData App Group 共享 |

### 3.3 GitHub 产品/设计参考案例
| 项目 | 核心用途 | 适用场景 |
|---|---|---|
| [dkhamsing/open-source-ios-apps](https://github.com/dkhamsing/open-source-ios-apps) | 5万★ 开源 App 总索引（Finance 分类） | 找金融类 App 抄交互 |
| [dimillian/IceCubesApp](https://github.com/dimillian/IceCubesApp) | 7000★+ 纯 SwiftUI 生产级社交客户端 | 学大型 SwiftUI App 架构/多账号 |
| [sarimk80/psx_sockets](https://github.com/sarimk80/psx_sockets) | 实时股票 SwiftUI 完整案例 | **最贴近 NASDX 的产品参考** |
| [Escudo](https://dev.to/sugarhashira/escudo-232b) | 隐私优先个人理财，全本地 Keychain | 学「金融+隐私+本地优先」设计 |
| [Expenso-iOS](https://blog.csdn.net/gitblog_00493/article/details/155152422) | SwiftUI + Core Data CRUD 范例 | 学本地数据增删改查 |
| [NerdSnipe-Inc/design-foundation](https://github.com/NerdSnipe-Inc/design-foundation) | token 化设计系统（含 AI 规则文件） | 直接落地一致 UI |

---

## 4. 可执行落地路线图（里程碑）

| 阶段 | 目标 | 关键交付 | 依赖 |
|---|---|---|---|
| **M0 契约对齐** | 定义 iOS 专用 API | 在 `server/main.py` 下加 `/ios/*` 路由（鉴权/限流/字段裁剪），输出 OpenAPI | 现有 FastAPI |
| **M1 最小客户端** | 行情列表 + K线 + 自选 | SwiftUI App + SwiftData 缓存 + Kingfisher | M0 |
| **M2 分析页** | 调后端 Agent/LLM 渲染报告 | MarkdownUI 渲染 `debate`/`myreports` + 引导页 | M1 |
| **M3 预警/推送** | 价格预警 + APNs | 后端推 + 客户端通知 + Keychain 登录 | M1 |
| **M4 上架** | TestFlight + App Store | 隐私清单、营养标签、金融免责、AI 同意页 | M1–M3 |

### 立刻可做的小事
1. 在 `server/` 探活 `localhost:8900/docs`（FastAPI 自带 Swagger），把现有接口字段截图存档 → iOS 模型直接对齐。
2. 用 `sarimk80/psx_sockets` 作为「实时股票 SwiftUI」的脚手架参考。
3. 设计系统先用 `DesignFoundation` 起一套暗/亮 token，统一后续所有视图。

---

## 5. 一句话总结
**不要重写，要复用**：把 NASDX 的 FastAPI 当后端，iOS 端做原生 SwiftUI 客户端，重计算/数据/LLM 全留服务端；技术栈选 SwiftUI + MVVM + SwiftData，合规上重点盯 Xcode 26 SDK、隐私清单、5.1.1/5.1.2(i) 与金融资质。最值得直接 clone 学习的案例是 `psx_sockets`（实时股票）和 `open-source-ios-apps`（金融分类索引）。

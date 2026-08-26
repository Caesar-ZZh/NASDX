# NASDX iOS 客户端骨架

> 配套：后端契约 `server/stock/ios_api.py`（已接入 `base_app.py`，路由 `/api/v1/ios/*`）
> 设计文档：`docs/NASDX-iOS设计骨架.md` ｜ 参考库：`NASDX/.workbuddy/skills/IOS开发/`

## 1. 在 Xcode 打开（推荐路径）

1. Xcode 26（macOS 15+）新建 **iOS App** 项目：
   - Product Name: `NASDX`
   - Interface: **SwiftUI** ｜ Language: **Swift** ｜ **Storage: SwiftData**
   - Minimum Deployments: **iOS 17**
2. 删除模板生成的 `ContentView.swift` / `Assets.xcassets` 中多余项。
3. 把本目录 `NASDX/` 整个拖入 Xcode 项目（勾选 "Copy if needed" + 你的 App target）。
4. 在 `NASDXApp.swift` 把 `baseURL` 改成**后端可达地址**：
   - 模拟器：`http://localhost:8900`
   - 真机：电脑局域网 IP，如 `http://192.168.1.20:8900`
5. **ATS 例外（仅开发）**：`Info.plist` 加
   ```xml
   <key>NSAppTransportSecurity</key>
   <dict><key>NSAllowsArbitraryLoads</key><true/></dict>
   ```
   上架前务必改回 HTTPS / 正式域名。

## 2. SPM 依赖（Xcode → Package Dependencies 添加）

| 包 | URL | 用途 |
|---|---|---|
| Alamofire | https://github.com/Alamofire/Alamofire | 复杂 HTTP（本项目先用 URLSession，预留） |
| Starscream | https://github.com/daltoniam/Starscream | 实时行情 WebSocket（v2 启用） |
| KeychainAccess | https://github.com/kishikawakats/KeychainAccess | 存 token |
| Kingfisher | https://github.com/onevcat/Kingfisher | 资讯配图 |
| TelemetryDeck | https://github.com/TelemetryDeck/telemetrydeck-swift | 隐私优先埋点 |

> 当前骨架（v1）仅用 `URLSession`（系统库），无需任何外部依赖即可编译运行。

## 3. 目录结构

```
NASDX/
├─ App/
│  └─ NASDXApp.swift              入口、装配 APIClient + SwiftData
├─ Core/
│  ├─ Network/                    Endpoint / APIClient / APIError
│  ├─ Models/                     Quote / KLineBar / WatchlistQuote / *Response
│  ├─ Design/                     DesignTokens（红涨绿跌 + 暗色）
│  ├─ Storage/                    Persistence（SwiftData 缓存）
│  └─ Realtime/                   QuoteSocket（v1 轮询，预留 WS）
└─ Features/
   ├─ Market/                     行情总览 + 自选
   ├─ Quote/                      个股详情（K线 + 实时）
   ├─ Watchlist/                  自选管理
   └─ Settings/                   关于 / 免责 / 后端地址
```

## 4. 后端联调

后端起服务：`python -m uvicorn server.main:app --port 8900`（在 NASDX 项目根）。
iOS 端取数示例（已 reshape 为 camelCase）：
- `GET /api/v1/ios/quote?codes=600519,000001`
- `GET /api/v1/ios/kline/600519?category=4&offset=60`
- `GET /api/v1/ios/market/overview`
- `GET /api/v1/ios/watchlist?codes=600519,000001`
- `GET /api/v1/ios/portfolio`

## 5. 下一步

- [x] 用 `design-md/finance/robinhood/DESIGN-swiftui.md` 替换 `DesignTokens` 配色（记得红涨绿跌反转）
- [ ] `Hero` 接入列表→详情转场
- [ ] `QuoteSocket` 切真 WebSocket（待后端 `/stream`）
- [ ] WidgetKit 自选涨跌幅（App Group 共享 SwiftData）

## 6. CI/CD：Windows 写代码 → 云 Mac 编译 → TestFlight 到 iPhone

**为什么需要这套**：你只有 Windows + iPhone + iPad，没有 Mac。原生 iOS 的最后三步（编译 / 出包 / 上架）必须在 macOS 上完成——这里用 **GitHub Actions 的 macOS runner（云 Mac）** 顶替，你全程在 Windows 写代码、push，云端自动编译并传 TestFlight，你的 iPhone 装包测试即可形成完整闭环。

```
Windows(VS Code 写 .swift) ──push──▶ GitHub ──▶ macOS runner
                                                 ├─ xcodegen 生成 .xcodeproj
                                                 ├─ xcodebuild 编译（build 任务，无需凭据）
                                                 └─ fastlane match + build_app + upload_to_testflight（beta 任务，需 Secrets）
                                                      └─▶ TestFlight ──▶ 你的 iPhone/iPad
```

### 6.1 一次性前置条件（你用浏览器在苹果官网完成，不需要 Mac）

1. **加入 Apple Developer Program**（年费 $99）：https://developer.apple.com/programs/
2. **创建 App Store Connect API Key**（用于 fastlane 免密登录）：
   - App Store Connect → 用户与访问 → 密钥 → 生成，下载 `.p8`（只此一次可下载）
   - 记下：`Issuer ID`、`Key ID`、`.p8` 文件内容
3. **在 App Store Connect 创建 App 记录**（Bundle ID = `com.nasdx.ios` 或你自定，需与 Secrets 一致）
4. **建一个私有 git 仓库**用于存放签名证书（fastlane match 用），记下其 HTTPS URL 与一个有写权限的 PAT（base64：`echo -n 'user:pat' | base64`）

### 6.2 仓库 Secrets（GitHub → 仓库 Settings → Secrets → Actions）

| Secret 名 | 内容 | 说明 |
|---|---|---|
| `BUNDLE_IDENTIFIER` | `com.nasdx.ios` | 与 App Store Connect 里创建的 Bundle ID 一致 |
| `APPLE_ID` | 你的 Apple ID 邮箱 | |
| `TEAM_ID` | 开发者团队 ID | App Store Connect 密钥页可见 |
| `APP_STORE_CONNECT_API_KEY_KEY_ID` | API Key ID | |
| `APP_STORE_CONNECT_API_KEY_ISSUER_ID` | Issuer ID | |
| `APP_STORE_CONNECT_API_KEY_KEY` | `.p8` 文件**全文**（含换行） | 注意保留换行 |
| `FASTLANE_MATCH_GIT_URL` | 私有 match 仓库 HTTPS URL | |
| `FASTLANE_MATCH_GIT_BASIC_AUTHORIZATION` | `user:pat` 的 base64 | |
| `MATCH_PASSWORD` | match 仓库加密密码（自定） | |
| `FASTLANE_PASSWORD` | Apple ID 密码 / App 专用密码 | |
| `MATCH_READONLY` | `true`（首次 bootstrap 时临时改 `false`） | 见 6.3 |

### 6.3 闭环操作步骤

1. **每次 push `ios/**`**：自动跑 `build` 任务——云 Mac 用 `xcodegen` 生成工程并**模拟器无签名编译**，验证代码能编过（不需要任何 Apple 凭据）。这一步你平常就能看到编译结果。
2. **首次发布前，做一次签名引导**（GitHub → Actions → 选 `iOS CI / Release` → Run workflow → `mode=bootstrap-signing`）：`fastlane match_init` 用 API Key 在云端创建证书与描述文件，存入你的私有 match 仓库。
3. **发布到 TestFlight**（Run workflow → `mode=beta`）：`fastlane beta` 签名 + 出包 + 上传。首次若尚未 bootstrap，把 Secret `MATCH_READONLY` 临时设 `false`，跑完改回 `true`。
4. 上传后到 App Store Connect → TestFlight 添加你的 Apple ID 为内部测试员，**iPhone/iPad 打开 TestFlight 即可安装**。

### 6.4 本地（若有 Mac）等价命令

```bash
cd ios
brew install xcodegen fastlane
xcodegen generate --spec project.yml
bundle install
bundle exec fastlane build          # 仅编译校验
bundle exec fastlane match_init     # 首次签名引导
bundle exec fastlane beta           # 上传 TestFlight
```

> 注意：`NASDX.xcodeproj` 是 CI 生成物，已写入 `ios/.gitignore`，**不要手动提交**。AppIcon 当前为占位空集，上架前须在 `NASDX/Assets.xcassets/AppIcon.appiconset` 放入各尺寸图标。

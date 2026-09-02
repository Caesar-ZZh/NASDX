// NASDX iOS · Core 层 SPM 包（可选）
// 用途：在 macOS 上 `swift build` / `swift test` 校验 Core（网络/模型/设计/存储）编译与单测。
// 注意：App 入口与 Features（依赖 SwiftUI 视图）仍建议放在 Xcode 工程 target 里。
// 真机/模拟器开发请以 ios/README.md 的「在 Xcode 打开」为准。
import PackageDescription

let package = Package(
    name: "NASDXCore",
    platforms: [
        .iOS(.v17),
        .macOS(.v14)
    ],
    products: [
        .library(name: "NASDXCore", targets: ["NASDXCore"])
    ],
    targets: [
        .target(
            name: "NASDXCore",
            path: "NASDX/Core"
        )
    ]
)

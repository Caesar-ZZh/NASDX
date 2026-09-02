import SwiftUI

/// 设置：关于 / 免责声明 / 数据源说明。v2 可加后端地址配置、推送开关。
struct SettingsView: View {
    var body: some View {
        NavigationStack {
            List {
                Section("关于") {
                    LabeledContent("应用", value: "NASDX")
                    LabeledContent("版本", value: "1.0.0 (iOS)")
                    LabeledContent("后端", value: "FastAPI /api/v1/ios/*")
                }
                Section("免责声明") {
                    Text("本应用仅作数据展示与自研工具聚合，不构成任何投资建议。市场有风险，投资需谨慎。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("数据来源") {
                    Text("行情：腾讯财经；K线/财务：mootdx；分析：服务端量化模型。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("设置")
        }
    }
}

import SwiftUI
import Charts

/// 个股详情：实时价格 + K线图（Swift Charts）。
struct QuoteDetailView: View {
    let code: String
    let name: String
    @State private var vm = QuoteDetailViewModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignTokens.Spacing.lg) {
                if let q = vm.quote {
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xs) {
                            Text(q.name).font(.title.bold())
                            Text(q.code).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: DesignTokens.Spacing.xs) {
                            Text(String(format: "%.2f", q.price))
                                .font(.largeTitle.monospacedDigit().bold())
                                .trendColor(q.trend)
                                .contentTransition(.numericText()) // 实时跳动
                            Text(String(format: "%+.2f  %+.2f%%", q.changeAmt, q.changePct))
                                .font(.caption.monospacedDigit())
                                .trendColor(q.trend)
                        }
                    }

                    Divider()

                    if !vm.bars.isEmpty {
                        Text("日 K 线（近 \(vm.bars.count) 根）")
                            .font(.caption).foregroundStyle(.secondary)
                        Chart(vm.bars) { bar in
                            LineMark(
                                x: .value("日期", bar.date),
                                y: .value("收盘", bar.close)
                            )
                            .foregroundStyle(Color.brand)
                        }
                        .frame(height: 240)
                        .chartXAxis { AxisMarks(values: .automatic) }
                    } else {
                        ProgressView("加载 K 线…")
                    }
                } else {
                    ProgressView("加载中…")
                }
            }
            .padding(DesignTokens.Spacing.lg)
        }
        .navigationTitle(name)
        .navigationBarTitleDisplayMode(.inline)
        .task { await vm.load(code: code); vm.startRealtime(code: code) }
        .onDisappear { vm.stopRealtime() }
    }
}

import SwiftUI

struct MarketListView: View {
    @State private var vm = MarketListViewModel()

    var body: some View {
        NavigationStack {
            Group {
                if vm.watchlist.isEmpty && vm.isLoading {
                    ProgressView("加载中…")
                } else if vm.watchlist.isEmpty {
                    ContentUnavailableView("暂无行情", systemImage: "chart.line.uptrend.xyaxis",
                                           description: Text(vm.errorMessage ?? "下拉刷新重试"))
                } else {
                    List {
                        Section {
                            ForEach(vm.watchlist) { q in
                                NavigationLink {
                                    QuoteDetailView(code: q.code, name: q.name)
                                } label: {
                                    QuoteRow(quote: q)
                                }
                            }
                        } header: {
                            Text("自选速览")
                        }
                    }
                    .listStyle(.insetGrouped)
                }
            }
            .navigationTitle("行情")
            .refreshable { await vm.refresh() }
            .task { await vm.refresh() }
        }
    }
}

/// 行情行（红涨绿跌）
struct QuoteRow: View {
    let quote: WatchlistQuote

    var body: some View {
        HStack(spacing: DesignTokens.Spacing.md) {
            VStack(alignment: .leading, spacing: DesignTokens.Spacing.xs) {
                Text(quote.name).font(.headline)
                Text(quote.code).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: DesignTokens.Spacing.xs) {
                Text(String(format: "%.2f", quote.price))
                    .font(.body.monospacedDigit())
                Text(String(format: "%+.2f%%", quote.changePct))
                    .font(.caption.monospacedDigit())
                    .trendColor(quote.trend)
            }
        }
        .padding(.vertical, DesignTokens.Spacing.xs)
    }
}

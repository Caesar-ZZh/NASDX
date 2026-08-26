import SwiftUI

/// 根标签导航：行情 / 自选 / 设置。iPad 上可用 NavigationSplitView 转双栏（见 2.3）。
struct RootTabView: View {
    var body: some View {
        TabView {
            MarketListView()
                .tabItem { Label("行情", systemImage: "chart.line.uptrend.xyaxis") }

            WatchlistView()
                .tabItem { Label("自选", systemImage: "star.fill") }

            SettingsView()
                .tabItem { Label("设置", systemImage: "gearshape.fill") }
        }
    }
}

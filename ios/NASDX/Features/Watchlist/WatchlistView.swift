import SwiftUI

/// 自选管理（v1 简化：展示默认自选 + 手动添加代码）。
struct WatchlistView: View {
    @State private var codes: [String] = ["600519", "000001", "300750", "601318", "600036"]
    @State private var newCode = ""

    var body: some View {
        NavigationStack {
            List {
                ForEach(codes, id: \.self) { code in
                    NavigationLink {
                        QuoteDetailView(code: code, name: code)
                    } label: {
                        Label(code, systemImage: "chart.bar.fill")
                    }
                }
                .onDelete { codes.remove(atOffsets: $0) }
            }
            .navigationTitle("自选")
            .safeAreaInset(edge: .bottom) {
                HStack {
                    TextField("输入 6 位代码", text: $newCode)
                        .textFieldStyle(.roundedBorder)
                        .keyboardType(.numberPad)
                    Button("添加") {
                        let c = newCode.trimmingCharacters(in: .whitespaces)
                        if c.count == 6, c.allSatisfy(\.isNumber), !codes.contains(c) {
                            codes.append(c)
                        }
                        newCode = ""
                    }
                    .buttonStyle(.borderedProminent)
                }
                .padding(DesignTokens.Spacing.md)
                .background(Color.appSurface)
            }
        }
    }
}

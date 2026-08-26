import SwiftUI
import SwiftData

@main
struct NASDXApp: App {
    // 后端地址：模拟器用 localhost，真机改电脑局域网 IP。
    // 上架前必须换成 HTTPS 正式域名，并移除 ATS 例外。
    @State private var client = APIClient(
        baseURL: URL(string: "http://localhost:8900")!
    )

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .modelContainer(Persistence.shared.container)
        }
    }
}

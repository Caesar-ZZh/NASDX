import Foundation

/// 实时行情推送封装。
/// v1：后端尚无 WebSocket，先用定时轮询 /quote 模拟「推送」，保证原型可跑。
/// v2：后端提供 /api/v1/ios/stream 后，改用 Starscream 接入真正的 WS（见下方注释）。
@MainActor
final class QuoteSocket {
    static let shared = QuoteSocket()

    private var timer: Timer?
    private(set) var isConnected = false
    /// 收到价格更新时回调，参数为 code -> price
    var onUpdate: (([String: Double]) -> Void)?

    /// v1 轮询实现
    func startPolling(codes: [String], interval: TimeInterval = 3) {
        stop()
        let client = APIClient.shared
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { _ in
            Task { [weak self] in
                do {
                    let res: QuoteResponse = try await client.send(.quote(codes: codes))
                    let map = Dictionary(uniqueKeysWithValues: res.quotes.map { ($0.code, $0.price) })
                    self?.onUpdate?(map)
                } catch {
                    // 静默失败，等下一次轮询
                }
            }
        }
        isConnected = true
    }

    // v2 WebSocket 实现（待后端 /stream 就绪）：
    // import Starscream
    // private var ws: WebSocket?
    // func connect(codes: [String]) {
    //     var req = URLRequest(url: URL(string: "ws://host:8900/api/v1/ios/stream?codes=\(codes.joined(separator: ","))")!)
    //     ws = WebSocket(request: req)
    //     ws?.onText { _, text in /* 解析推送 -> onUpdate */ }
    //     ws?.connect()
    // }

    func stop() {
        timer?.invalidate()
        timer = nil
        isConnected = false
    }
}

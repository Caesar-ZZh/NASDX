import Foundation

/// 个股详情 ViewModel：并行拉取实时行情 + K线，并接入实时轮询。
@MainActor
@Observable
final class QuoteDetailViewModel {
    private(set) var quote: Quote?
    private(set) var bars: [KLineBar] = []
    private(set) var isLoading = false

    private var socketTaskStarted = false

    func load(code: String) async {
        isLoading = true
        defer { isLoading = false }

        async let q: QuoteResponse = APIClient.shared.send(.quote(codes: [code]))
        async let k: KLineResponse = APIClient.shared.send(.kline(code: code, category: 4, offset: 60))

        if let res = try? await q { quote = res.quotes.first }
        if let res = try? await k { bars = res.bars }
    }

    /// 接入实时轮询（v1），价格更新时刷新 quote
    func startRealtime(code: String) {
        guard !socketTaskStarted else { return }
        socketTaskStarted = true
        QuoteSocket.shared.onUpdate = { [weak self] map in
            guard let price = map[code], let self else { return }
            if let q = self.quote {
                let last = q.price
                let changeAmt = price - q.lastClose
                self.quote = Quote(code: q.code, name: q.name, price: price,
                                   lastClose: q.lastClose, open: q.open,
                                   changeAmt: changeAmt,
                                   changePct: q.lastClose > 0 ? changeAmt / q.lastClose * 100 : 0,
                                   high: max(q.high, price), low: min(q.low, price),
                                   peTtm: q.peTtm, pb: q.pb, mcapYi: q.mcapYi)
            }
        }
        QuoteSocket.shared.startPolling(codes: [code])
    }

    func stopRealtime() {
        QuoteSocket.shared.stop()
        socketTaskStarted = false
    }
}

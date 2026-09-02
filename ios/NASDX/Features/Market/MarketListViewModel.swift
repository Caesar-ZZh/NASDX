import Foundation

/// 行情总览 ViewModel：拉取默认自选快照，失败回退本地缓存。
@MainActor
@Observable
final class MarketListViewModel {
    private(set) var watchlist: [WatchlistQuote] = []
    private(set) var isLoading = false
    private(set) var errorMessage: String?

    /// 默认关注的标的（v1 写死；v2 接入用户自选管理）
    private let defaultCodes = ["600519", "000001", "300750", "601318", "600036"]

    func refresh() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let res: QuoteResponse = try await APIClient.shared.send(.watchlist(codes: defaultCodes))
            // /watchlist 返回的是完整 Quote，列表只需要 WatchlistQuote 这几个字段。
            watchlist = res.quotes.map {
                WatchlistQuote(code: $0.code, name: $0.name, price: $0.price,
                               changePct: $0.changePct, changeAmt: $0.changeAmt)
            }
            let quotes = res.quotes.map {
                Quote(code: $0.code, name: $0.name, price: $0.price, lastClose: 0,
                      open: 0, changeAmt: $0.changeAmt, changePct: $0.changePct,
                      high: 0, low: 0, peTtm: 0, pb: 0, mcapYi: 0)
            }
            Persistence.shared.cache(quotes)
        } catch {
            // 回退到本地缓存
            watchlist = Persistence.shared.cached().map {
                WatchlistQuote(code: $0.code, name: $0.name, price: $0.price,
                               changePct: $0.changePct, changeAmt: 0)
            }
            if watchlist.isEmpty {
                errorMessage = (error as? APIError)?.errorDescription ?? "加载失败"
            }
        }
    }
}

import Foundation

/// iOS 端调用的全部后端端点，路径对应 server/stock/ios_api.py 的 /api/v1/ios/* 契约。
enum Endpoint {
    case health
    case quote(codes: [String])
    case kline(code: String, category: Int, offset: Int)
    case marketOverview
    case watchlist(codes: [String])
    case portfolio

    var path: String {
        switch self {
        case .health:
            return "/api/v1/ios/health"
        case let .quote(codes):
            return "/api/v1/ios/quote?codes=\(codes.joined(separator: ","))"
        case let .kline(code, category, offset):
            return "/api/v1/ios/kline/\(code)?category=\(category)&offset=\(offset)"
        case .marketOverview:
            return "/api/v1/ios/market/overview"
        case let .watchlist(codes):
            return "/api/v1/ios/watchlist?codes=\(codes.joined(separator: ","))"
        case .portfolio:
            return "/api/v1/ios/portfolio"
        }
    }
}

import Foundation

// MARK: - 实时行情（对应 /quote、/watchlist）

struct Quote: Identifiable, Decodable, Hashable {
    let code: String
    let name: String
    let price: Double
    let lastClose: Double
    let open: Double
    let changeAmt: Double
    let changePct: Double
    let high: Double
    let low: Double
    let peTtm: Double
    let pb: Double
    let mcapYi: Double

    var id: String { code }
    /// 中国惯例：涨为正（红），跌为负（绿）
    var trend: Trend { changeAmt > 0 ? .up : (changeAmt < 0 ? .down : .flat) }
}

enum Trend {
    case up, down, flat
}

struct QuoteResponse: Decodable {
    let quotes: [Quote]
}

struct WatchlistQuote: Identifiable, Decodable, Hashable {
    let code: String
    let name: String
    let price: Double
    let changePct: Double
    let changeAmt: Double
    var id: String { code }
    var trend: Trend { changeAmt > 0 ? .up : (changeAmt < 0 ? .down : .flat) }
}

// MARK: - K线（对应 /kline/{code}）

struct KLineBar: Identifiable, Decodable, Hashable {
    let date: String
    let open: Double
    let high: Double
    let low: Double
    let close: Double
    let volume: Double
    let amount: Double
    var id: String { date }
}

struct KLineResponse: Decodable {
    let code: String
    let category: Int
    let bars: [KLineBar]
}

// MARK: - 通用 JSON 容器（接住后端任意嵌套结构）

enum JSONValue: Decodable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null; return }
        if let v = try? container.decode(Bool.self) { self = .bool(v); return }
        if let v = try? container.decode(Double.self) { self = .number(v); return }
        if let v = try? container.decode(String.self) { self = .string(v); return }
        if let v = try? container.decode([String: JSONValue].self) { self = .object(v); return }
        if let v = try? container.decode([JSONValue].self) { self = .array(v); return }
        throw DecodingError.typeMismatch(JSONValue.self, .init(codingPath: decoder.codingPath, debugDescription: "unknown"))
    }
}

// MARK: - 市场总览（对应 /market/overview）

struct MarketOverviewResponse: Decodable {
    let overview: JSONValue?
    let turnoverTop: JSONValue?
    let partial: Bool?
}

// MARK: - 持仓（对应 /portfolio）

struct PortfolioResponse: Decodable {
    let portfolio: JSONValue?
}

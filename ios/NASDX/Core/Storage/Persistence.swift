import Foundation
import SwiftData

/// SwiftData 持久化：弱网/行情间隙仍可看上次快照。
@Model
final class CachedQuote {
    @Attribute(.unique) var code: String
    var name: String
    var price: Double
    var changePct: Double
    var updatedAt: Date

    init(code: String, name: String, price: Double, changePct: Double, updatedAt: Date = .now) {
        self.code = code
        self.name = name
        self.price = price
        self.changePct = changePct
        self.updatedAt = updatedAt
    }
}

@MainActor
final class Persistence {
    static let shared = Persistence()

    let container: ModelContainer

    private init() {
        let schema = Schema([CachedQuote.self])
        let config = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)
        container = try! ModelContainer(for: schema, configurations: [config])
    }

    /// 写入一批行情快照（已存在则更新）
    func cache(_ quotes: [Quote]) {
        let context = container.mainContext
        for q in quotes {
            let cached = CachedQuote(code: q.code, name: q.name, price: q.price, changePct: q.changePct)
            context.insert(cached)
        }
        try? context.save()
    }

    /// 读取全部缓存
    func cached() -> [CachedQuote] {
        (try? container.mainContext.fetch(FetchDescriptor<CachedQuote>())) ?? []
    }
}

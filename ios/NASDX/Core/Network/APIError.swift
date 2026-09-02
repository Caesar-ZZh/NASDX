import Foundation

enum APIError: LocalizedError {
    case invalidURL
    case http(status: Int, message: String?)
    case decoding(Error)
    case network(Error)
    case cancelled

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "请求地址无效"
        case let .http(status, message):
            return "服务错误 \(status)" + (message.map { "：\($0)" } ?? "")
        case let .decoding(error):
            return "数据解析失败：\(error.localizedDescription)"
        case let .network(error):
            return "网络异常：\(error.localizedDescription)"
        case .cancelled:
            return "请求已取消"
        }
    }
}

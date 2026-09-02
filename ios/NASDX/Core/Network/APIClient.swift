import Foundation

/// 网络层：基于系统 URLSession + async/await。
/// v1 仅用标准库即可运行；复杂场景（重试/证书固定）可平滑替换为 Alamofire。
@MainActor
final class APIClient {
    static let shared = APIClient()

    private let session: URLSession
    private let baseURL: URL
    /// 登录后由 Keychain 读取并赋值；当前 NASDX 后端无需鉴权，预留。
    var authToken: String?

    init(baseURL: URL = URL(string: "http://localhost:8900")!) {
        self.baseURL = baseURL
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 15
        cfg.waitsForConnectivity = true
        self.session = URLSession(configuration: cfg)
    }

    /// 发送请求并解码为 T。后端返回体含 ok/partial 等字段，Swift 只解码需要的键。
    func send<T: Decodable>(_ endpoint: Endpoint, as type: T.Type = T.self) async throws -> T {
        guard let url = URL(string: endpoint.path, relativeTo: baseURL) else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token = authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch is CancellationError {
            throw APIError.cancelled
        } catch {
            throw APIError.network(error)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.network(NSError(domain: "NASDX", code: -1))
        }
        guard (200..<300).contains(http.statusCode) else {
            let message = try? JSONDecoder().decode([String: String].self, from: data)["detail"]
            throw APIError.http(status: http.statusCode, message: message)
        }

        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }
}

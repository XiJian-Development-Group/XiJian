import Foundation

/// Token 监控：定期从 provider API 拉取用量。
/// MVP：通用 OpenAI-compatible usage endpoint（POST /v1/dashboard/billing/usage）。
/// 订阅型 provider 用额外 endpoint 拿 reset / limit。
public actor TokenMonitor {
    public struct Usage: Sendable, Codable, Hashable {
        public var usedTokens: Int
        public var totalTokens: Int?
        public var resetAt: Date?
        public var tier: String?

        public var percentage: Double? {
            guard let total = totalTokens, total > 0 else { return nil }
            return Double(usedTokens) / Double(total)
        }

        public init(usedTokens: Int = 0, totalTokens: Int? = nil, resetAt: Date? = nil, tier: String? = nil) {
            self.usedTokens = usedTokens
            self.totalTokens = totalTokens
            self.resetAt = resetAt
            self.tier = tier
        }
    }

    public struct Provider: Sendable, Identifiable, Hashable, Codable {
        public let id: String
        public var name: String
        public var endpoint: URL
        public var apiKey: String?
        public var usage: Usage?
        public var lastFetched: Date?
        public var error: String?

        enum CodingKeys: String, CodingKey {
            case id, name, endpoint
            case apiKey
            case usage, lastFetched, error
        }

        public init(id: String, name: String, endpoint: URL, apiKey: String? = nil) {
            self.id = id
            self.name = name
            self.endpoint = endpoint
            self.apiKey = apiKey
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            self.id = try c.decode(String.self, forKey: .id)
            self.name = try c.decode(String.self, forKey: .name)
            self.endpoint = try c.decode(URL.self, forKey: .endpoint)
            self.apiKey = try? c.decode(String.self, forKey: .apiKey)
            self.usage = try? c.decode(Usage.self, forKey: .usage)
            self.lastFetched = try? c.decode(Date.self, forKey: .lastFetched)
            self.error = try? c.decode(String.self, forKey: .error)
        }

        public func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(id, forKey: .id)
            try c.encode(name, forKey: .name)
            try c.encode(endpoint, forKey: .endpoint)
            try c.encodeIfPresent(apiKey, forKey: .apiKey)
            try c.encodeIfPresent(usage, forKey: .usage)
            try c.encodeIfPresent(lastFetched, forKey: .lastFetched)
            try c.encodeIfPresent(error, forKey: .error)
        }
    }

    public static let shared = TokenMonitor()

    public private(set) var providers: [Provider] = []

    /// 持久化文件路径（`~/.hermes/token-providers.json`）
    private let storePath: URL

    public init(storePath: URL? = nil) {
        if let p = storePath {
            self.storePath = p
        } else {
            self.storePath = URL(fileURLWithPath: ("~/.hermes/token-providers.json" as NSString).expandingTildeInPath)
        }
    }

    /// 加载已保存的 providers。
    public func loadProviders() throws -> [Provider] {
        guard FileManager.default.fileExists(atPath: storePath.path) else { return [] }
        let data = try Data(contentsOf: storePath)
        let loaded = try JSONDecoder().decode([Provider].self, from: data)
        self.providers = loaded
        return loaded
    }

    /// 保存 providers。
    public func saveProviders() throws {
        let data = try JSONEncoder().encode(providers)
        try FileManager.default.createDirectory(at: storePath.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: storePath, options: .atomic)
    }

    /// 添加 provider。
    public func addProvider(_ provider: Provider) throws {
        if let idx = providers.firstIndex(where: { $0.id == provider.id }) {
            providers[idx] = provider
        } else {
            providers.append(provider)
        }
        try saveProviders()
    }

    /// 删除 provider。
    public func removeProvider(id: String) throws {
        providers.removeAll { $0.id == id }
        try saveProviders()
    }

    /// 刷新单个 provider 的用量。
    public func refresh(providerID: String) async {
        guard let idx = providers.firstIndex(where: { $0.id == providerID }) else { return }
        var p = providers[idx]
        do {
            p.usage = try await fetchUsage(p)
            p.lastFetched = Date()
            p.error = nil
        } catch {
            p.error = error.localizedDescription
            p.lastFetched = Date()
        }
        providers[idx] = p
        try? saveProviders()
    }

    /// 刷新所有 provider。
    public func refreshAll() async {
        for p in providers {
            await refresh(providerID: p.id)
        }
    }

    /// 调用 provider API 拉取 usage。通用 OpenAI-compatible billing endpoint。
    /// POST `/v1/dashboard/billing/usage` 带 Bearer token，返回 `total_usage`。
    private func fetchUsage(_ provider: Provider) async throws -> Usage {
        var request = URLRequest(url: provider.endpoint)
        request.httpMethod = "GET"
        if let key = provider.apiKey {
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        }
        request.timeoutInterval = 10
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw TokenError.httpError(statusCode: code)
        }
        let decoder = JSONDecoder()
        // 字段名：total_usage（美元美分）/ total_granted（配额）/ hard_limit_usd
        // 简化：转 token 数 = total_usage * 1_000_000（粗估）
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            let cents = (json["total_usage"] as? Double) ?? 0
            let granted = json["total_granted"] as? Double
            return Usage(
                usedTokens: Int(cents * 1_000_000),
                totalTokens: granted.map { Int($0 * 1_000_000) }
            )
        }
        throw TokenError.invalidResponse
    }
}

public enum TokenError: Error, LocalizedError, Sendable {
    case httpError(statusCode: Int)
    case invalidResponse

    public var errorDescription: String? {
        switch self {
        case .httpError(let code): return "HTTP \(code)"
        case .invalidResponse: return "响应格式无效"
        }
    }
}

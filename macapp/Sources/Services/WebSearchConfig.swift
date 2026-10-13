import Foundation

/// websearch 切换：让用户选择默认 web search provider（默认 / tavily / brave / duckduckgo）
public actor WebSearchConfig {
    public struct Provider: Sendable, Identifiable, Hashable, Codable {
        public let id: String
        public var name: String
        public var apiKey: String?

        public init(id: String, name: String, apiKey: String? = nil) {
            self.id = id
            self.name = name
            self.apiKey = apiKey
        }
    }

    public static let shared = WebSearchConfig()

    public let available: [Provider] = [
        Provider(id: "default", name: "默认（Hermes 内置）"),
        Provider(id: "tavily", name: "Tavily", apiKey: nil),
        Provider(id: "brave", name: "Brave Search", apiKey: nil),
        Provider(id: "duckduckgo", name: "DuckDuckGo", apiKey: nil),
    ]

    private let storePath: URL

    public init(storePath: URL? = nil) {
        if let p = storePath {
            self.storePath = p
        } else {
            self.storePath = URL(fileURLWithPath: ("~/.hermes/websearch.json" as NSString).expandingTildeInPath)
        }
    }

    public private(set) var currentID: String = "default"

    public func load() throws -> String {
        guard FileManager.default.fileExists(atPath: storePath.path) else {
            return currentID
        }
        let data = try Data(contentsOf: storePath)
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let id = json["provider"] as? String {
            self.currentID = id
        }
        return currentID
    }

    public func setCurrent(_ id: String) throws {
        guard available.contains(where: { $0.id == id }) else { return }
        self.currentID = id
        let dict: [String: Any] = ["provider": id]
        let data = try JSONSerialization.data(withJSONObject: dict)
        try FileManager.default.createDirectory(at: storePath.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: storePath, options: .atomic)
    }
}

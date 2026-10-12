import Foundation

/// MCP server 管理器：读写 `~/.hermes/mcp.json`。
/// JSON 格式（hermes 标准）：
/// ```json
/// {
///   "mcpServers": {
///     "name": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-X"], "env": {"KEY": "..."}, "enabled": true}
///   }
/// }
/// ```
public actor MCPManager {
    public struct MCPServer: Sendable, Identifiable, Hashable, Codable {
        public let id: String  // server name
        public var command: String
        public var args: [String]
        public var env: [String: String]
        public var enabled: Bool

        public init(id: String, command: String, args: [String] = [], env: [String: String] = [:], enabled: Bool = true) {
            self.id = id
            self.command = command
            self.args = args
            self.env = env
            self.enabled = enabled
        }

        enum CodingKeys: String, CodingKey {
            case id
            case command
            case args
            case env
            case enabled
        }

        public init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.id = try container.decode(String.self, forKey: .id)
            self.command = try container.decode(String.self, forKey: .command)
            self.args = (try? container.decode([String].self, forKey: .args)) ?? []
            self.env = (try? container.decode([String: String].self, forKey: .env)) ?? [:]
            self.enabled = (try? container.decode(Bool.self, forKey: .enabled)) ?? true
        }

        public func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(id, forKey: .id)
            try c.encode(command, forKey: .command)
            try c.encode(args, forKey: .args)
            if !env.isEmpty { try c.encode(env, forKey: .env) }
            try c.encode(enabled, forKey: .enabled)
        }
    }

    public struct Config: Sendable, Codable {
        public var servers: [String: MCPServer]

        public init(servers: [String: MCPServer] = [:]) {
            self.servers = servers
        }

        enum CodingKeys: String, CodingKey {
            case mcpServers = "mcpServers"
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            // 兼容两种格式：直接顶层或 mcpServers wrapper
            self.servers = (try? c.decode([String: MCPServer].self, forKey: .mcpServers)) ?? [:]
        }

        public func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(servers, forKey: .mcpServers)
        }
    }

    public static let shared = MCPManager()

    private let configPath: URL

    public init(configPath: URL? = nil) {
        if let p = configPath {
            self.configPath = p
        } else {
            self.configPath = URL(fileURLWithPath: ("~/.hermes/mcp.json" as NSString).expandingTildeInPath)
        }
    }

    /// 加载 MCP 配置。文件不存在返回空配置。
    public func load() throws -> Config {
        guard FileManager.default.fileExists(atPath: configPath.path) else {
            return Config()
        }
        let data = try Data(contentsOf: configPath)
        return try JSONDecoder().decode(Config.self, from: data)
    }

    /// 原子保存。
    public func save(_ config: Config) throws {
        let data = try JSONEncoder().encode(config)
        try FileManager.default.createDirectory(
            at: configPath.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let tmp = configPath.deletingLastPathComponent()
            .appendingPathComponent("mcp.json.tmp")
        try data.write(to: tmp, options: .atomic)
        if FileManager.default.fileExists(atPath: configPath.path) {
            try FileManager.default.removeItem(at: configPath)
        }
        try FileManager.default.moveItem(at: tmp, to: configPath)
    }

    /// 列出所有 server（按名称排序）。
    public func listServers() throws -> [MCPServer] {
        let config = try load()
        return config.servers.values.sorted { $0.id < $1.id }
    }

    /// 添加 / 更新 server（id 已存在则覆盖）。
    public func addServer(_ server: MCPServer) throws {
        var config = try load()
        config.servers[server.id] = server
        try save(config)
    }

    /// 删除 server。
    public func removeServer(name: String) throws {
        var config = try load()
        config.servers.removeValue(forKey: name)
        try save(config)
    }

    /// 切换启用状态。
    public func toggleServer(name: String, enabled: Bool) throws {
        var config = try load()
        guard var server = config.servers[name] else {
            throw MCPError.notFound(name: name)
        }
        server.enabled = enabled
        config.servers[name] = server
        try save(config)
    }
}

public enum MCPError: Error, LocalizedError, Sendable {
    case notFound(name: String)

    public var errorDescription: String? {
        switch self {
        case .notFound(let name): return "MCP server 不存在：\(name)"
        }
    }
}

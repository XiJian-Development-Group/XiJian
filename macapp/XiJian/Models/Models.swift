import Foundation

enum SidebarItem: String, CaseIterable, Identifiable {
    case chat = "chat"
    case characters = "characters"
    case settings = "settings"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .chat: return "对话"
        case .characters: return "角色"
        case .settings: return "设置"
        }
    }

    var icon: String {
        switch self {
        case .chat: return "message"
        case .characters: return "person.2"
        case .settings: return "gearshape"
        }
    }
}

struct Message: Identifiable, Equatable {
    let id: String
    var role: MessageRole
    var content: String
    let timestamp: Date
    var isStreaming: Bool

    init(id: String = UUID().uuidString, role: MessageRole, content: String, timestamp: Date = Date(), isStreaming: Bool = false) {
        self.id = id
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.isStreaming = isStreaming
    }

    var roleLabel: String {
        switch role {
        case .user: return "你"
        case .assistant: return "助手"
        case .system: return "系统"
        }
    }

    static func == (lhs: Message, rhs: Message) -> Bool {
        lhs.id == rhs.id && lhs.content == rhs.content && lhs.isStreaming == rhs.isStreaming
    }
}

enum MessageRole: String, Codable {
    case user
    case assistant
    case system
}

struct Conversation: Identifiable {
    let id: String
    var title: String
    var messages: [Message]
    var model: String
    let createdAt: Date
    var updatedAt: Date

    init(id: String = UUID().uuidString, title: String = "新对话", messages: [Message] = [], model: String = "default", createdAt: Date = Date()) {
        self.id = id
        self.title = title
        self.messages = messages
        self.model = model
        self.createdAt = createdAt
        self.updatedAt = createdAt
    }
}

struct ServerCharacter: Identifiable, Codable {
    let id: String
    var name: String
    var persona: String

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case persona
    }
}

struct MemoryConfig: Codable {
    var maxShortTerm: Int = 50
    var maxLongTerm: Int = 200
    var memoryDecay: Double = 0.1

    enum CodingKeys: String, CodingKey {
        case maxShortTerm = "max_short_term"
        case maxLongTerm = "max_long_term"
        case memoryDecay = "memory_decay"
    }
}

struct ServerConfig: Codable {
    var customServerURL: String = ""
    var useCustomServer: Bool = false
    var customPythonPath: String = ""
    var defaultModel: String = "default"

    static let defaultConfig = ServerConfig()

    static func load() -> ServerConfig {
        guard let url = configFileURL,
              let data = try? Data(contentsOf: url),
              let config = try? JSONDecoder().decode(ServerConfig.self, from: data) else {
            return .defaultConfig
        }
        return config
    }

    func save() {
        guard let url = Self.configFileURL,
              let data = try? JSONEncoder().encode(self) else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }

    private static var configFileURL: URL? {
        let paths = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        return paths?.appendingPathComponent("XiJian/UI/config.json")
    }
}

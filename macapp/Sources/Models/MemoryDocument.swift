import Foundation

/// 记忆文档：MEMORY.md / USER.md 两个固定文件的包装。
/// 整个文件内容是单一字符串，没有章节解析（保持简洁）。
public struct MemoryDocument: Sendable, Equatable {
    public enum Kind: String, CaseIterable, Sendable, Identifiable {
        case memory = "MEMORY.md"
        case user = "USER.md"

        public var id: String { rawValue }

        public var displayName: String {
            switch self {
            case .memory: return loc("长期记忆")
            case .user: return loc("用户资料")
            }
        }
    }

    public let kind: Kind
    public var body: String

    public init(kind: Kind, body: String = "") {
        self.kind = kind
        self.body = body
    }

    /// 加载记忆文件。不存在返回空文档。
    public static func load(kind: Kind, from profilePath: URL) throws -> MemoryDocument {
        let url = profilePath.appendingPathComponent(kind.rawValue)
        guard FileManager.default.fileExists(atPath: url.path) else {
            return MemoryDocument(kind: kind)
        }
        let content = try String(contentsOf: url, encoding: .utf8)
        return MemoryDocument(kind: kind, body: content)
    }

    /// 原子保存。
    public func save(to profilePath: URL) throws {
        let url = profilePath.appendingPathComponent(kind.rawValue)
        let tmp = profilePath.appendingPathComponent("\(kind.rawValue).tmp")
        try body.write(to: tmp, atomically: false, encoding: .utf8)
        if FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.removeItem(at: url)
        }
        try FileManager.default.moveItem(at: tmp, to: url)
    }
}

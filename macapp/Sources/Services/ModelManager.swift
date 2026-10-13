import Foundation

/// 模型管理：读 `~/.hermes/config.yaml` 列出 models + 切换活跃。
public actor ModelManager {
    public struct Model: Sendable, Identifiable, Hashable {
        public let id: String  // 唯一 ID（provider:name）
        public var provider: String
        public var name: String
        public var isActive: Bool
        public var notes: String?

        public init(id: String, provider: String, name: String, isActive: Bool = false, notes: String? = nil) {
            self.id = id
            self.provider = provider
            self.name = name
            self.isActive = isActive
            self.notes = notes
        }
    }

    public static let shared = ModelManager()

    public private(set) var models: [Model] = []
    public private(set) var activeModel: Model?

    private let configPath: URL

    public init(configPath: URL? = nil) {
        if let p = configPath {
            self.configPath = p
        } else {
            self.configPath = URL(fileURLWithPath: ("~/.hermes/config.yaml" as NSString).expandingTildeInPath)
        }
    }

    /// 加载 config.yaml 中所有 model 块（YAML 解析见 YAMLParser.swift）。
    /// 返回所有模型，第一个标 `active: true` 的为活跃。
    public func load() throws -> [Model] {
        guard FileManager.default.fileExists(atPath: configPath.path) else {
            self.models = []
            self.activeModel = nil
            return []
        }
        let content = try String(contentsOf: configPath, encoding: .utf8)
        let parsed = SimpleYAMLParser.parse(content)
        // 简化解析：找顶层 key=`models`，再找 `active_model`
        var loaded: [Model] = []
        if let modelsBlock = parsed["models"]?.dictionary {
            for (name, value) in modelsBlock {
                if let dict = value.dictionary {
                    let provider = dict["provider"]?.string ?? "unknown"
                    let active = dict["active"]?.bool ?? false
                    let notes = dict["notes"]?.string
                    loaded.append(Model(
                        id: "\(provider):\(name)",
                        provider: provider,
                        name: name,
                        isActive: active,
                        notes: notes
                    ))
                }
            }
        }
        self.models = loaded.sorted { $0.name < $1.name }
        self.activeModel = loaded.first { $0.isActive }
        return self.models
    }

    /// 设置某个模型为活跃（同时取消其他）。
    public func setActive(id: String) throws {
        // 改内存
        for i in models.indices {
            models[i].isActive = models[i].id == id
        }
        self.activeModel = models.first { $0.isActive }
        // 写回 yaml
        try writeBack()
    }

    /// 添加新模型。
    public func add(_ model: Model) throws {
        models.append(model)
        models.sort { $0.name < $1.name }
        try writeBack()
    }

    /// 删除模型。
    public func remove(id: String) throws {
        models.removeAll { $0.id == id }
        if activeModel?.id == id { activeModel = nil }
        try writeBack()
    }

    /// 把当前状态写回 config.yaml。简化：只重写 `models:` 块。
    private func writeBack() throws {
        var lines: [String] = []
        lines.append("models:")
        for m in models {
            lines.append("  \(m.name):")
            lines.append("    provider: \(m.provider)")
            if m.isActive { lines.append("    active: true") }
            if let notes = m.notes, !notes.isEmpty {
                lines.append("    notes: \"\(notes.replacingOccurrences(of: "\"", with: "\\\""))\"")
            }
        }
        let content = lines.joined(separator: "\n") + "\n"
        try content.write(to: configPath, atomically: true, encoding: .utf8)
    }
}

/// 极简 YAML 解析器，只支持：标量、字典、列表、嵌套字典。
/// 假设 YAML 用 2 空格缩进。值类型：string / int / double / bool / null / dict / list。
enum SimpleYAMLParser {
    enum Value: Sendable {
        case string(String)
        case int(Int)
        case double(Double)
        case bool(Bool)
        case null
        case dictionary([String: Value])
        case list([Value])

        var string: String? {
            if case .string(let s) = self { return s }
            return nil
        }
        var int: Int? {
            if case .int(let i) = self { return i }
            return nil
        }
        var double: Double? {
            if case .double(let d) = self { return d }
            return nil
        }
        var bool: Bool? {
            if case .bool(let b) = self { return b }
            return nil
        }
        var dictionary: [String: Value]? {
            if case .dictionary(let d) = self { return d }
            return nil
        }
        var list: [Value]? {
            if case .list(let l) = self { return l }
            return nil
        }
    }

    static func parse(_ text: String) -> [String: Value] {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        var root: [String: Value] = [:]
        parseBlock(lines: lines, indent: 0, index: 0, into: &root)
        return root
    }

    /// 解析 indent 缩进对应的块，写入 result 字典。
    /// 返回下一行起始 index。
    private static func parseBlock(lines: [String], indent: Int, index: Int, into result: inout [String: Value]) -> Int {
        var i = index
        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty || trimmed.hasPrefix("#") { i += 1; continue }
            let lineIndent = leadingSpaces(line)
            if lineIndent < indent { break }
            if lineIndent > indent { i += 1; continue }
            // 形如 `key:` 或 `key: value` 或 `key:` 后接子块
            if let colon = trimmed.firstIndex(of: ":") {
                let key = String(trimmed[..<colon])
                let rest = trimmed[trimmed.index(after: colon)...].trimmingCharacters(in: .whitespaces)
                if rest.isEmpty {
                    // 子块
                    var sub: [String: Value] = [:]
                    let next = parseBlock(lines: lines, indent: indent + 2, index: i + 1, into: &sub)
                    result[key] = .dictionary(sub)
                    i = next
                } else {
                    result[key] = parseScalar(rest)
                    i += 1
                }
            } else if trimmed.hasPrefix("- ") {
                // 列表（在当前 indent 的子节点）
                var list: [Value] = []
                var j = i
                while j < lines.count {
                    let l = lines[j]
                    let lt = l.trimmingCharacters(in: .whitespaces)
                    let li = leadingSpaces(l)
                    if li != indent || !lt.hasPrefix("- ") { break }
                    let item = String(lt.dropFirst(2)).trimmingCharacters(in: .whitespaces)
                    if item.isEmpty {
                        var sub: [String: Value] = [:]
                        j = parseBlock(lines: lines, indent: indent + 4, index: j + 1, into: &sub)
                        list.append(.dictionary(sub))
                    } else {
                        list.append(parseScalar(item))
                        j += 1
                    }
                }
                // 列表作为最后 key（无 key 前缀）— 实际不应在顶层出现；这里挂到上一个 key
                // 不在此实现，简化丢弃
                i = j
            } else {
                i += 1
            }
        }
        return i
    }

    private static func leadingSpaces(_ s: String) -> Int {
        var n = 0
        for c in s {
            if c == " " { n += 1 } else { break }
        }
        return n
    }

    private static func parseScalar(_ s: String) -> Value {
        if s == "null" || s == "~" { return .null }
        if s == "true" { return .bool(true) }
        if s == "false" { return .bool(false) }
        if let i = Int(s) { return .int(i) }
        if let d = Double(s) { return .double(d) }
        // 去引号
        var v = s
        if (v.hasPrefix("\"") && v.hasSuffix("\"")) || (v.hasPrefix("'") && v.hasSuffix("'")) {
            v = String(v.dropFirst().dropLast())
        }
        return .string(v)
    }
}

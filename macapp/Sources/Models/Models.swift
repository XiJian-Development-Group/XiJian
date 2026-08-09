import Foundation

// MARK: - 通用 JSON 值（用于自由结构响应：设置、状态等）

/// 动态 CodingKey（用于解码未知字段）
struct DynamicCodingKeys: CodingKey {
    var stringValue: String
    var intValue: Int?

    init?(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    init?(intValue: Int) {
        self.stringValue = String(intValue)
        self.intValue = intValue
    }
}

/// 任意 JSON 值，用于解码结构不固定的响应字段
enum JSONValue: Codable, Hashable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null
    case array([JSONValue])
    case object([String: JSONValue])

    var stringValue: String? {
        if case .string(let s) = self { return s }
        if case .number(let n) = self { return n.truncatingRemainder(dividingBy: 1) == 0 ? String(Int(n)) : String(n) }
        if case .bool(let b) = self { return b ? "true" : "false" }
        return nil
    }

    var doubleValue: Double? {
        if case .number(let n) = self { return n }
        return nil
    }

    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    /// 以可读文本呈现（用于状态展示）
    var displayText: String {
        switch self {
        case .string(let s): return s
        case .number(let n): return n.truncatingRemainder(dividingBy: 1) == 0 ? String(Int(n)) : String(n)
        case .bool(let b): return b ? loc("是") : loc("否")
        case .null: return loc("无")
        case .array(let arr): return arr.map(\.displayText).joined(separator: loc("、"))
        case .object(let dict): return dict.map { "\($0.key): \($0.value.displayText)" }.sorted().joined(separator: "\n")
        }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let s = try? container.decode(String.self) { self = .string(s) }
        else if let n = try? container.decode(Double.self) { self = .number(n) }
        else if let b = try? container.decode(Bool.self) { self = .bool(b) }
        else if container.decodeNil() { self = .null }
        else if let arr = try? container.decode([JSONValue].self) { self = .array(arr) }
        else if let obj = try? container.decode([String: JSONValue].self) { self = .object(obj) }
        else { throw DecodingError.dataCorruptedError(in: container, debugDescription: "无法解析 JSON 值") }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let s): try container.encode(s)
        case .number(let n): try container.encode(n)
        case .bool(let b): try container.encode(b)
        case .null: try container.encodeNil()
        case .array(let arr): try container.encode(arr)
        case .object(let obj): try container.encode(obj)
        }
    }
}

// MARK: - 模型

/// OAI 模型信息（GET /v1/models）
struct ModelInfo: Codable, Identifiable, Hashable {
    let id: String
    let object: String?
    let created: Int?
    let owned_by: String?
    let xijian: XijianExtra?

    struct XijianExtra: Codable, Hashable {
        let backend: String?
        let family: String?
        let size_b: Double?
        let quant: String?
        let context_length: Int?
        let min_ram_gb: Double?
        let loaded: Bool?
    }

    var displayName: String {
        let quant = xijian?.quant.map { " (\($0))" } ?? ""
        return "\(id)\(quant)"
    }
}

// MARK: - 消息与会话

/// 消息角色
enum MessageRole: String, Codable, Hashable {
    case system
    case user
    case assistant
    case tool

    var displayName: String {
        switch self {
        case .system: return loc("系统")
        case .user: return loc("用户")
        case .assistant: return loc("助手")
        case .tool: return loc("工具")
        }
    }
}

/// 会话消息（GET /v1/xijian/sessions/{id}/messages 与聊天请求共用）
struct ChatMessage: Codable, Identifiable, Hashable {
    let id: String?
    let object: String?
    let session_id: String?
    let role: String
    let content: String
    let created_at: Double?
    let name: String?

    var roleEnum: MessageRole { MessageRole(rawValue: role) ?? .user }
    var isUser: Bool { role == "user" }
    var isAssistant: Bool { role == "assistant" }

    /// 构造本地待发送消息
    init(role: String, content: String, sessionID: String? = nil) {
        self.id = nil
        self.object = nil
        self.session_id = sessionID
        self.role = role
        self.content = content
        self.created_at = Date().timeIntervalSince1970
        self.name = nil
    }
}

/// 会话信息（POST /v1/xijian/sessions）
struct SessionInfo: Codable, Identifiable, Hashable {
    let id: String
    let object: String?
    let title: String?
    let created_at: Double?
    let updated_at: Double?
}

// MARK: - 角色

/// 角色信息（GET /v1/xijian/characters）
struct CharacterInfo: Codable, Identifiable, Hashable {
    let id: String
    let object: String?
    let name: String?
    let display_name: String?
    let persona_doc: String?
    let voice_profile: String?
    let default_emotion: String?
    let tags: [String]?
    let loaded: Bool?
    let created_at: Double?
    let updated_at: Double?
    /// 来源标记：来自资源包（Core 原样返回 _pack_source / _pack_id）
    let packSource: Bool?
    let packID: String?

    var displayName: String { display_name ?? name ?? id }
    var isLoaded: Bool { loaded ?? false }
    var tagList: [String] { tags ?? [] }
    var isFromPack: Bool { packSource ?? false }

    private enum CodingKeys: String, CodingKey {
        case id, object, name, display_name, persona_doc, voice_profile
        case default_emotion, tags, loaded, created_at, updated_at
        case packSource = "_pack_source"
        case packID = "_pack_id"
    }
}

/// 角色状态（GET /v1/xijian/characters/{id}/state）— 动态字段
struct CharacterStateInfo: Codable, Hashable {
    let values: [String: JSONValue]

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        values = try container.decode([String: JSONValue].self)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(values)
    }

    subscript(key: String) -> JSONValue? { values[key] }

    /// 可读字段列表（按 key 排序，供展示）
    var sortedEntries: [(key: String, value: JSONValue)] {
        values.sorted { $0.key < $1.key }.map { (key: $0.key, value: $0.value) }
    }

    /// 常用字段快捷读取
    var intimacy: Double? { values["intimacy"]?.doubleValue }
    var mood: Double? { values["mood"]?.doubleValue }

    /// A3.2 数值状态摘要（顶层含 values/max 块时可用；角色从未被状态系统
    /// 触碰时返回 nil，此时响应仅含 v1 文本字段）
    var summary: CharacterStateSummary? {
        CharacterStateSummary(from: self)
    }
}

/// 角色状态维度 — A3.2 四个标准数值字段
enum CharacterStatusDimension: String, CaseIterable, Identifiable {
    case hunger
    case thirst
    case health
    case mood

    var id: String { rawValue }

    /// 本地化显示名
    var displayName: String {
        switch self {
        case .hunger: return loc("饱食")
        case .thirst: return loc("饮水")
        case .health: return loc("健康")
        case .mood: return loc("心情")
        }
    }

    /// SF Symbol（不依赖 SwiftUI，仅返回名称）
    var iconName: String {
        switch self {
        case .hunger: return "fork.knife"
        case .thirst: return "drop.fill"
        case .health: return "heart.fill"
        case .mood: return "face.smiling"
        }
    }

    /// 从 Core 字段名解析维度（未知字段返回 nil，供日志等自由字段展示兜底）
    init?(fieldName: String) {
        self.init(rawValue: fieldName)
    }
}

/// 角色状态摘要 — GET /v1/xijian/characters/{id}/state 的 A3.2 数值块。
/// Core 契约：``values`` / ``max`` 为 {hunger, thirst, health, mood, stamina} 数值字典。
struct CharacterStateSummary: Equatable {
    let values: [String: Double]
    let max: [String: Double]
    /// 状态机标签（healthy / hungry / thirsty / sick / recovering / critical）
    let status: String?
    let statusChangedAt: Double?
    let lastUpdated: Double?
    /// Critical 状态下角色不可对话
    let canDialogue: Bool?
    let activeBehavior: [JSONValue]?
    let modifiers: [String: JSONValue]?

    init(
        values: [String: Double],
        max: [String: Double] = [:],
        status: String? = nil,
        statusChangedAt: Double? = nil,
        lastUpdated: Double? = nil,
        canDialogue: Bool? = nil,
        activeBehavior: [JSONValue]? = nil,
        modifiers: [String: JSONValue]? = nil
    ) {
        self.values = values
        self.max = max
        self.status = status
        self.statusChangedAt = statusChangedAt
        self.lastUpdated = lastUpdated
        self.canDialogue = canDialogue
        self.activeBehavior = activeBehavior
        self.modifiers = modifiers
    }

    /// 从状态响应顶层字典构建；缺少 ``values`` 块时返回 nil
    init?(from state: CharacterStateInfo) {
        guard case .object(let valuesObj) = state.values["values"] else { return nil }
        var values: [String: Double] = [:]
        for (key, value) in valuesObj {
            if let number = value.doubleValue { values[key] = number }
        }
        var maxes: [String: Double] = [:]
        if case .object(let maxObj) = state.values["max"] {
            for (key, value) in maxObj {
                if let number = value.doubleValue { maxes[key] = number }
            }
        }
        self.init(
            values: values,
            max: maxes,
            status: state.values["status"]?.stringValue,
            statusChangedAt: state.values["status_changed_at"]?.doubleValue,
            lastUpdated: state.values["last_updated"]?.doubleValue,
            canDialogue: state.values["can_dialogue"]?.boolValue,
            activeBehavior: state.values["active_behavior"]?.arrayValue,
            modifiers: state.values["modifiers"]?.objectValue
        )
    }

    /// 维度当前值（缺失时 0）
    func value(for dimension: CharacterStatusDimension) -> Double {
        values[dimension.rawValue] ?? 0
    }

    /// 维度上限（缺失时默认 100）
    func max(for dimension: CharacterStatusDimension) -> Double {
        max[dimension.rawValue] ?? 100.0
    }

    /// 是否处于 Critical（健康 ≤ 0，不可对话）
    var isCritical: Bool { status == "critical" }

    /// 状态本地化显示名
    var statusDisplayName: String {
        switch status {
        case "healthy": return loc("健康")
        case "hungry": return loc("饥饿")
        case "thirsty": return loc("口渴")
        case "sick": return loc("生病")
        case "recovering": return loc("恢复中")
        case "critical": return loc("危殆")
        default: return status ?? loc("未知")
        }
    }
}

/// 角色状态变更日志条目 — GET /v1/xijian/characters/{id}/state/log 返回
/// ``{"entries": [...]}``，最新在前
struct CharacterStateLogEntry: Codable, Hashable {
    let id: String?
    let character_id: String?
    /// 变更维度（hunger / thirst / health / mood / stamina）
    let field: String?
    let old_value: Double?
    let new_value: Double?
    /// 来源：tick / dialogue / world_event / manual / admin_recover
    let reason: String?
    let ref_id: String?
    let created_at: Double?

    /// 维度显示名（未知字段回退原文）
    var fieldDisplayName: String {
        CharacterStatusDimension(fieldName: field ?? "")?.displayName ?? field ?? loc("未知")
    }

    /// 来源本地化显示名
    var reasonDisplayName: String {
        switch reason {
        case "tick": return loc("自然衰减")
        case "dialogue": return loc("对话")
        case "world_event": return loc("世界事件")
        case "manual": return loc("手动调整")
        case "admin_recover": return loc("强制恢复")
        default: return reason ?? loc("未知")
        }
    }

    /// 数值变化文本（"+12" / "-3"；缺失时为空）
    var deltaText: String {
        guard let oldValue = old_value, let newValue = new_value else { return "" }
        let delta = newValue - oldValue
        return delta > 0 ? String(format: "+%.0f", delta) : String(format: "%.0f", delta)
    }

    /// 变化方向（用于着色：正 / 负 / 无变化）
    var deltaSign: Int {
        guard let oldValue = old_value, let newValue = new_value else { return 0 }
        return newValue > oldValue ? 1 : (newValue < oldValue ? -1 : 0)
    }
}

// MARK: - JSONValue 便捷访问

extension JSONValue {
    /// 数组值（供 active_behavior 等字段使用）
    var arrayValue: [JSONValue]? {
        if case .array(let arr) = self { return arr }
        return nil
    }

    /// 对象值（供 modifiers 等字段使用）
    var objectValue: [String: JSONValue]? {
        if case .object(let obj) = self { return obj }
        return nil
    }
}

// MARK: - 世界

/// 世界记录（GET /v1/xijian/worlds）
struct WorldInfo: Codable, Identifiable, Hashable {
    let id: String?
    let name: String?
    let world_doc_path: String?
    let config_path: String?
    let state_doc_path: String?
    let is_active: Bool?
    let last_active_at: Double?
    let created_at: Double?
    let updated_at: Double?
    /// 来源标记：来自资源包（Core 原样返回 _pack_source / _pack_id）
    let packSource: Bool?
    let packID: String?

    var worldID: String { id ?? "" }
    var isActive: Bool { is_active ?? false }
    var isFromPack: Bool { packSource ?? false }

    private enum CodingKeys: String, CodingKey {
        case id, name, world_doc_path, config_path, state_doc_path
        case is_active, last_active_at, created_at, updated_at
        case packSource = "_pack_source"
        case packID = "_pack_id"
    }
}

/// 世界状态（GET /v1/xijian/worlds/{id}/state）
struct WorldStateInfo: Codable, Hashable {
    let world_id: String?
    let name: String?
    let is_active: Bool?
    let world_doc_path: String?
    let config_path: String?
    let state_doc_path: String?
    let environment: WorldEnvironment?
    let compute_config: WorldComputeConfig?
    let npc_count: Int?
    let updated_at: Double?
    /// 自由状态字段（economy / health / diet / stamina / mentality 等）
    let extra: [String: JSONValue]

    struct WorldEnvironment: Codable, Hashable {
        let weather: String?
        let time_of_day: String?
        let light_level: Double?
        let ambient_audio: String?
        let env_meta: [String: JSONValue]?
    }

    struct WorldComputeConfig: Codable, Hashable {
        let active_tier: String?
        let max_npcs: Int?
        let max_active_npcs: Int?
        let max_low_active_npcs: Int?
        let total_token_budget: Int?
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        world_id = try container.decodeIfPresent(String.self, forKey: .world_id)
        name = try container.decodeIfPresent(String.self, forKey: .name)
        is_active = try container.decodeIfPresent(Bool.self, forKey: .is_active)
        world_doc_path = try container.decodeIfPresent(String.self, forKey: .world_doc_path)
        config_path = try container.decodeIfPresent(String.self, forKey: .config_path)
        state_doc_path = try container.decodeIfPresent(String.self, forKey: .state_doc_path)
        environment = try container.decodeIfPresent(WorldEnvironment.self, forKey: .environment)
        compute_config = try container.decodeIfPresent(WorldComputeConfig.self, forKey: .compute_config)
        npc_count = try container.decodeIfPresent(Int.self, forKey: .npc_count)
        updated_at = try container.decodeIfPresent(Double.self, forKey: .updated_at)
        // 捕获所有未映射字段（economy / health / mentality 等）
        let dynamic = try decoder.container(keyedBy: DynamicCodingKeys.self)
        let known: Set<String> = ["world_id", "name", "is_active", "world_doc_path", "config_path",
                                  "state_doc_path", "environment", "compute_config", "npc_count", "updated_at"]
        var extras: [String: JSONValue] = [:]
        for key in dynamic.allKeys where !known.contains(key.stringValue) {
            extras[key.stringValue] = try dynamic.decode(JSONValue.self, forKey: key)
        }
        extra = extras
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(world_id, forKey: .world_id)
        try container.encodeIfPresent(name, forKey: .name)
        try container.encodeIfPresent(is_active, forKey: .is_active)
        try container.encodeIfPresent(world_doc_path, forKey: .world_doc_path)
        try container.encodeIfPresent(config_path, forKey: .config_path)
        try container.encodeIfPresent(state_doc_path, forKey: .state_doc_path)
        try container.encodeIfPresent(environment, forKey: .environment)
        try container.encodeIfPresent(compute_config, forKey: .compute_config)
        try container.encodeIfPresent(npc_count, forKey: .npc_count)
        try container.encodeIfPresent(updated_at, forKey: .updated_at)
    }

    private enum CodingKeys: String, CodingKey {
        case world_id, name, is_active, world_doc_path, config_path, state_doc_path
        case environment, compute_config, npc_count, updated_at
    }
}

// MARK: - 记忆

/// 记忆条目（GET /v1/xijian/memory/entries）
struct MemoryEntry: Codable, Identifiable, Hashable {
    let id: String
    let object: String?
    let character_id: String?
    let type: String?
    let content: String
    let importance: Double?
    let source: String?
    let source_ref_id: String?
    let tags: [String]?
    let access_count: Int?
    let last_access_at: Double?
    let decay_score: Double?
    let created_at: Double?
    let updated_at: Double?
    let deleted_at: Double?
    let attributes: [String: JSONValue]?

    var tagList: [String] { tags ?? [] }
    var typeDisplay: String { type == "long" ? loc("长期") : loc("短期") }
}

// MARK: - 互动

/// 互动类型（GET /v1/xijian/interactions）
struct InteractionInfo: Codable, Identifiable, Hashable {
    let id: String
    let name: String?
    let nsfw_level: String?
    let category: String?
    let cooldown_seconds: Int?
    let requires_state: [String: JSONValue]?

    var displayName: String { name ?? id }
}

// MARK: - 安全

/// 保护闸门状态（GET /v1/xijian/safety/gate/status）
struct GateStatus: Codable, Hashable {
    let enabled: Bool
    let guard_level: String?
    let audit_log_size: Int?
    let version: String?
}

/// 关闭保护挑战（第一步响应）
struct DisableChallenge: Codable, Hashable {
    let challenge_id: String?
    let expires_at: Double?
    let challenge_phrase: String?
    let enabled: Bool?
    let disabled_at: Double?
    let error: String?
}

/// 安全扫描结果（scan/input 与 scan/output）
struct SafetyScanResult: Codable, Hashable {
    let verdict: String?
    let blocked: String?
    let matches: [JSONValue]?
    let audit_id: String?
    let reasons: [String]?

    var isPass: Bool { verdict == "pass" || verdict == "allow" }
    var isWarn: Bool { verdict == "warn" }
    var isBlock: Bool { verdict == "block" }
}

// MARK: - 备份

/// 受保护模块（GET /v1/protected-modules）
struct ProtectedModule: Codable, Identifiable, Hashable {
    let module_name: String?
    let description: String?
    let auto_backup: Bool?
    let last_backup_at: Double?
    let character_id: String?

    var id: String { module_name ?? "module" }
}

/// 备份记录（GET /v1/backups）
struct BackupRecord: Codable, Identifiable, Hashable {
    let backup_id: String
    let character_id: String?
    let scope: String?
    let created_by: String?
    let file_path: String?
    let size_bytes: Int?
    let created_at: Double?

    var id: String { backup_id }
}

// MARK: - 剧情

/// 剧情设计（GET /v1/xijian/plots/designs）
struct PlotDesign: Codable, Identifiable, Hashable {
    let plot_id: String
    let title: String?
    let node_count: Int?
    let edge_count: Int?

    var id: String { plot_id }
}

/// 剧情运行时（POST /v1/xijian/plots/runtime）
struct PlotRuntime: Codable, Identifiable, Hashable {
    let runtime_id: String
    let plot_id: String?
    let world_id: String?
    let status: String?
    let current_node_id: String?
    let variables: [String: JSONValue]?

    var id: String { runtime_id }
}

// MARK: - 设置

/// 服务器设置（GET /v1/xijian/settings）— 自由结构
struct ServerSettings: Codable, Hashable {
    var values: [String: JSONValue]

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        values = try container.decode([String: JSONValue].self)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(values)
    }

    init(values: [String: JSONValue] = [:]) { self.values = values }

    subscript(key: String) -> JSONValue? { values[key] }

    var isEmpty: Bool { values.isEmpty }
}

// MARK: - 分页信封

/// OAI 风格分页列表信封 {object, data, has_more, first_id, last_id}
struct ListEnvelope<T: Decodable>: Decodable {
    let object: String?
    let data: [T]
    let has_more: Bool?
    let first_id: String?
    let last_id: String?
}

// MARK: - SSE 流式块

/// 聊天流式数据块（data: 行内容）
struct ChatStreamChunk: Codable, Hashable {
    let id: String?
    let object: String?
    let created: Int?
    let model: String?
    let choices: [Choice]?
    let usage: Usage?

    struct Choice: Codable, Hashable {
        let index: Int?
        let delta: Delta?
        let finish_reason: String?
    }

    struct Delta: Codable, Hashable {
        let role: String?
        let content: String?
    }

    struct Usage: Codable, Hashable {
        let prompt_tokens: Int?
        let completion_tokens: Int?
        let total_tokens: Int?
    }

    /// 本次块新增的文本
    var deltaContent: String {
        choices?.first?.delta?.content ?? ""
    }

    /// 是否结束（finish_reason 非空）
    var finishReason: String? {
        choices?.first?.finish_reason
    }
}

/// SSE 事件（支持 event: abort 块）
enum SSEEvent {
    case chunk(ChatStreamChunk)
    case done
    case aborted
}

// MARK: - 资源包

/// 资源包清单
struct PackManifest: Codable, Hashable {
    let schema: String?
    let package_id: String?
    let name: String
    let version: String
    let kind: String?  // character | world | mixed
    let author: String?
    let description: String?
    let dependencies: [String]?
    let created_at: String?
    let files: [String]?
    
    // 兼容 DevKit 提交字段（忽略）
    let developer_id: String?
    let submitted_at: String?
    let ai_ratio: Double?
    let notes: String?
    
    /// 实际类型：kind 为空时从 manifest 推导，或默认 mixed
    var effectiveKind: String { kind ?? "mixed" }
    
    /// 是否包含角色
    var hasCharacters: Bool { effectiveKind == "character" || effectiveKind == "mixed" }
    
    /// 是否包含世界观
    var hasWorlds: Bool { effectiveKind == "world" || effectiveKind == "mixed" }
}

/// 自定义解码放在扩展中，保留合成成员初始化器（PackInfo 解码兜底需要）
extension PackManifest {
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schema = try container.decodeIfPresent(String.self, forKey: .schema)
        package_id = try container.decodeIfPresent(String.self, forKey: .package_id)
        name = try container.decodeIfPresent(String.self, forKey: .name) ?? ""
        version = try container.decodeIfPresent(String.self, forKey: .version) ?? ""
        kind = try container.decodeIfPresent(String.self, forKey: .kind)
        author = try container.decodeIfPresent(String.self, forKey: .author)
        description = try container.decodeIfPresent(String.self, forKey: .description)
        dependencies = try container.decodeIfPresent([String].self, forKey: .dependencies)
        created_at = try container.decodeIfPresent(String.self, forKey: .created_at)
        files = try container.decodeIfPresent([String].self, forKey: .files)
        developer_id = try container.decodeIfPresent(String.self, forKey: .developer_id)
        submitted_at = try container.decodeIfPresent(String.self, forKey: .submitted_at)
        ai_ratio = try container.decodeIfPresent(Double.self, forKey: .ai_ratio)
        notes = try container.decodeIfPresent(String.self, forKey: .notes)
    }
}

/// 已安装资源包信息（GET /v1/xijian/packs）
struct PackInfo: Codable, Identifiable, Hashable {
    let package_id: String
    let kind: String  // character | world | mixed
    let name: String
    let version: String
    let path: String
    let manifest: PackManifest
    let loaded: Bool
    
    var id: String { package_id }
    
    var displayKind: String {
        switch kind {
        case "character": return loc("角色")
        case "world": return loc("世界观")
        case "mixed": return loc("混合")
        default: return kind
        }
    }
    
    var hasCharacters: Bool { kind == "character" || kind == "mixed" }
    var hasWorlds: Bool { kind == "world" || kind == "mixed" }
    
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        package_id = try container.decodeIfPresent(String.self, forKey: .package_id) ?? ""
        kind = try container.decodeIfPresent(String.self, forKey: .kind) ?? "mixed"
        name = try container.decodeIfPresent(String.self, forKey: .name) ?? ""
        version = try container.decodeIfPresent(String.self, forKey: .version) ?? ""
        path = try container.decodeIfPresent(String.self, forKey: .path) ?? ""
        manifest = try container.decodeIfPresent(PackManifest.self, forKey: .manifest) ?? PackManifest(schema: nil, package_id: nil, name: "", version: "", kind: nil, author: nil, description: nil, dependencies: nil, created_at: nil, files: nil, developer_id: nil, submitted_at: nil, ai_ratio: nil, notes: nil)
        loaded = try container.decodeIfPresent(Bool.self, forKey: .loaded) ?? false
    }
}

/// 导入任务信息（POST /v1/xijian/resources/import → 202，GET /v1/xijian/resources/imports/{job_id}）
struct ImportJobInfo: Codable, Identifiable, Hashable {
    let id: String
    let object: String?
    let status: String  // queued | running | completed | failed
    let kind: String?
    let name: String?
    let file_id: String?
    let package_id: String?
    let created_at: Double?
    let completed_at: Double?
    let error: String?
    let result: ImportResult?
    
    struct ImportResult: Codable, Hashable {
        let kind: String?
        let loaded_characters: Int?
        let loaded_worlds: Int?
        let loaded_memories: Int?
    }
    
    var isCompleted: Bool { status == "completed" }
    var isFailed: Bool { status == "failed" }
    var isRunning: Bool { status == "running" || status == "queued" }
    
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        // POST /v1/xijian/resources/import 的 202 响应只返回 job_id，需要兜底
        let jobID = (try? decoder.container(keyedBy: JobIDKey.self))
            .flatMap { try? $0.decodeIfPresent(String.self, forKey: .job_id) }
        id = try container.decodeIfPresent(String.self, forKey: .id) ?? jobID ?? ""
        object = try container.decodeIfPresent(String.self, forKey: .object)
        status = try container.decodeIfPresent(String.self, forKey: .status) ?? "queued"
        kind = try container.decodeIfPresent(String.self, forKey: .kind)
        name = try container.decodeIfPresent(String.self, forKey: .name)
        file_id = try container.decodeIfPresent(String.self, forKey: .file_id)
        package_id = try container.decodeIfPresent(String.self, forKey: .package_id)
        created_at = try container.decodeIfPresent(Double.self, forKey: .created_at)
        completed_at = try container.decodeIfPresent(Double.self, forKey: .completed_at)
        error = try container.decodeIfPresent(String.self, forKey: .error)
        result = try container.decodeIfPresent(ImportResult.self, forKey: .result)
    }

    private enum JobIDKey: String, CodingKey {
        case job_id
    }

    private enum CodingKeys: String, CodingKey {
        case id, object, status, kind, name, file_id, package_id
        case created_at, completed_at, error, result
    }
}

// MARK: - 时间格式化

extension Date {
    /// 格式化为 "HH:mm" 或 "MM-dd HH:mm"
    var xijianTimeText: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        if Calendar.current.isDateInToday(self) {
            formatter.dateFormat = "HH:mm"
        } else {
            formatter.dateFormat = "MM-dd HH:mm"
        }
        return formatter.string(from: self)
    }
}

extension Double {
    /// Unix 时间戳 → Date
    var xijianDate: Date { Date(timeIntervalSince1970: self) }
}

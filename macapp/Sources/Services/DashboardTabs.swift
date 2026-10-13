import Foundation

/// Dashboard 子页管理：让用户排序、启用/禁用侧边栏的 tab。
public actor DashboardTabs {
    public struct Tab: Sendable, Identifiable, Hashable, Codable {
        public let id: String  // 与 AppTab.rawValue 对应
        public var enabled: Bool
        public var order: Int
        public var label: String

        public init(id: String, enabled: Bool, order: Int, label: String) {
            self.id = id
            self.enabled = enabled
            self.order = order
            self.label = label
        }
    }

    public static let shared = DashboardTabs()

    public private(set) var tabs: [Tab] = []

    private let storePath: URL

    public init(storePath: URL? = nil) {
        if let p = storePath {
            self.storePath = p
        } else {
            self.storePath = URL(fileURLWithPath: ("~/.hermes/dashboard-tabs.json" as NSString).expandingTildeInPath)
        }
    }

    public static let defaults: [Tab] = [
        Tab(id: "状态", enabled: true, order: 0, label: "状态"),
        Tab(id: "终端", enabled: true, order: 1, label: "终端"),
        Tab(id: "Dashboard", enabled: true, order: 2, label: "Dashboard"),
        Tab(id: "Workspace", enabled: true, order: 3, label: "Workspace"),
        Tab(id: "角色", enabled: true, order: 4, label: "角色"),
        Tab(id: "记忆", enabled: true, order: 5, label: "记忆"),
        Tab(id: "MCP", enabled: true, order: 6, label: "MCP"),
        Tab(id: "Skills", enabled: true, order: 7, label: "Skills"),
        Tab(id: "Tokens", enabled: true, order: 8, label: "Tokens"),
        Tab(id: "网络", enabled: true, order: 9, label: "网络"),
        Tab(id: "主题", enabled: true, order: 10, label: "主题"),
        Tab(id: "模型", enabled: true, order: 11, label: "模型"),
        Tab(id: "设置", enabled: true, order: 12, label: "设置"),
    ]

    public func load() throws -> [Tab] {
        guard FileManager.default.fileExists(atPath: storePath.path) else {
            self.tabs = Self.defaults
            return self.tabs
        }
        let data = try Data(contentsOf: storePath)
        let loaded = try JSONDecoder().decode([Tab].self, from: data)
        // 合并默认：默认里有但文件里没有的补上
        var merged = loaded
        for def in Self.defaults where !merged.contains(where: { $0.id == def.id }) {
            merged.append(def)
        }
        self.tabs = merged.sorted { $0.order < $1.order }
        return self.tabs
    }

    public func setEnabled(id: String, enabled: Bool) throws {
        if let idx = tabs.firstIndex(where: { $0.id == id }) {
            tabs[idx].enabled = enabled
        }
        try persist()
    }

    public func reorder(ids: [String]) throws {
        for (newOrder, id) in ids.enumerated() {
            if let idx = tabs.firstIndex(where: { $0.id == id }) {
                tabs[idx].order = newOrder
            }
        }
        tabs.sort { $0.order < $1.order }
        try persist()
    }

    private func persist() throws {
        let data = try JSONEncoder().encode(tabs)
        try FileManager.default.createDirectory(at: storePath.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: storePath, options: .atomic)
    }
}

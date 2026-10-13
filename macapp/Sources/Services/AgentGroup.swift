import Foundation

/// Agent 群聊：让用户启动多个 profile 的 hermes 进程并行聊天。
/// 每个 agent 独立 PTY，输出汇总到一个时间线视图。
public actor AgentGroup {
    public struct Agent: Sendable, Identifiable, Hashable {
        public let id: String  // UUID
        public var name: String
        public var profileName: String
        public var status: Status
        public var startedAt: Date?

        public enum Status: String, Sendable, Codable {
            case stopped
            case starting
            case running
            case crashed
        }

        public init(id: String = UUID().uuidString, name: String, profileName: String, status: Status = .stopped, startedAt: Date? = nil) {
            self.id = id
            self.name = name
            self.profileName = profileName
            self.status = status
            self.startedAt = startedAt
        }
    }

    public static let shared = AgentGroup()

    public private(set) var agents: [Agent] = []

    /// 广播消息给所有 running agent。
    public func broadcast(message: String) async -> [(agentID: String, sent: Bool)] {
        var results: [(String, Bool)] = []
        for agent in agents where agent.status == .running {
            // 简化：通过 hermes CLI 发送（实际应注入到 PTY）
            let sent = await HermesCommandRunner.shared.sendChatMessage(profile: agent.profileName, message: message)
            results.append((agent.id, sent))
        }
        return results
    }

    public func add(name: String, profileName: String) {
        let agent = Agent(name: name, profileName: profileName)
        agents.append(agent)
    }

    public func remove(id: String) {
        agents.removeAll { $0.id == id }
    }

    public func start(id: String) async {
        guard let idx = agents.firstIndex(where: { $0.id == id }) else { return }
        agents[idx].status = .starting
        // 调用 hermes CLI 启动对应 profile
        do {
            try await HermesCommandRunner.shared.startProfile(agents[idx].profileName)
            agents[idx].status = .running
            agents[idx].startedAt = Date()
        } catch {
            agents[idx].status = .crashed
        }
    }

    public func stop(id: String) async {
        guard let idx = agents.firstIndex(where: { $0.id == id }) else { return }
        do {
            try await HermesCommandRunner.shared.stopProfile(agents[idx].profileName)
            agents[idx].status = .stopped
        } catch {
            agents[idx].status = .crashed
        }
    }
}

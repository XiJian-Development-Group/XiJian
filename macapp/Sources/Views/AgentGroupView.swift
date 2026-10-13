import SwiftUI
import XiJianManagerKit

/// Agent 群聊视图：每个 agent 是独立 profile，可启停，可发广播
struct AgentGroupView: View {
    @State private var agents: [AgentGroup.Agent] = []
    @State private var profiles: [ProfileManager.Profile] = []
    @State private var broadcastText: String = ""
    @State private var showAddSheet = false
    @State private var newName: String = ""
    @State private var newProfile: String = ""
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(xj: "Agent 群聊")
                    .font(.title)
                Spacer()
                Button(loc("+ 添加")) { showAddSheet = true }
            }
            .padding()

            Divider()

            if agents.isEmpty {
                ContentUnavailableView(
                    "未添加 Agent",
                    systemImage: "person.3",
                    description: Text("每个 Agent 是独立 profile，可以同时运行")
                )
            } else {
                List {
                    ForEach(agents) { agent in
                        agentRow(agent)
                    }
                }
            }

            Divider()

            HStack {
                TextField("群发消息（发给所有运行中的 agent）", text: $broadcastText)
                    .textFieldStyle(.roundedBorder)
                Button(loc("发送")) {
                    Task { await broadcast() }
                }
                .disabled(broadcastText.isEmpty)
            }
            .padding()

            if let err = error {
                Text(err).font(.callout).foregroundStyle(.red).padding(8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(isPresented: $showAddSheet) { addSheet }
        .task { await load() }
    }

    @ViewBuilder
    private func agentRow(_ a: AgentGroup.Agent) -> some View {
        HStack {
            Circle()
                .fill(statusColor(a.status))
                .frame(width: 10, height: 10)
            VStack(alignment: .leading) {
                Text(a.name).font(.headline)
                HStack {
                    Text("profile: \(a.profileName)")
                        .font(.caption).foregroundStyle(.secondary)
                    Text(a.status.rawValue)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    if let started = a.startedAt {
                        Text(started, format: .relative(presentation: .named))
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            Spacer()
            if a.status == .running || a.status == .starting {
                Button(loc("停止")) { Task { await stop(a.id) } }
            } else {
                Button(loc("启动")) { Task { await start(a.id) } }
                    .buttonStyle(.bordered)
            }
            Button(loc("删除"), role: .destructive) {
                Task { await remove(a.id) }
            }
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 4)
    }

    private var addSheet: some View {
        VStack(spacing: 16) {
            Text(xj: "添加 Agent").font(.headline)
            TextField("Agent 名称", text: $newName)
                .textFieldStyle(.roundedBorder)
                .frame(width: 400)
            Picker("Profile", selection: $newProfile) {
                Text("选择...").tag("")
                ForEach(profiles.map { $0.id }, id: \.self) { name in
                    Text(name).tag(name)
                }
            }
            .frame(width: 400)
            HStack {
                Button(loc("取消")) {
                    newName = ""
                    newProfile = ""
                    showAddSheet = false
                }
                .keyboardShortcut(.cancelAction)
                Spacer()
                Button(loc("添加")) {
                    Task { await add() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(newName.isEmpty || newProfile.isEmpty)
            }
        }
        .padding(20)
    }

    private func statusColor(_ s: AgentGroup.Agent.Status) -> Color {
        switch s {
        case .stopped: return .gray
        case .starting: return .yellow
        case .running: return .green
        case .crashed: return .red
        }
    }

    private func load() async {
        agents = await AgentGroup.shared.agents
        if let loaded = try? await ProfileManager.shared.listProfiles() {
            profiles = loaded
        }
    }

    private func add() async {
        await AgentGroup.shared.add(name: newName, profileName: newProfile)
        newName = ""
        newProfile = ""
        showAddSheet = false
        await load()
    }

    private func remove(_ id: String) async {
        await AgentGroup.shared.remove(id: id)
        await load()
    }

    private func start(_ id: String) async {
        await AgentGroup.shared.start(id: id)
        await load()
    }

    private func stop(_ id: String) async {
        await AgentGroup.shared.stop(id: id)
        await load()
    }

    private func broadcast() async {
        let msg = broadcastText
        broadcastText = ""
        _ = await AgentGroup.shared.broadcast(message: msg)
    }
}

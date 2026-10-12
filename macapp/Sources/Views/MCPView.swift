import SwiftUI
import XiJianManagerKit

/// MCP server 管理视图
struct MCPView: View {
    @State private var servers: [MCPManager.MCPServer] = []
    @State private var error: String?
    @State private var selectedId: String?
    @State private var isLoading = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(xj: "MCP 管理")
                    .font(.title)
                Spacer()
                Button(loc("刷新")) { Task { await load() } }
                Button(loc("+ 新建")) { selectedId = "__new__" }
            }
            .padding()

            Divider()

            if isLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                HSplitView {
                    serverList
                        .frame(minWidth: 220, idealWidth: 280)
                    editor
                        .frame(minWidth: 400)
                }
            }

            if let err = error {
                Text(err).font(.callout).foregroundStyle(.red).padding(8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task { await load() }
    }

    private var serverList: some View {
        List(selection: $selectedId) {
            ForEach(servers) { s in
                HStack {
                    Circle()
                        .fill(s.enabled ? Color.green : Color.secondary)
                        .frame(width: 8, height: 8)
                    Text(s.id).font(.body)
                }
                .tag(s.id)
            }
        }
        .frame(minWidth: 200, minHeight: 200)
    }

    @ViewBuilder
    private var editor: some View {
        if let id = selectedId {
            MCPEditor(
                initialServer: id == "__new__"
                    ? MCPManager.MCPServer(id: "", command: "")
                    : servers.first { $0.id == id } ?? MCPManager.MCPServer(id: id, command: ""),
                isNew: id == "__new__",
                onSave: { server in
                    Task {
                        do {
                            try await MCPManager.shared.addServer(server)
                            await load()
                            selectedId = server.id
                        } catch {
                            self.error = "保存失败：\(error)"
                        }
                    }
                },
                onDelete: id == "__new__" ? nil : { name in
                    Task {
                        do {
                            try await MCPManager.shared.removeServer(name: name)
                            selectedId = nil
                            await load()
                        } catch {
                            self.error = "删除失败：\(error)"
                        }
                    }
                },
                onToggle: id == "__new__" ? nil : { name, enabled in
                    Task {
                        do {
                            try await MCPManager.shared.toggleServer(name: name, enabled: enabled)
                            await load()
                        } catch {
                            self.error = "切换失败：\(error)"
                        }
                    }
                }
            )
        } else {
            ContentUnavailableView("选中 MCP server", systemImage: "server.rack", description: Text("左侧选一个 server 编辑"))
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            servers = try await MCPManager.shared.listServers()
            error = nil
        } catch {
            self.error = "加载失败：\(error)"
        }
    }
}

private struct MCPEditor: View {
    let initialServer: MCPManager.MCPServer
    let isNew: Bool
    let onSave: (MCPManager.MCPServer) -> Void
    let onDelete: ((String) -> Void)?
    let onToggle: ((String, Bool) -> Void)?

    @State private var id: String = ""
    @State private var command: String = ""
    @State private var argsText: String = ""
    @State private var envText: String = ""
    @State private var enabled: Bool = true

    var body: some View {
        Form {
            Section("基本信息") {
                TextField("名称（唯一 ID）", text: $id)
                    .disabled(!isNew)
                TextField("命令", text: $command)
                TextField("参数（一行一个）", text: $argsText, axis: .vertical)
                    .lineLimit(3...6)
                TextField("环境变量（KEY=VALUE，一行一个）", text: $envText, axis: .vertical)
                    .lineLimit(3...6)
                Toggle("启用", isOn: $enabled)
            }
            HStack {
                Button("保存", action: save)
                    .keyboardShortcut(.defaultAction)
                if let onDelete {
                    Button("删除", role: .destructive) { onDelete(id) }
                }
                Spacer()
            }
        }
        .padding()
        .onAppear(perform: load)
    }

    private func load() {
        id = initialServer.id
        command = initialServer.command
        argsText = initialServer.args.joined(separator: "\n")
        envText = initialServer.env.map { "\($0.key)=\($0.value)" }.joined(separator: "\n")
        enabled = initialServer.enabled
    }

    private func save() {
        let args = argsText.split(separator: "\n").map { String($0) }.filter { !$0.isEmpty }
        var env: [String: String] = [:]
        for line in envText.split(separator: "\n") {
            let parts = line.split(separator: "=", maxSplits: 1)
            if parts.count == 2 {
                env[String(parts[0])] = String(parts[1])
            }
        }
        let server = MCPManager.MCPServer(
            id: id.isEmpty ? "new-server" : id,
            command: command,
            args: args,
            env: env,
            enabled: enabled
        )
        onSave(server)
    }
}

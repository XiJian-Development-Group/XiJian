import SwiftUI
import XiJianKit

/// 剧情系统：设计列表、运行时创建/推进/暂停/恢复/删除
struct PlotSettingsView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme

    @State private var designs: [PlotDesign] = []
    @State private var runtimes: [PlotRuntime] = []
    @State private var worlds: [WorldInfo] = []

    @State private var isLoading = false
    @State private var showError = false
    @State private var errorMessage = ""

    @State private var selectedDesignID: String?
    @State private var selectedWorldID: String?
    @State private var showCreateRuntime = false
    @State private var runtimeResult: String?
    /// 结构化初始变量行（U3）
    @State private var initialVariablesRows: [KeyValueRow] = []
    @State private var advanceResult: String?

    var body: some View {
        Form {
            Section(loc("剧情设计")) {
                if designs.isEmpty {
                    if isLoading {
                        ProgressView(loc("加载中..."))
                    } else {
                        Text(loc("暂无剧情设计（DevKit 工作目录为空）。"))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(designs) { design in
                        HStack {
                            Image(systemName: "film.stack")
                                .foregroundStyle(theme.accentColor)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(design.title ?? design.plot_id)
                                    .font(.subheadline)
                                Text(loc("%@ · %lld 节点 / %lld 边", design.plot_id, design.node_count ?? 0, design.edge_count ?? 0))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }

            Section(loc("创建剧情运行时")) {
                if designs.isEmpty {
                    Text(loc("请先准备剧情设计。"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Picker(loc("剧情设计"), selection: $selectedDesignID) {
                        ForEach(designs) { design in
                            Text(design.title ?? design.plot_id).tag(String?.some(design.plot_id))
                        }
                    }
                    .onAppear {
                        if selectedDesignID == nil { selectedDesignID = designs.first?.plot_id }
                    }

                    if worlds.isEmpty {
                        Text(loc("无可用世界。"))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        Picker(loc("目标世界"), selection: $selectedWorldID) {
                            ForEach(worlds) { world in
                                Text(world.name ?? world.worldID).tag(String?.some(world.worldID))
                            }
                        }
                        .onAppear {
                            if selectedWorldID == nil { selectedWorldID = worlds.first?.worldID }
                        }
                    }

                    KeyValueListEditor(rows: $initialVariablesRows)
                        .frame(maxHeight: 160)

                    Button(loc("创建并启动")) {
                        Task { await createRuntime() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(selectedDesignID == nil || selectedWorldID == nil)
                }
            }

            Section(loc("运行时实例")) {
                if runtimes.isEmpty {
                    Text(loc("暂无运行时。"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(runtimes) { runtime in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Image(systemName: "play.rectangle")
                                    .foregroundStyle(statusColor(runtime.status ?? ""))
                                Text("\(runtime.plot_id ?? "?") · \(runtime.world_id ?? "?")")
                                    .font(.subheadline)
                                Spacer()
                                Text(runtime.status ?? "unknown")
                                    .font(.caption2)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 1)
                                    .background(Capsule().fill(statusColor(runtime.status ?? "").opacity(0.15)))
                                    .foregroundStyle(statusColor(runtime.status ?? ""))
                            }
                            if let node = runtime.current_node_id {
                                Text(loc("当前节点：%@", node))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            HStack(spacing: XJSpacing.sm) {
                                Button(loc("推进剧情")) {
                                    Task { await advance(runtime) }
                                }
                                .controlSize(.small)
                                .disabled(runtime.status == "completed" || runtime.status == "failed")

                                if runtime.status == "paused" {
                                    Button(loc("恢复")) {
                                        Task { await resume(runtime) }
                                    }
                                    .controlSize(.small)
                                } else if runtime.status == "running" {
                                    Button(loc("暂停")) {
                                        Task { await pause(runtime) }
                                    }
                                    .controlSize(.small)
                                }

                                Button(loc("删除"), role: .destructive) {
                                    Task { await delete(runtime) }
                                }
                                .controlSize(.small)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }

            if let result = runtimeResult {
                Section(loc("结果")) {
                    Text(result)
                        .font(.caption)
                        .textSelection(.enabled)
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(loc("剧情系统"))
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .task {
            await load()
        }
    }

    // MARK: 加载

    private func load() async {
        guard let client = core.makeClient() else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            designs = try await client.listPlotDesigns()
            runtimes = try await client.listPlotRuntimes(worldID: nil)
            worlds = try await client.listWorlds()
        } catch {
            presentError(error)
        }
    }

    // MARK: 动作

    private func createRuntime() async {
        guard let client = core.makeClient(), let plotID = selectedDesignID, let worldID = selectedWorldID else { return }
        do {
            let variables = KVListParser.toJSON(initialVariablesRows)
            let runtime = try await client.createPlotRuntime(plotID: plotID, worldID: worldID, initialVariables: variables.isEmpty ? nil : variables)
            runtimeResult = loc("运行时已创建：%@", runtime.runtime_id)
            await load()
        } catch {
            presentError(error)
        }
    }

    private func advance(_ runtime: PlotRuntime) async {
        guard let client = core.makeClient() else { return }
        do {
            let result = try await client.advancePlotRuntime(runtime.runtime_id, chooseEdgeID: nil)
            let node = result["current_node_id"]?.stringValue ?? result["message"]?.stringValue ?? ""
            runtimeResult = node.isEmpty ? loc("剧情已推进。") : loc("剧情已推进。%@", loc("当前节点：%@", node))
            await load()
        } catch {
            presentError(error)
        }
    }

    private func pause(_ runtime: PlotRuntime) async {
        guard let client = core.makeClient() else { return }
        do {
            _ = try await client.pausePlotRuntime(runtime.runtime_id)
            runtimeResult = loc("已暂停：%@", runtime.runtime_id)
            await load()
        } catch {
            presentError(error)
        }
    }

    private func resume(_ runtime: PlotRuntime) async {
        guard let client = core.makeClient() else { return }
        do {
            _ = try await client.resumePlotRuntime(runtime.runtime_id)
            runtimeResult = loc("已恢复：%@", runtime.runtime_id)
            await load()
        } catch {
            presentError(error)
        }
    }

    private func delete(_ runtime: PlotRuntime) async {
        guard let client = core.makeClient() else { return }
        do {
            try await client.deletePlotRuntime(runtime.runtime_id)
            runtimes.removeAll { $0.runtime_id == runtime.runtime_id }
            runtimeResult = loc("已删除：%@", runtime.runtime_id)
        } catch {
            presentError(error)
        }
    }

    // MARK: 辅助

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "running": return .green
        case "paused": return .orange
        case "completed": return .blue
        case "failed": return .red
        default: return .gray
        }
    }

    private func presentError(_ error: Error) {
        if let apiError = error as? APIError {
            errorMessage = apiError.message
        } else {
            errorMessage = error.localizedDescription
        }
        showError = true
    }
}

import SwiftUI
import XiJianKit

/// 远程模型行
private struct RemoteModelRow: View {
    let model: AIModel
    let onDelete: @MainActor () async -> Void
    let onLoad: @MainActor () async -> Void
    let onUnload: @MainActor () async -> Void

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(model.name)
                    .font(.headline)
                    .textSelection(.enabled)
                HStack(spacing: 12) {
                    if let backend = model.backendName {
                        Label(backend, systemImage: "cloud")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if model.family.isEmpty == false {
                        Label(model.family, systemImage: "tag")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
            if model.loaded {
                Button("卸载") { Task { await onUnload() } }
                    .controlSize(.small)
            } else {
                Button("加载") { Task { await onLoad() } }
                    .controlSize(.small)
            }
            Button(role: .destructive) {
                Task { await onDelete() }
            } label: {
                Image(systemName: "trash")
            }
            .controlSize(.small)
        }
        .padding(.vertical, 2)
    }
}

/// 本地模型行（config.toml 注册，只读 + 加载/卸载）
private struct LocalModelRow: View {
    let info: LocalModelDisplay
    let isBusy: Bool
    let onLoad: @MainActor () async -> Void
    let onUnload: @MainActor () async -> Void

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(info.id)
                    .font(.headline)
                    .textSelection(.enabled)
                HStack(spacing: 12) {
                    Label("本地 · \(info.backend)", systemImage: "internaldrive")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if info.contextLength > 0 {
                        Label("\(info.contextLength / 1000)k ctx", systemImage: "doc.text")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
            if info.loaded {
                Label("已加载", systemImage: "checkmark.circle.fill")
                    .font(.caption)
                    .foregroundStyle(.green)
                Button("卸载") { Task { await onUnload() } }
                    .controlSize(.small)
                    .disabled(isBusy)
            } else {
                Button("加载") { Task { await onLoad() } }
                    .controlSize(.small)
                    .disabled(isBusy)
            }
        }
        .padding(.vertical, 2)
    }
}

/// 本地模型展示数据（来自 /v1/models）
struct LocalModelDisplay: Identifiable {
    let id: String
    let backend: String
    let loaded: Bool
    let contextLength: Int
}

// MARK: - 模型管理页面

struct ModelManagementView: View {
    @Environment(CoreManager.self) private var core

    @State private var localModels: [LocalModelDisplay] = []
    @State private var remoteModels: [AIModel] = []
    @State private var backends: [AIBackend] = []
    @State private var busyLocalID: String?
    @State private var errorMessage: String?
    @State private var showError = false
    @State private var showAddSheet = false

    var body: some View {
        List {
            // 本地模型（config.toml）
            Section {
                ForEach(localModels) { m in
                    LocalModelRow(
                        info: m,
                        isBusy: busyLocalID == m.id,
                        onLoad: { await loadLocal(m) },
                        onUnload: { await unloadLocal(m) }
                    )
                }
                if localModels.isEmpty {
                    Text("暂无本地模型。可在 Core 配置文件 config.toml 的 [[models]] 中注册 MLX / GGUF 模型。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } header: {
                Label("本地模型（MLX / GGUF）", systemImage: "internaldrive")
            } footer: {
                Text("本地模型在配置文件中注册，此处仅支持加载与卸载。")
                    .font(.caption2)
            }

            // 远程模型（动态后端）
            Section {
                ForEach(remoteModels) { m in
                    RemoteModelRow(
                        model: m,
                        onDelete: { await deleteRemote(m) },
                        onLoad: { await loadRemote(m) },
                        onUnload: { await unloadRemote(m) }
                    )
                }
                if remoteModels.isEmpty {
                    Text("暂无远程模型，点击下方按钮添加。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } header: {
                HStack {
                    Label("远程模型（OpenAI 兼容后端）", systemImage: "cloud")
                    Spacer()
                    Button {
                        showAddSheet = true
                    } label: {
                        Label("添加远程模型", systemImage: "plus.circle.fill")
                    }
                    .disabled(backends.isEmpty)
                    .help(backends.isEmpty ? "请先在「AI 后端」中添加并配置后端" : "添加远程模型")
                }
            } footer: {
                if backends.isEmpty {
                    Text("添加远程模型前，请先在 设置 → AI 后端 中完成后端配置。")
                        .font(.caption2)
                }
            }
        }
        .navigationTitle("模型管理")
        .sheet(isPresented: $showAddSheet) {
            AddRemoteModelSheet(backends: backends) { newModels in
                Task { await addRemoteBatch(newModels) }
            }
        }
        .task { await loadData() }
        .alert("出错了", isPresented: $showError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(errorMessage ?? "")
        }
        .refreshable { await loadData() }
    }

    private func loadData() async {
        do {
            let client = try core.makeClientOrThrow()
            async let locals = client.listModels()
            async let remotes = client.listAIModels()
            async let bknds = client.listAIBackends()
            let (l, r, b) = try await (locals, remotes, bknds)
            backends = b
            remoteModels = r
            localModels = l.map { m in
                let meta = m.xijian
                return LocalModelDisplay(
                    id: m.id,
                    backend: meta?.backend ?? "—",
                    loaded: meta?.loaded ?? false,
                    contextLength: meta?.context_length ?? 0
                )
            }
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    // MARK: 本地模型操作

    private func loadLocal(_ m: LocalModelDisplay) async {
        busyLocalID = m.id
        defer { busyLocalID = nil }
        do {
            let client = try core.makeClientOrThrow()
            _ = try await client.loadModel(m.id)
            await loadData()
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func unloadLocal(_ m: LocalModelDisplay) async {
        busyLocalID = m.id
        defer { busyLocalID = nil }
        do {
            let client = try core.makeClientOrThrow()
            _ = try await client.unloadModel(m.id)
            await loadData()
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    // MARK: 远程模型操作

    private func addRemoteBatch(_ models: [AIModel]) async {
        do {
            let client = try core.makeClientOrThrow()
            var created: [AIModel] = []
            for m in models {
                created.append(try await client.createAIModel(m))
            }
            remoteModels.append(contentsOf: created)
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func deleteRemote(_ model: AIModel) async {
        do {
            let client = try core.makeClientOrThrow()
            try await client.deleteAIModel(id: model.id)
            remoteModels.removeAll { $0.id == model.id }
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func loadRemote(_ model: AIModel) async {
        do {
            let client = try core.makeClientOrThrow()
            let updated = try await client.loadDynamicModel(id: model.id)
            if let idx = remoteModels.firstIndex(where: { $0.id == model.id }) {
                remoteModels[idx].loaded = updated.loaded
            }
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func unloadRemote(_ model: AIModel) async {
        do {
            let client = try core.makeClientOrThrow()
            let updated = try await client.unloadDynamicModel(id: model.id)
            if let idx = remoteModels.firstIndex(where: { $0.id == model.id }) {
                remoteModels[idx].loaded = updated.loaded
            }
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }
}

// MARK: - 添加远程模型弹窗

/// 添加远程模型：从后端自动获取（多选）或手动批量输入模型 ID。
private struct AddRemoteModelSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(CoreManager.self) private var core

    let backends: [AIBackend]
    /// 返回待创建的模型列表（每个选中/输入的 ID 一个条目）
    let onSave: ([AIModel]) -> Void

    enum SourceMode: String, CaseIterable, Identifiable {
        case fetch = "自动获取"
        case manual = "手动输入"
        var id: String { rawValue }
    }

    @State private var mode: SourceMode = .fetch
    @State private var backendID: String = ""
    @State private var remoteList: [RemoteModelInfo] = []
    @State private var selectedIDs: Set<String> = []
    @State private var manualIDsText: String = ""
    @State private var isFetching = false
    @State private var hasFetched = false
    @State private var filterText: String = ""

    private var filteredRemote: [RemoteModelInfo] {
        guard !filterText.isEmpty else { return remoteList }
        return remoteList.filter { $0.id.localizedCaseInsensitiveContains(filterText) }
    }

    /// 解析手动输入的多个 ID（逗号 / 空格 / 换行分隔）
    private var manualIDs: [String] {
        manualIDsText
            .split(whereSeparator: { ",， \n\t".contains($0) })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    private var pendingCreations: [AIModel] {
        let ids: [String]
        if mode == .fetch {
            ids = selectedIDs.sorted()
        } else {
            ids = manualIDs
        }
        let backendName = backends.first { $0.id == backendID }?.name
        return ids.map { id in
            AIModel(
                id: "",
                name: id,
                backendID: backendID,
                backendName: backendName,
                filename: "",
                family: "",
                sizeGB: 0,
                quant: "",
                contextLength: 0,
                minRamGB: 0,
                loaded: false,
                createdAt: Date(),
                updatedAt: Date()
            )
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("目标后端") {
                    Picker("后端", selection: $backendID) {
                        ForEach(backends) { b in
                            Text(b.name).tag(b.id)
                        }
                    }
                    .disabled(backends.isEmpty)
                }

                Section("添加方式") {
                    Picker("方式", selection: $mode) {
                        ForEach(SourceMode.allCases) { m in
                            Text(m.rawValue).tag(m)
                        }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: mode) {
                        selectedIDs.removeAll()
                    }
                }

                switch mode {
                case .fetch:
                    fetchSection
                case .manual:
                    manualSection
                }

                Section {
                    Button(mode == .fetch ? "获取模型列表" : "添加 \(manualIDs.count) 个模型") {
                        save()
                    }
                    .disabled(!canSave)
                    .frame(maxWidth: .infinity)
                } footer: {
                    Text(previewText)
                        .font(.caption2)
                }
            }
            .formStyle(.grouped)
            .navigationTitle("添加远程模型")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
            }
            .onAppear {
                if backendID.isEmpty, let first = backends.first {
                    backendID = first.id
                }
            }
            .onChange(of: backendID) {
                remoteList.removeAll()
                selectedIDs.removeAll()
                hasFetched = false
            }
        }
        .frame(minWidth: 480, idealWidth: 560, minHeight: 520, idealHeight: 600)
    }

    // MARK: 自动获取区

    @ViewBuilder
    private var fetchSection: some View {
        Section {
            HStack {
                Button {
                    Task { await fetchRemote() }
                } label: {
                    if isFetching {
                        ProgressView().controlSize(.small)
                    } else {
                        Label(hasFetched ? "重新获取" : "连接并获取", systemImage: "arrow.triangle.2.circlepath")
                    }
                }
                .disabled(isFetching || backendID.isEmpty)

                if hasFetched {
                    Text("共 \(remoteList.count) 个可用模型")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                if hasFetched && !remoteList.isEmpty {
                    Button(selectedIDs.count == filteredRemote.count ? "取消全选" : "全选") {
                        toggleSelectAll()
                    }
                    .controlSize(.small)
                }
            }

            if hasFetched && !remoteList.isEmpty {
                TextField("筛选…", text: $filterText)
                    .textFieldStyle(.roundedBorder)
                    .controlSize(.small)

                ForEach(filteredRemote) { rm in
                    HStack {
                        Image(systemName: selectedIDs.contains(rm.id) ? "checkmark.square.fill" : "square")
                            .foregroundStyle(selectedIDs.contains(rm.id) ? Color.accentColor : Color.secondary)
                            .onTapGesture { toggleSelect(rm.id) }
                        Text(rm.id)
                            .font(.system(.body, design: .monospaced))
                            .textSelection(.enabled)
                        Spacer()
                    }
                    .contentShape(Rectangle())
                    .onTapGesture { toggleSelect(rm.id) }
                }
            } else if hasFetched && remoteList.isEmpty {
                Text("后端未返回任何模型，请检查后端配置是否正确。")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        } header: {
            Text("从后端获取可用模型")
        }
    }

    // MARK: 手动输入区

    @ViewBuilder
    private var manualSection: some View {
        Section {
            TextField("model-id-a, model-id-b, …", text: $manualIDsText, axis: .vertical)
                .lineLimit(3...6)
                .font(.system(.body, design: .monospaced))

            if !manualIDs.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("将添加以下 \(manualIDs.count) 个模型：")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    FlowChips(items: manualIDs)
                }
            }
        } header: {
            Text("手动输入模型 ID")
        } footer: {
            Text("支持英文逗号、空格或换行分隔，可一次输入多个。ID 需与后端实际提供的模型名一致。")
                .font(.caption2)
        }
    }

    // MARK: 状态

    private var canSave: Bool {
        guard !backendID.isEmpty else { return false }
        if isFetching { return false }
        return mode == .fetch ? !selectedIDs.isEmpty : !manualIDs.isEmpty
    }

    private var previewText: String {
        let n = mode == .fetch ? selectedIDs.count : manualIDs.count
        guard n > 0 else { return "" }
        return "将为后端「\(backends.first { $0.id == backendID }?.name ?? "")」创建 \(n) 个远程模型配置。"
    }

    private func toggleSelect(_ id: String) {
        if selectedIDs.contains(id) {
            selectedIDs.remove(id)
        } else {
            selectedIDs.insert(id)
        }
    }

    private func toggleSelectAll() {
        let visible = Set(filteredRemote.map(\.id))
        if selectedIDs.isSuperset(of: visible) && !visible.isEmpty {
            selectedIDs.subtract(visible)
        } else {
            selectedIDs.formUnion(visible)
        }
    }

    private func fetchRemote() async {
        isFetching = true
        defer { isFetching = false }
        do {
            let client = try core.makeClientOrThrow()
            remoteList = try await client.listRemoteModels(backendID: backendID)
            hasFetched = true
            selectedIDs.removeAll()
        } catch {
            remoteList.removeAll()
            hasFetched = true
        }
    }

    private func save() {
        let models = pendingCreations
        guard !models.isEmpty else { return }
        onSave(models)
        dismiss()
    }
}

/// 简易流式布局标签组（用于预览手动输入的 ID 列表）
struct FlowChips: View {
    let items: [String]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 6)], alignment: .leading, spacing: 6) {
            ForEach(items, id: \.self) { item in
                Text(item)
                    .font(.system(size: 11, design: .monospaced))
                    .lineLimit(1)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.accentColor.opacity(0.12))
                    .clipShape(Capsule())
            }
        }
    }
}

// MARK: - 数据模型

/// 远程后端返回的可用模型条目
struct RemoteModelInfo: Codable, Identifiable, Hashable {
    let id: String
    let owned_by: String?
}

/// 动态远程模型（/v1/xijian/models）
struct AIModel: Identifiable, Codable, Hashable, Sendable {
    let id: String
    let name: String
    let backendID: String
    let backendName: String?
    let filename: String
    let family: String
    let sizeGB: Double
    let quant: String
    let contextLength: Int
    let minRamGB: Double
    var loaded: Bool
    let createdAt: Date
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, name, backendID = "backend_id", backendName = "backend_name"
        case filename, family, sizeGB = "size_b", quant
        case contextLength = "context_length", minRamGB = "min_ram_gb"
        case loaded, createdAt = "created_at", updatedAt = "updated_at"
    }
}

// MARK: - API 扩展

extension APIClient {
    /// 拉取指定后端的可用远程模型列表（GET /v1/xijian/backends/{id}/remote-models）
    func listRemoteModels(backendID: String) async throws -> [RemoteModelInfo] {
        let envelope: ListEnvelope<RemoteModelInfo> = try await get(
            "/v1/xijian/backends/\(backendID)/remote-models"
        )
        return envelope.data
    }

    func listAIModels() async throws -> [AIModel] {
        let envelope: ListEnvelope<AIModel> = try await get("/v1/xijian/models")
        return envelope.data
    }

    func createAIModel(_ model: AIModel) async throws -> AIModel {
        try await post("/v1/xijian/models", body: model)
    }

    func updateAIModel(_ model: AIModel) async throws -> AIModel {
        try await patch("/v1/xijian/models/\(model.id)", body: model)
    }

    func deleteAIModel(id: String) async throws {
        try await deleteVoid("/v1/xijian/models/\(id)")
    }

    /// 加载动态远程模型（POST /v1/xijian/models/{id}/load）
    func loadDynamicModel(id: String) async throws -> AIModel {
        try await post("/v1/xijian/models/\(id)/load")
    }

    /// 卸载动态远程模型（POST /v1/xijian/models/{id}/unload）
    func unloadDynamicModel(id: String) async throws -> AIModel {
        try await post("/v1/xijian/models/\(id)/unload")
    }
}

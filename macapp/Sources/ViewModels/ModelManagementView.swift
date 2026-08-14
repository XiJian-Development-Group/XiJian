import SwiftUI
import XiJianKit

/// ��型管理行
private struct ModelRow: View {
    let model: AIModel
    let isSelected: Bool
    let onSelect: () -> Void
    let onDelete: () -> Void
    let onLoad: () -> Void
    let onUnload: () -> Void

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(model.displayName)
                        .font(.headline)
                    if model.loaded {
                        Text("已加载")
                            .font(.caption)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.green.opacity(0.2))
                            .foregroundStyle(Color.green)
                            .clipShape(Capsule())
                    } else {
                        Text("未加载")
                            .font(.caption)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.gray.opacity(0.2))
                            .foregroundStyle(.secondary)
                            .clipShape(Capsule())
                    }
                }
                HStack(spacing: 12) {
                    if let backend = model.backendName {
                        Label(backend, systemImage: "server.rack")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if model.contextLength > 0 {
                        Label("\(model.contextLength / 1000)k", systemImage: "doc.text")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    if model.minRamGB > 0 {
                        Label("\(String(format: "%.1f", model.minRamGB))GB", systemImage: "memorychip")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
            Menu {
                if model.loaded {
                    Button("��载", systemImage: "eject.fill", action: onUnload)
                } else {
                    Button("加载", systemImage: "play.fill", action: onLoad)
                }
                Divider()
                Button("编辑", systemImage: "pencil") {
                    // TODO: 编辑模型
                }
                Divider()
                Button(role: .destructive) {
                    onDelete()
                } label: {
                    Label("删除", systemImage: "trash")
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .foregroundStyle(.secondary)
            }
            .menuStyle(.borderlessButton)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
        .background(isSelected ? Color.accentColor.opacity(0.1) : Color.clear)
    }
}

/// ��型管理页面
struct ModelManagementView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme

    @State private var models: [AIModel] = []
    @State private var backends: [AIBackend] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showError = false
    @State private var showAddSheet = false
    @State private var editingModel: AIModel?

    var body: some View {
        List {
            Section {
                ForEach(models) { model in
                    ModelRow(
                        model: model,
                        isSelected: editingModel?.id == model.id,
                        onSelect: { editingModel = model },
                        onDelete: { deleteModel(model) },
                        onLoad: { loadModel(model) },
                        onUnload: { unloadModel(model) }
                    )
                }
                if models.isEmpty {
                    Text("��无模型配置")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 20)
                }
            } header: {
                HStack {
                    Text("模型管理")
                    Spacer()
                    Button {
                        showAddSheet = true
                    } label: {
                        Label("��加模型", systemImage: "plus.circle.fill")
                    }
                }
            }

            if let editing = editingModel {
                Section("编辑模型：\(editing.name)") {
                    ModelEditForm(model: editing, backends: backends) { updated in
                        Task { await updateModel(updated) }
                    } onCancel: {
                        editingModel = nil
                    }
                }
            }
        }
        .navigationTitle("模型管理")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showAddSheet = true
                } label: {
                    Label("��加模型", systemImage: "plus")
                }
            }
        }
        .sheet(isPresented: $showAddSheet) {
            NavigationStack {
                ModelEditForm(model: nil, backends: backends) { new in
                    Task { await addModel(new) }
                } onCancel: {
                    showAddSheet = false
                }
            }
        }
        .task { await loadData() }
        .alert("错误", isPresented: $showError, presenting: errorMessage) { _ in
            Button("好") {}
        } message: { msg in
            Text(msg)
        }
        .refreshable { await loadData() }
    }

    private func loadData() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let client = try core.makeClientOrThrow()
            async let modelsTask = client.listAIModels()
            async let backendsTask = client.listAIBackends()
            models = try await modelsTask
            backends = try await backendsTask
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func addModel(_ model: AIModel) async {
        do {
            let client = try core.makeClientOrThrow()
            let created = try await client.createAIModel(model)
            models.append(created)
            showAddSheet = false
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func updateModel(_ model: AIModel) async {
        do {
            let client = try core.makeClientOrThrow()
            let updated = try await client.updateAIModel(model)
            if let idx = models.firstIndex(where: { $0.id == model.id }) {
                models[idx] = updated
            }
            editingModel = nil
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func loadModel(_ model: AIModel) async {
        do {
            let client = try core.makeClientOrThrow()
            try await client.loadAIModel(id: model.id)
            if let idx = models.firstIndex(where: { $0.id == model.id }) {
                models[idx].loaded = true
            }
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func unloadModel(_ model: AIModel) async {
        do {
            let client = try core.makeClientOrThrow()
            try await client.unloadAIModel(id: model.id)
            if let idx = models.firstIndex(where: { $0.id == model.id }) {
                models[idx].loaded = false
            }
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func deleteModel(_ model: AIModel) async {
        do {
            let client = try core.makeClientOrThrow()
            try await client.deleteAIModel(id: model.id)
            models.removeAll { $0.id == model.id }
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }
}

/// ��型编辑/创建表单
private struct ModelEditForm: View {
    let model: AIModel?
    let backends: [AIBackend]
    let onSave: (AIModel) -> Void
    let onCancel: () -> Void

    @State private var name: String = ""
    @State private var backendID: String = ""
    @State private var filename: String = ""
    @State private var family: String = ""
    @State private var sizeGB: Double = 0
    @State private var quant: String = ""
    @State private var contextLength: Int = 0
    @State private var minRamGB: Double = 0
    @State private var isSaving = false

    var body: some View {
        Form {
            Section("基本信息") {
                TextField("模型名称 (例: gpt-4o, Qwen2.5-7B)", text: $name)

                Picker("后端", selection: $backendID) {
                    ForEach(backends) { b in
                        Text(b.name).tag(b.id)
                    }
                }
                .disabled(backends.isEmpty)

                if backends.isEmpty {
                    Text("请先在 AI 后端页面��加后端")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("模型参数 (可选)") {
                TextField("文件名 / ��径", text: $filename)
                TextField("模型族 (例: gpt-4o, qwen2.5)", text: $family)
                HStack {
                    Text("大小 (GB)")
                    Spacer()
                    TextField("0", value: $sizeGB, format: .number)
                        .keyboardType(.decimalPad)
                        .frame(width: 80)
                }
                TextField("量化 (例: q4_k_m, 4bit)", text: $quant)
                HStack {
                    Text("上下文长度")
                    Spacer()
                    TextField("0", value: $contextLength, format: .number)
                        .keyboardType(.numberPad)
                        .frame(width: 100)
                }
                HStack {
                    Text("最小内存 (GB)")
                    Spacer()
                    TextField("0", value: $minRamGB, format: .number)
                        .keyboardType(.decimalPad)
                        .frame(width: 80)
                }
            }

            Section {
                Button(isSaving ? "保存中…" : (model == nil ? "创建" : "保存")) {
                    save()
                }
                .disabled(name.isEmpty || backendID.isEmpty || isSaving)
                .frame(maxWidth: .infinity, alignment: .center)
            }
        }
        .navigationTitle(model == nil ? "新建模型" : "编辑模型")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("取消", action: onCancel)
                    .disabled(isSaving)
            }
        }
        .onAppear {
            if let m = model {
                name = m.name
                backendID = m.backendID
                filename = m.filename
                family = m.family
                sizeGB = m.sizeGB
                quant = m.quant
                contextLength = m.contextLength
                minRamGB = m.minRamGB
            } else if let first = backends.first {
                backendID = first.id
            }
        }
    }

    private func save() {
        isSaving = true
        let newModel = AIModel(
            id: model?.id ?? "",
            name: name,
            backendID: backendID,
            backendName: backends.first { $0.id == backendID }?.name,
            filename: filename,
            family: family,
            sizeGB: sizeGB,
            quant: quant,
            contextLength: contextLength,
            minRamGB: minRamGB,
            loaded: model?.loaded ?? false,
            createdAt: model?.createdAt ?? Date(),
            updatedAt: Date()
        )
        onSave(newModel)
    }
}

/// 供 API 客户端使用的模型模型
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
    let loaded: Bool
    let createdAt: Date
    let updatedAt: Date

    var displayName: String {
        let quantStr = quant.isEmpty ? "" : " (\(quant))"
        return "\(name)\(quantStr)"
    }

    enum CodingKeys: String, CodingKey {
        case id, name, backendID = "backend_id", backendName = "backend_name"
        case filename, family, sizeGB = "size_gb", quant
        case contextLength = "context_length", minRamGB = "min_ram_gb"
        case loaded, createdAt = "created_at", updatedAt = "updated_at"
    }
}

extension APIClient {
    func listAIModels() async throws -> [AIModel] {
        let request = try makeRequest("GET", "v1/xijian/models")
        let (data, _) = try await session.data(for: request)
        let response = try JSONDecoder().decode(ModelsResponse.self, from: data)
        return response.data
    }

    func createAIModel(_ model: AIModel) async throws -> AIModel {
        let request = try makeRequest("POST", "v1/xijian/models", body: model)
        let (data, _) = try await session.data(for: request)
        return try JSONDecoder().decode(AIModel.self, from: data)
    }

    func updateAIModel(_ model: AIModel) async throws -> AIModel {
        let request = try makeRequest("PATCH", "v1/xijian/models/\(model.id)", body: model)
        let (data, _) = try await session.data(for: request)
        return try JSONDecoder().decode(AIModel.self, from: data)
    }

    func deleteAIModel(id: String) async throws {
        let request = try makeRequest("DELETE", "v1/xijian/models/\(id)")
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw APIError.httpStatus((response as? HTTPURLResponse)?.statusCode ?? 500, "删除失败")
        }
    }

    func loadAIModel(id: String) async throws {
        let request = try makeRequest("POST", "v1/xijian/models/\(id)/load")
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw APIError.httpStatus((response as? HTTPURLResponse)?.statusCode ?? 500, "加载失败")
        }
    }

    func unloadAIModel(id: String) async throws {
        let request = try makeRequest("POST", "v1/xijian/models/\(id)/unload")
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw APIError.httpStatus((response as? HTTPURLResponse)?.statusCode ?? 500, "��载失败")
        }
    }
}

private struct ModelsResponse: Codable {
    let data: [AIModel]
}
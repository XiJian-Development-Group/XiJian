import SwiftUI
import XiJianKit

/// AI 后端配置行
private struct BackendRow: View {
    let backend: AIBackend
    let isSelected: Bool
    let onSelect: () -> Void
    let onDelete: @MainActor () async -> Void
    let onEdit: () -> Void

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(backend.name)
                        .font(.headline)
                    if backend.isDefault {
                        Text("默认")
                            .font(.caption)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.accentColor.opacity(0.2))
                            .foregroundStyle(Color.accentColor)
                            .clipShape(Capsule())
                    }
                }
                Text("\(backend.type.capitalized) · \(backend.baseURL)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Menu {
                Button("编辑", systemImage: "pencil", action: onEdit)
                Divider()
                Button(role: .destructive) {
                    Task { await onDelete() }
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

/// AI 后端设置页面
struct AIBackendSettingsView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme

    @State private var backends: [AIBackend] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showError = false
    @State private var showAddSheet = false
    @State private var editingBackend: AIBackend?

    var body: some View {
        List {
            Section {
                ForEach(backends) { backend in
                    BackendRow(
                        backend: backend,
                        isSelected: editingBackend?.id == backend.id,
                        onSelect: { editingBackend = backend },
                        onDelete: { Task { await deleteBackend(backend) } },
                        onEdit: { editingBackend = backend }
                    )
                }
                if backends.isEmpty {
                    Text("��无 AI 后端配置")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 20)
                }
            } header: {
                HStack {
                    Text("AI 后端")
                    Spacer()
                    Button {
                        showAddSheet = true
                    } label: {
                        Label("��加后端", systemImage: "plus.circle.fill")
                    }
                }
            }

            if let editing = editingBackend {
                Section("编辑后端：\(editing.name)") {
                    BackendEditForm(backend: editing) { updated in
                        Task { await updateBackend(updated) }
                    } onCancel: {
                        editingBackend = nil
                    }
                }
            }
        }
        .navigationTitle("AI 后端")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showAddSheet = true
                } label: {
                    Label("��加后端", systemImage: "plus")
                }
            }
        }
        .sheet(isPresented: $showAddSheet) {
            NavigationStack {
                BackendEditForm(backend: nil) { new in
                    Task { await addBackend(new) }
                } onCancel: {
                    showAddSheet = false
                }
            }
        }
        .task { await loadBackends() }
        .alert("错误", isPresented: $showError, presenting: errorMessage) { _ in
            Button("好") {}
        } message: { msg in
            Text(msg)
        }
        .refreshable { await loadBackends() }
    }

    private func loadBackends() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let client = try core.makeClientOrThrow()
            backends = try await client.listAIBackends()
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func addBackend(_ backend: AIBackend) async {
        do {
            let client = try core.makeClientOrThrow()
            let created = try await client.createAIBackend(backend)
            backends.append(created)
            showAddSheet = false
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func updateBackend(_ backend: AIBackend) async {
        do {
            let client = try core.makeClientOrThrow()
            let updated = try await client.updateAIBackend(backend)
            if let idx = backends.firstIndex(where: { $0.id == backend.id }) {
                backends[idx] = updated
            }
            editingBackend = nil
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }

    private func deleteBackend(_ backend: AIBackend) async {
        do {
            let client = try core.makeClientOrThrow()
            try await client.deleteAIBackend(id: backend.id)
            backends.removeAll { $0.id == backend.id }
        } catch {
            errorMessage = error.localizedDescription
            showError = true
        }
    }
}

/// 后端编辑/创建表单
private struct BackendEditForm: View {
    let backend: AIBackend?
    let onSave: (AIBackend) -> Void
    let onCancel: () -> Void

    @State private var name: String = ""
    @State private var type: String = "openai_compatible"
    @State private var baseURL: String = ""
    @State private var apiKey: String = ""
    @State private var isDefault: Bool = false
    @State private var isSaving = false

    private let types = ["openai_compatible"]

    var body: some View {
        Form {
            Section("基本信息") {
                TextField("名称", text: $name)
                Picker("类型", selection: $type) {
                    ForEach(types, id: \.self) { Text($0.capitalized) }
                }
                TextField("Base URL (例: https://api.openai.com/v1)", text: $baseURL)
                    .textContentType(.URL)
                    .autocorrectionDisabled()
                SecureField("API Key", text: $apiKey)
            }

            Section {
                Toggle("设为默认后端", isOn: $isDefault)
            }

            Section {
                Button(isSaving ? "保存中…" : (backend == nil ? "创建" : "保存")) {
                    save()
                }
                .disabled(name.isEmpty || baseURL.isEmpty || isSaving)
                .frame(maxWidth: .infinity, alignment: .center)
            }
        }
        .navigationTitle(backend == nil ? "新建后端" : "编辑后端")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("取消", action: onCancel)
                    .disabled(isSaving)
            }
        }
        .onAppear {
            if let b = backend {
                name = b.name
                type = b.type
                baseURL = b.baseURL
                apiKey = b.apiKey
                isDefault = b.isDefault
            }
        }
    }

    private func save() {
        isSaving = true
        let newBackend = AIBackend(
            id: backend?.id ?? "",
            name: name,
            type: type,
            baseURL: baseURL,
            apiKey: apiKey,
            headers: [:],
            isDefault: isDefault,
            createdAt: backend?.createdAt ?? Date(),
            updatedAt: Date()
        )
        onSave(newBackend)
    }
}

/// 供 API 客户端使用的后端模型
struct AIBackend: Identifiable, Codable, Hashable, Sendable {
    let id: String
    let name: String
    let type: String
    let baseURL: String
    let apiKey: String
    let headers: [String: String]
    let isDefault: Bool
    let createdAt: Date
    let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, name, type, baseURL = "base_url", apiKey = "api_key", headers, isDefault = "is_default", createdAt = "created_at", updatedAt = "updated_at"
    }
}

extension APIClient {
    func listAIBackends() async throws -> [AIBackend] {
        let request = try makeRequest("GET", "v1/xijian/backends")
        let (data, _) = try await session.data(for: request)
        let response = try JSONDecoder().decode(BackendsResponse.self, from: data)
        return response.data
    }

    func createAIBackend(_ backend: AIBackend) async throws -> AIBackend {
        let request = try makeRequest("POST", "v1/xijian/backends", body: backend)
        let (data, _) = try await session.data(for: request)
        return try JSONDecoder().decode(AIBackend.self, from: data)
    }

    func updateAIBackend(_ backend: AIBackend) async throws -> AIBackend {
        let request = try makeRequest("PATCH", "v1/xijian/backends/\(backend.id)", body: backend)
        let (data, _) = try await session.data(for: request)
        return try JSONDecoder().decode(AIBackend.self, from: data)
    }

    func deleteAIBackend(id: String) async throws {
        let request = try makeRequest("DELETE", "v1/xijian/backends/\(id)")
        let (_, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw APIError.httpStatus((response as? HTTPURLResponse)?.statusCode ?? 500, "删除失败")
        }
    }
}

private struct BackendsResponse: Codable {
    let data: [AIBackend]
}
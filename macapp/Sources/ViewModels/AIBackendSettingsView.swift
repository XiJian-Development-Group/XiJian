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
                    Text("暂无 AI 后端配置")
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
                        Label("添加后端", systemImage: "plus.circle.fill")
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
                    Label("添加后端", systemImage: "plus")
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

/// 后端编辑表单
private struct BackendEditForm: View {
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var type = "openai"
    @State private var baseURL = ""
    @State private var apiKey = ""
    @State private var isDefault = false
    @State private var isSaving = false

    let backend: AIBackend?
    let onSave: (AIBackend) -> Void
    let onCancel: () -> Void

    init(backend: AIBackend?, onSave: @escaping (AIBackend) -> Void, onCancel: @escaping () -> Void) {
        self.backend = backend
        self.onSave = onSave
        self.onCancel = onCancel

        if let backend {
            _name = State(initialValue: backend.name)
            _type = State(initialValue: backend.type)
            _baseURL = State(initialValue: backend.baseURL)
            _apiKey = State(initialValue: backend.apiKey)
            _isDefault = State(initialValue: backend.isDefault)
        }
    }

    var body: some View {
        Form {
            Section("基本信息") {
                TextField("名称", text: $name)
                Picker("类型", selection: $type) {
                    Text("OpenAI 官方").tag("openai")
                    Text("兼容 OpenAI 协议（自定义端点）").tag("openai_compatible")
                }
                .pickerStyle(.segmented)
                TextField("Base URL", text: $baseURL)
                    .textFieldStyle(.roundedBorder)
                SecureField("API Key", text: $apiKey)
                    .textFieldStyle(.roundedBorder)
            }

            Section("其他") {
                Toggle("设为默认", isOn: $isDefault)
            }

            Section {
                Button("保存") { save() }
                    .disabled(isSaving)
            }
        }
        .padding()
        .frame(minWidth: 400, idealWidth: 500)
    }

    private func save() {
        isSaving = true
        let newBackend = AIBackend(
            id: "",
            name: name,
            type: type,
            baseURL: baseURL,
            apiKey: apiKey,
            headers: [:],
            isDefault: isDefault,
            createdAt: Date(),
            updatedAt: Date()
        )
        onSave(newBackend)
        dismiss()
    }
}

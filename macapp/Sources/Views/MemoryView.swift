import SwiftUI
import XiJianKit

/// 记忆管理：条目列表、搜索、增删改、整理与遗忘
struct MemoryView: View {
    @Bindable var viewModel: MemoryViewModel
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme

    @State private var showCreateSheet = false
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var editingEntry: MemoryEntry?
    @State private var showConsolidateConfirm = false
    @State private var showForgetConfirm = false
    @State private var characters: [CharacterInfo] = []
    @State private var selectedCharacterID: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // 搜索栏
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(.secondary)
                    TextField(loc("搜索记忆（向量检索）"), text: $viewModel.searchText)
                        .textFieldStyle(.plain)
                        .onSubmit {
                            Task { await viewModel.search(characterID: selectedCharacterID) }
                        }
                    if !viewModel.searchText.isEmpty {
                        Button {
                            viewModel.clearSearch()
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                    Button(loc("搜索")) {
                        Task { await viewModel.search(characterID: selectedCharacterID) }
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
                .padding(10)

                // 角色过滤
                if !characters.isEmpty {
                    Picker(loc("按角色过滤"), selection: $selectedCharacterID) {
                        Text(loc("全部角色")).tag(String?.none)
                        ForEach(characters) { character in
                            Text(character.displayName).tag(String?.some(character.id))
                        }
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                    .padding(.horizontal, 10)
                    .padding(.bottom, 6)
                }

                Divider()

                Group {
                    if let results = viewModel.searchResults {
                        resultsList(results)
                    } else if viewModel.isLoading && viewModel.entries.isEmpty {
                        ProgressView(loc("加载记忆中..."))
                    } else if viewModel.entries.isEmpty {
                        emptyState
                    } else {
                        entriesList
                    }
                }
            }
            .navigationTitle(loc("记忆"))
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showCreateSheet = true
                    } label: {
                        Label(loc("新建记忆"), systemImage: "plus")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button(loc("整理记忆（提炼长期记忆）")) {
                            showConsolidateConfirm = true
                        }
                        Button(loc("遗忘过期条目")) {
                            showForgetConfirm = true
                        }
                        Divider()
                        Button(loc("刷新列表")) {
                            Task { await viewModel.refresh(characterID: selectedCharacterID) }
                        }
                    } label: {
                        Label(loc("更多"), systemImage: "ellipsis.circle")
                    }
                }
            }
        }
        .sheet(isPresented: $showCreateSheet) {
            MemoryEditSheet(viewModel: viewModel, characters: characters, mode: .create)
        }
        .sheet(item: $editingEntry) { entry in
            MemoryEditSheet(viewModel: viewModel, characters: characters, mode: .edit(entry))
        }
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .confirmationDialog(loc("确定整理记忆吗？"), isPresented: $showConsolidateConfirm, titleVisibility: .visible) {
            Button(loc("整理")) {
                Task { await viewModel.consolidate(characterID: selectedCharacterID) }
            }
            Button(loc("取消"), role: .cancel) {}
        } message: {
            Text(loc("将短期会话记忆提炼为长期记忆（异步任务）。"))
        }
        .confirmationDialog(loc("确定遗忘过期条目吗？"), isPresented: $showForgetConfirm, titleVisibility: .visible) {
            Button(loc("遗忘")) {
                Task { await viewModel.forget(entryIDs: [], decay: nil) }
            }
            Button(loc("取消"), role: .cancel) {}
        } message: {
            Text(loc("按衰减策略删除过期记忆条目。"))
        }
        .task {
            await viewModel.refresh(characterID: selectedCharacterID)
            if let client = CoreManager.shared.makeClient() {
                characters = (try? await client.listCharacters()) ?? []
            }
        }
        .onChange(of: viewModel.showError) { _, newValue in
            if newValue {
                errorMessage = viewModel.errorMessage ?? loc("未知错误")
                showError = true
                viewModel.showError = false
            }
        }
    }

    // MARK: 列表

    private var entriesList: some View {
        List {
            ForEach(viewModel.entries) { entry in
                MemoryRow(entry: entry) {
                    editingEntry = entry
                }
            }
        }
    }

    private func resultsList(_ results: [MemoryEntry]) -> some View {
        List {
            Section(loc("搜索结果（%lld）", results.count)) {
                ForEach(results) { entry in
                    MemoryRow(entry: entry) {
                        editingEntry = entry
                    }
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: XJSpacing.md) {
            // Apple 风格：毛玻璃圆形容器 + 主题色图标（与 ChatView 空态一致）
            ZStack {
                Circle()
                    .fill(theme.accentColor.opacity(0.12))
                    .frame(width: 88, height: 88)
                Image(systemName: "brain.head.profile")
                    .font(.system(size: 38))
                    .foregroundStyle(theme.accentColor)
            }
            .shadow(color: theme.accentColor.opacity(0.15), radius: 18, y: 8)
            Text(loc("还没有记忆条目"))
                .font(.title2.bold())
                .foregroundStyle(.primary)
            Text(loc("点击右上角 + 新建，或确认 Core 已启动"))
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .xjFadeUp()
    }
}

/// 记忆行
struct MemoryRow: View {
    let entry: MemoryEntry
    var onEdit: () -> Void

    @Environment(ThemeSettings.self) private var theme

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                Text(entry.typeDisplay)
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(Capsule().fill(
                        entry.type == "long" ? Color.orange.opacity(0.2) : Color.blue.opacity(0.2)
                    ))
                    .foregroundStyle(entry.type == "long" ? .orange : .blue)

                if let importance = entry.importance {
                    Text(loc("重要度 %.1f", importance))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                if let source = entry.source, !source.isEmpty {
                    Text(loc("来源：%@", source))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if let created = entry.created_at {
                    Text(created.xijianDate.xijianTimeText)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }

            Text(entry.content)
                .font(.body)
                .lineLimit(3)
                .textSelection(.enabled)

            if !entry.tagList.isEmpty {
                HStack(spacing: 5) {
                    ForEach(entry.tagList.prefix(6), id: \.self) { tag in
                        Text("#\(tag)")
                            .font(.caption2)
                            .foregroundStyle(theme.accentColor)
                    }
                }
            }

            HStack {
                if let characterID = entry.character_id, !characterID.isEmpty {
                    Text(loc("角色：%@", characterID))
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                Spacer()
                Button(loc("编辑")) { onEdit() }
                    .controlSize(.small)
            }
        }
        .padding(.vertical, 4)
    }
}

/// 记忆编辑模式
enum MemoryEditMode {
    case create
    case edit(MemoryEntry)
}

/// 记忆创建/编辑表单
struct MemoryEditSheet: View {
    @Bindable var viewModel: MemoryViewModel
    let characters: [CharacterInfo]
    let mode: MemoryEditMode

    @Environment(\.dismiss) private var dismiss
    @State private var content = ""
    @State private var characterID: String?
    @State private var importance = 0.5
    @State private var decay = "normal"
    @State private var category = "manual"
    @State private var tagsText = ""
    @State private var isSaving = false

    private let decayOptions = ["never", "slow", "normal", "fast"]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(isCreate ? loc("新建记忆") : loc("编辑记忆"))
                .font(.title2)
                .bold()

            Form {
                if !characters.isEmpty {
                    Picker(loc("关联角色"), selection: $characterID) {
                        Text(loc("无")).tag(String?.none)
                        ForEach(characters) { character in
                            Text(character.displayName).tag(String?.some(character.id))
                        }
                    }
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text(loc("内容"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextEditor(text: $content)
                        .frame(minHeight: 90)
                        .padding(4)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color(.textBackgroundColor))
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 8)
                                .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                        )
                }

                HStack {
                    Text(loc("重要度"))
                    Slider(value: $importance, in: 0...1, step: 0.1)
                    Text(String(format: "%.1f", importance))
                        .foregroundStyle(.secondary)
                        .frame(width: 32)
                }

                Picker(loc("衰减策略"), selection: $decay) {
                    ForEach(decayOptions, id: \.self) { Text($0) }
                }

                TextField(loc("来源分类"), text: $category)
                TextField(loc("标签（逗号分隔）"), text: $tagsText)
            }
            .formStyle(.grouped)

            HStack {
                Spacer()
                Button(loc("取消")) { dismiss() }
                Button(isCreate ? loc("创建") : loc("保存")) {
                    Task { await save() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSaving)
            }
        }
        .padding(20)
        .frame(width: 500, height: 520)
        .onAppear {
            if case .edit(let entry) = mode {
                content = entry.content
                characterID = entry.character_id
                importance = entry.importance ?? 0.5
                if let decayScore = entry.decay_score {
                    decay = decayScore >= 0.9 ? "slow" : (decayScore <= 0.4 ? "fast" : "normal")
                }
                category = entry.source ?? "manual"
                tagsText = entry.tagList.joined(separator: ", ")
            }
        }
    }

    private var isCreate: Bool {
        if case .create = mode { return true }
        return false
    }

    private func save() async {
        isSaving = true
        defer { isSaving = false }
        let tags = tagsText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        switch mode {
        case .create:
            await viewModel.create(
                characterID: characterID,
                content: content.trimmingCharacters(in: .whitespacesAndNewlines),
                importance: importance,
                decay: decay,
                category: category.isEmpty ? nil : category,
                tags: tags
            )
        case .edit(let entry):
            var patch: [String: JSONValue] = [
                "content": .string(content),
                "importance": .number(importance),
                "decay": .string(decay),
                "tags": .array(tags.map { .string($0) }),
            ]
            if let characterID { patch["character_id"] = .string(characterID) }
            await viewModel.update(entry.id, patch: patch)
        }
        dismiss()
    }
}

import SwiftUI
import XiJianKit

/// 角色列表：CRUD、加载/卸载、进入详情
struct CharacterListView: View {
    @Bindable var viewModel: CharacterViewModel
    @Environment(CoreManager.self) private var core
    @State private var showCreateSheet = false
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var selectedID: String?
    @State private var showDetail = false

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading && viewModel.characters.isEmpty {
                    ProgressView("加载角色中...")
                } else if viewModel.characters.isEmpty {
                    emptyState
                } else {
                    List {
                        ForEach(viewModel.characters) { character in
                            Button {
                                selectedID = character.id
                                showDetail = true
                            } label: {
                                CharacterRow(character: character) {
                                    Task { await viewModel.toggleLoaded(character.id) }
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .navigationTitle("角色")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showCreateSheet = true
                    } label: {
                        Label("新建角色", systemImage: "plus")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await viewModel.refresh() }
                    } label: {
                        Label("刷新", systemImage: "arrow.clockwise")
                    }
                }
            }
        }
        .sheet(isPresented: $showCreateSheet) {
            CharacterEditSheet(viewModel: viewModel, mode: .create)
        }
        .sheet(isPresented: $showDetail) {
            if let id = selectedID {
                CharacterDetailView(viewModel: viewModel, characterID: id)
            }
        }
        .alert("出错了", isPresented: $showError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .task {
            await viewModel.refresh()
        }
        .onChange(of: viewModel.showError) { _, newValue in
            if newValue {
                errorMessage = viewModel.errorMessage ?? "未知错误"
                showError = true
                viewModel.showError = false
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "person.crop.circle.badge.plus")
                .font(.system(size: 44))
                .foregroundStyle(.tertiary)
            Text("还没有角色")
                .font(.title3)
                .foregroundStyle(.secondary)
            Text("点击右上角 + 新建角色，或确认 Core 已启动")
                .font(.caption)
                .foregroundStyle(.tertiary)
            if !coreIsRunning {
                Button("启动 Core") {
                    Task { await core.startCore() }
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var coreIsRunning: Bool {
        if case .running = core.state { return true }
        return false
    }
}

/// 角色行
struct CharacterRow: View {
    let character: CharacterInfo
    var onToggleLoaded: () -> Void

    @Environment(ThemeSettings.self) private var theme

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(theme.accentColor.opacity(0.18))
                    .frame(width: 38, height: 38)
                Text(String(character.displayName.prefix(1)))
                    .font(.headline)
                    .foregroundStyle(theme.accentColor)
            }

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(character.displayName)
                        .font(.headline)
                    if character.isLoaded {
                        Text("已加载")
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(Color.green.opacity(0.2)))
                            .foregroundStyle(.green)
                    }
                }
                if !character.tagList.isEmpty {
                    Text(character.tagList.prefix(4).joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }

            Spacer()

            Button(action: onToggleLoaded) {
                Image(systemName: character.isLoaded ? "eject.circle.fill" : "play.circle.fill")
                    .font(.system(size: 20))
                    .foregroundStyle(character.isLoaded ? Color.orange : Color.green)
            }
            .buttonStyle(.plain)
            .help(character.isLoaded ? "卸载角色" : "加载角色")
        }
        .padding(.vertical, 4)
    }
}

/// 角色编辑模式
enum CharacterEditMode {
    case create
    case edit(CharacterInfo)

    var isCreate: Bool {
        if case .create = self { return true }
        return false
    }
}

/// 角色创建/编辑表单
struct CharacterEditSheet: View {
    @Bindable var viewModel: CharacterViewModel
    let mode: CharacterEditMode

    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var displayName = ""
    @State private var personaDoc = ""
    @State private var voiceProfile = ""
    @State private var defaultEmotion = "neutral"
    @State private var tagsText = ""
    @State private var isSaving = false

    private let emotions = ["neutral", "happy", "sad", "angry", "surprised", "calm"]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(mode.isCreate ? "新建角色" : "编辑角色")
                .font(.title2)
                .bold()

            Form {
                TextField("名称（必填）", text: $name)
                TextField("显示名（可选）", text: $displayName)
                TextField("语音档案（可选）", text: $voiceProfile)

                Picker("默认情绪", selection: $defaultEmotion) {
                    ForEach(emotions, id: \.self) { Text($0) }
                }

                TextField("标签（逗号分隔）", text: $tagsText)

                VStack(alignment: .leading, spacing: 4) {
                    Text("人设文档")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextEditor(text: $personaDoc)
                        .font(.body)
                        .frame(minHeight: 140)
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
            }
            .formStyle(.grouped)

            HStack {
                Spacer()
                Button("取消") { dismiss() }
                Button(mode.isCreate ? "创建" : "保存") {
                    Task { await save() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSaving)
            }
        }
        .padding(20)
        .frame(width: 520, height: 560)
        .onAppear {
            if case .edit(let character) = mode {
                name = character.name ?? ""
                displayName = character.display_name ?? ""
                personaDoc = character.persona_doc ?? ""
                voiceProfile = character.voice_profile ?? ""
                defaultEmotion = character.default_emotion ?? "neutral"
                tagsText = character.tagList.joined(separator: ", ")
            }
        }
    }

    private func save() async {
        isSaving = true
        defer { isSaving = false }
        let tags = tagsText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        switch mode {
        case .create:
            await viewModel.create(
                name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                displayName: displayName.isEmpty ? nil : displayName,
                personaDoc: personaDoc,
                voiceProfile: voiceProfile.isEmpty ? nil : voiceProfile,
                defaultEmotion: defaultEmotion,
                tags: tags
            )
        case .edit(let character):
            var patch: [String: JSONValue] = [
                "name": .string(name),
                "persona_doc": .string(personaDoc),
                "default_emotion": .string(defaultEmotion),
                "tags": .array(tags.map { .string($0) }),
            ]
            if !displayName.isEmpty { patch["display_name"] = .string(displayName) }
            if !voiceProfile.isEmpty { patch["voice_profile"] = .string(voiceProfile) }
            await viewModel.update(character.id, patch: patch)
        }
        dismiss()
    }
}

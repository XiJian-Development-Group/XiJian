import SwiftUI
import XiJianKit

/// 角色列表：CRUD、加载/卸载、进入详情、导入资源包
struct CharacterListView: View {
    @Bindable var viewModel: CharacterViewModel
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var selectedID: String?
    @State private var showDetail = false
    @State private var showImportSheet = false

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading && viewModel.characters.isEmpty {
                    ProgressView(loc("加载角色中..."))
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
            .navigationTitle(loc("角色"))
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showImportSheet = true
                    } label: {
                        Label(loc("导入角色"), systemImage: "square.and.arrow.down")
                    }
                    .disabled(!coreIsRunning)
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await viewModel.refresh() }
                    } label: {
                        Label(loc("刷新"), systemImage: "arrow.clockwise")
                    }
                }
            }
        }
        .sheet(isPresented: $showImportSheet) {
            ImportPackSheet() {
                await viewModel.refresh()
            }
        }
        .sheet(isPresented: $showDetail) {
            if let id = selectedID {
                CharacterDetailView(viewModel: viewModel, characterID: id)
            }
        }
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .task {
            await viewModel.refresh()
        }
        .onChange(of: viewModel.showError) { _, newValue in
            if newValue {
                errorMessage = viewModel.errorMessage ?? loc("未知错误")
                showError = true
                viewModel.showError = false
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
                Image(systemName: "person.crop.circle.badge.plus")
                    .font(.system(size: 38))
                    .foregroundStyle(theme.accentColor)
            }
            .shadow(color: theme.accentColor.opacity(0.15), radius: 18, y: 8)
            Text(loc("还没有角色"))
                .font(.title2.bold())
                .foregroundStyle(.primary)
            Text(loc("点击右上角「导入角色」选择资源包（.7z/.zip），或确认 Core 已启动"))
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            if !coreIsRunning {
                Button(loc("启动 Core")) {
                    Task { await core.startCore() }
                }
                .xjPrimaryButton(prominent: true)
                .padding(.top, XJSpacing.xs)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .xjFadeUp()
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
    @State private var isHovering = false

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
                        Text(loc("已加载"))
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(Color.green.opacity(0.2)))
                            .foregroundStyle(.green)
                    }
                    if character.isFromPack {
                        Text(loc("资源包"))
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(Color.purple.opacity(0.2)))
                            .foregroundStyle(.purple)
                            .help(loc("来自资源包：%@", character.packID ?? ""))
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
            .help(character.isLoaded ? loc("卸载角色") : loc("加载角色"))
        }
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: XJRadius.small, style: .continuous)
                .fill(isHovering ? Color.primary.opacity(0.05) : Color.clear)
        )
        .onHover { hovering in
            withAnimation(.spring(response: 0.4, dampingFraction: 1.0)) {
                isHovering = hovering
            }
        }
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
            Text(mode.isCreate ? loc("新建角色") : loc("编辑角色"))
                .font(.title2)
                .bold()

            Form {
                TextField(loc("名称（必填）"), text: $name)
                TextField(loc("显示名（可选）"), text: $displayName)
                TextField(loc("语音档案（可选）"), text: $voiceProfile)

                Picker(loc("默认情绪"), selection: $defaultEmotion) {
                    ForEach(emotions, id: \.self) { Text($0) }
                }

                TextField(loc("标签（逗号分隔）"), text: $tagsText)

                VStack(alignment: .leading, spacing: 4) {
                    Text(loc("人设文档"))
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
                Button(loc("取消")) { dismiss() }
                Button(mode.isCreate ? loc("创建") : loc("保存")) {
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

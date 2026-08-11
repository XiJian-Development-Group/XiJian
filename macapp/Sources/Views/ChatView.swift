import SwiftUI
import XiJianKit

/// 对话界面：模型选择、参数调节、消息流、输入栏
struct ChatView: View {
    @Bindable var viewModel: ChatViewModel
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @State private var inputText = ""
    @State private var showModelPicker = false
    @State private var showError = false
    @State private var errorMessage = ""
    // A6 实时通话入口
    @State private var showCallPicker = false
    @State private var showCallSheet = false
    @State private var voiceCallVM: VoiceCallViewModel?

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            messagesList
            if viewModel.isStreaming {
                streamingBar
            }
            Divider()
            ChatInputBar(
                text: $inputText,
                isStreaming: viewModel.isStreaming,
                onSend: { Task { await send() } },
                onStop: { viewModel.stopStreaming() }
            )
        }
        .background(theme.appearanceMode == .dark ? Color.black.opacity(0.2) : Color(nsColor: .windowBackgroundColor))
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .onChange(of: viewModel.showError) { _, newValue in
            if newValue {
                errorMessage = viewModel.errorMessage ?? loc("未知错误")
                showError = true
                viewModel.showError = false
            }
        }
        .sheet(isPresented: $showCallSheet) {
            if let vm = voiceCallVM {
                VoiceCallView(viewModel: vm)
            }
        }
    }

    private var coreIsRunning: Bool {
        if case .running = core.state { return true }
        return false
    }

    // MARK: 顶部栏

    private var header: some View {
        HStack(spacing: 10) {
            // 模型选择
            Menu {
                if viewModel.models.isEmpty {
                    Text(loc("暂无可用模型"))
                }
                ForEach(viewModel.models) { model in
                    Button {
                        viewModel.selectedModelID = model.id
                    } label: {
                        if viewModel.selectedModelID == model.id {
                            Label(model.displayName, systemImage: "checkmark")
                        } else {
                            Text(model.displayName)
                        }
                    }
                }
                Divider()
                Button(loc("刷新模型列表")) {
                    Task { await viewModel.loadModels() }
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "cpu")
                    Text(viewModel.selectedModel ?? loc("选择模型"))
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.caption2)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Color(.controlBackgroundColor))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                )
            }
            .menuStyle(.borderlessButton)
            .fixedSize()

            if let model = viewModel.models.first(where: { $0.id == viewModel.selectedModelID }),
               let xijian = model.xijian {
                Text(loc("%@ · %@ · %@", xijian.backend ?? loc("未知后端"), xijian.quant ?? "", xijian.context_length.map { "\($0) ctx" } ?? ""))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            // A6 实时通话入口（拨出：选角色 → 建通话 → 响铃 → 接通）
            Button {
                showCallPicker.toggle()
            } label: {
                Label(loc("通话"), systemImage: "phone")
            }
            .disabled(!coreIsRunning)
            .help(coreIsRunning ? loc("与角色实时语音通话") : loc("Core 未运行，无法通话"))
            .popover(isPresented: $showCallPicker) {
                VoiceCallCharacterPicker { character in
                    let vm = VoiceCallViewModel()
                    voiceCallVM = vm
                    showCallPicker = false
                    showCallSheet = true
                    Task {
                        await vm.startCall(characterId: character.id, characterName: character.displayName)
                    }
                }
            }

            // 会话操作
            Button {
                Task { await viewModel.newSession() }
            } label: {
                Label(loc("新会话"), systemImage: "plus.bubble")
            }
            .help(loc("新建会话"))

            Button {
                Task { await viewModel.clearChat() }
            } label: {
                Label(loc("清空"), systemImage: "trash")
            }
            .help(loc("清空当前会话"))

            // 参数弹窗
            Button {
                showModelPicker.toggle()
            } label: {
                Image(systemName: "slider.horizontal.3")
            }
            .help(loc("聊天参数"))
            .popover(isPresented: $showModelPicker) {
                ChatParameterPanel(viewModel: viewModel)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        // Apple 风格：毛玻璃工具栏（内容在下方滚动时露出材质层次）
        .background(.bar)
    }

    // MARK: 消息列表

    private var messagesList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 0) {
                    if viewModel.messages.isEmpty {
                        emptyState
                    }
                    ForEach(Array(viewModel.messages.enumerated()), id: \.element.id) { index, message in
                        MessageBubbleView(
                            message: message,
                            isStreaming: viewModel.isStreaming && index == viewModel.messages.count - 1
                        )
                        .id(message.id)
                    }
                }
                .padding(.vertical, 8)
            }
            .onChange(of: viewModel.messages.count) {
                if let last = viewModel.messages.last, let lastID = last.id {
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(lastID, anchor: .bottom)
                    }
                }
            }
            .onChange(of: viewModel.messages.last?.content) {
                if let last = viewModel.messages.last, let lastID = last.id {
                    proxy.scrollTo(lastID, anchor: .bottom)
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            // Apple 风格：毛玻璃圆形容器 + 主题色图标
            ZStack {
                Circle()
                    .fill(theme.accentColor.opacity(0.12))
                    .frame(width: 88, height: 88)
                Image(systemName: "bubble.left.and.bubble.right")
                    .font(.system(size: 38))
                    .foregroundStyle(theme.accentColor)
            }
            .shadow(color: theme.accentColor.opacity(0.15), radius: 18, y: 8)
            Text(loc("开始与隙间对话"))
                .font(.title2.bold())
                .foregroundStyle(.primary)
            Text(loc("在下方输入消息，或先选择一个模型"))
                .font(.body)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.top, 80)
        .xjFadeUp()
    }

    private var streamingBar: some View {
        HStack(spacing: 8) {
            ProgressView()
                .controlSize(.small)
            Text(loc("正在生成..."))
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            Button(loc("停止生成")) {
                viewModel.stopStreaming()
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(.bar)
    }

    // MARK: 发送

    private func send() async {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        inputText = ""
        await viewModel.send(text: text)
    }
}

/// 聊天参数面板
struct ChatParameterPanel: View {
    @Bindable var viewModel: ChatViewModel
    @Environment(ThemeSettings.self) private var theme
    @State private var appVM = AppViewModel.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(loc("聊天参数"), systemImage: "slider.horizontal.3")
                .font(.headline)

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(loc("温度"))
                    Spacer()
                    Text(String(format: "%.2f", appVM.temperature))
                        .foregroundStyle(.secondary)
                }
                Slider(value: Bindable(appVM).temperature, in: 0...2, step: 0.05)
            }

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(loc("最大 Token"))
                    Spacer()
                    Text("\(appVM.maxTokens)")
                        .foregroundStyle(.secondary)
                }
                Slider(value: Binding(
                    get: { Double(appVM.maxTokens) },
                    set: { appVM.maxTokens = Int($0) }
                ), in: 64...8192, step: 64)
            }

            Toggle(loc("启用记忆召回"), isOn: Bindable(appVM).recallEnabled)
            Toggle(loc("显示时间戳"), isOn: Bindable(theme).showTimestamps)

            Divider()

            Text(loc("角色与世界（注入聊天请求）"))
                .font(.caption)
                .foregroundStyle(.secondary)

            CharacterWorldPicker(characterID: Bindable(appVM).selectedCharacterID, worldID: Bindable(appVM).selectedWorldID)
        }
        .padding(16)
        .frame(width: 320)
    }
}

/// 角色/世界选择器（复用：聊天参数、角色详情）
struct CharacterWorldPicker: View {
    @Binding var characterID: String?
    @Binding var worldID: String?

    @State private var characters: [CharacterInfo] = []
    @State private var worlds: [WorldInfo] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Picker(loc("角色"), selection: $characterID) {
                Text(loc("无")).tag(String?.none)
                ForEach(characters) { character in
                    Text(character.displayName).tag(String?.some(character.id))
                }
            }
            .onAppear { Task { await load() } }

            Picker(loc("世界"), selection: $worldID) {
                Text(loc("无")).tag(String?.none)
                ForEach(worlds) { world in
                    Text(world.name ?? world.worldID).tag(String?.some(world.worldID))
                }
            }
        }
        .labelsHidden()
        .frame(maxWidth: .infinity)
    }

    private func load() async {
        let core = CoreManager.shared
        guard let client = core.makeClient() else { return }
        if characters.isEmpty {
            characters = (try? await client.listCharacters()) ?? []
        }
        if worlds.isEmpty {
            worlds = (try? await client.listWorlds()) ?? []
        }
    }
}

import SwiftUI
import XiJianKit

/// A6 实时通话界面 — 拨出/接听/挂断控制、文本输入发送、对话记录、barge-in 开关、歌唱输入。
///
/// 样式与现有 macapp 主题一致（ThemeSettings：主题色 / 气泡 / 圆角 / 字号 / 深浅色）。
/// 通话阶段驱动：`VoiceCallPhase`（idle/ringing/active/ended）。
struct VoiceCallView: View {
    @ObservedObject var viewModel: VoiceCallViewModel
    @Environment(ThemeSettings.self) private var theme
    @Environment(\.dismiss) private var dismiss

    @State private var inputText = ""
    @State private var showSongSheet = false
    @State private var songLyrics = ""
    @State private var showError = false
    @State private var errorMessage = ""

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            transcriptList
            Divider()
            controlBar
        }
        .frame(width: 440, height: 580)
        .background(theme.appearanceMode == .dark ? Color.black.opacity(0.2) : Color(nsColor: .windowBackgroundColor))
        .sheet(isPresented: $showSongSheet) {
            songSheet
        }
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
        .onDisappear {
            viewModel.close()
        }
    }

    // MARK: 顶部栏

    private var header: some View {
        HStack(spacing: 12) {
            // 角色头像
            ZStack {
                Circle()
                    .fill(theme.accentColor.opacity(0.18))
                    .frame(width: 44, height: 44)
                Text(String(viewModel.characterName?.prefix(1) ?? "?"))
                    .font(.title3)
                    .foregroundStyle(theme.accentColor)
            }

            VStack(alignment: .leading, spacing: 3) {
                Text(viewModel.characterName ?? loc("通话"))
                    .font(.headline)
                HStack(spacing: 6) {
                    phaseDot
                    Text(phaseText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if viewModel.isWSConnected {
                        Text(loc("· WS 已连接"))
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
            }

            Spacer()

            if viewModel.phase == .active {
                TimelineView(.periodic(from: .now, by: 1.0)) { context in
                    Text(elapsedText(at: context.date))
                        .font(.system(.title3, design: .monospaced))
                        .foregroundStyle(theme.accentColor)
                        .monospacedDigit()
                }
            } else if viewModel.phase == .ringing {
                Image(systemName: "phone.badge.waveform")
                    .font(.system(size: 24))
                    .foregroundStyle(theme.accentColor)
                    .symbolEffect(.pulse, options: .repeating)
            }
        }
        .padding(.horizontal, XJSpacing.md)
        .padding(.vertical, 12)
        // Apple 风格：毛玻璃工具栏（与 ChatView 顶部栏一致）
        .background(.bar)
    }

    private var phaseDot: some View {
        Circle()
            .fill(phaseColor)
            .frame(width: 8, height: 8)
    }

    private var phaseColor: Color {
        switch viewModel.phase {
        case .idle: return .gray
        case .ringing: return .orange
        case .active: return .green
        case .ended: return .secondary
        }
    }

    private var phaseText: String {
        switch viewModel.phase {
        case .ringing:
            return viewModel.direction == .characterInitiated ? loc("来电响铃…") : loc("等待接通…")
        default:
            return viewModel.phase.displayName
        }
    }

    private func elapsedText(at now: Date) -> String {
        guard let startedAt = viewModel.startedAt else { return "00:00" }
        let elapsed = max(0, Int(now.timeIntervalSince1970 - startedAt))
        let minutes = elapsed / 60
        let seconds = elapsed % 60
        return String(format: "%02d:%02d", minutes, seconds)
    }

    // MARK: 对话记录

    private var transcriptList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 8) {
                    if viewModel.transcript.isEmpty {
                        emptyTranscript
                    }
                    ForEach(viewModel.transcript) { item in
                        transcriptRow(item)
                            .id(item.id)
                    }
                }
                .padding(12)
            }
            .onChange(of: viewModel.transcript.count) {
                if let last = viewModel.transcript.last {
                    withAnimation(.easeOut(duration: 0.2)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    private var emptyTranscript: some View {
        VStack(spacing: XJSpacing.md) {
            // Apple 风格：毛玻璃圆形容器 + 主题色图标（与 ChatView 空态一致）
            ZStack {
                Circle()
                    .fill(theme.accentColor.opacity(0.12))
                    .frame(width: 72, height: 72)
                Image(systemName: "waveform")
                    .font(.system(size: 30))
                    .foregroundStyle(theme.accentColor)
            }
            .shadow(color: theme.accentColor.opacity(0.15), radius: 14, y: 6)
            Text(loc("通话接通后即可开始对话"))
                .font(.body)
                .foregroundStyle(.secondary)
            if viewModel.phase == .ringing {
                Text(loc("对方接通前，你可以先准备要说的话"))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 80)
        .xjFadeUp()
    }

    @ViewBuilder
    private func transcriptRow(_ item: VoiceCallTranscriptItem) -> some View {
        if item.isSystem {
            HStack(spacing: 6) {
                Image(systemName: "info.circle")
                    .font(.caption2)
                Text(item.text)
                    .font(.caption)
            }
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity)
        } else {
            HStack(alignment: .bottom, spacing: 8) {
                if item.isUser { Spacer(minLength: 60) }
                VStack(alignment: item.isUser ? .trailing : .leading, spacing: 3) {
                    Text(item.text)
                        .font(.system(size: theme.fontSize))
                        .foregroundStyle(item.isUser ? theme.userTextColor : .primary)
                        .textSelection(.enabled)
                        .multilineTextAlignment(item.isUser ? .trailing : .leading)
                    if let meta = item.meta {
                        Text(meta)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: theme.cornerRadius, style: .continuous)
                        .fill(item.isUser ? theme.userBubbleColor : theme.assistantBubbleColor)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: theme.cornerRadius, style: .continuous)
                        .strokeBorder(
                            item.isUser ? theme.accentColor.opacity(0.6) : Color.secondary.opacity(0.2),
                            lineWidth: theme.bubbleStyle == .outlined ? 1 : 0
                        )
                )
                if !item.isUser { Spacer(minLength: 60) }
            }
        }
    }

    // MARK: 控制区

    @ViewBuilder
    private var controlBar: some View {
        Group {
            switch viewModel.phase {
        case .idle:
            HStack {
                Spacer()
                Button(loc("关闭")) { dismiss() }
                    .buttonStyle(.borderedProminent)
                Spacer()
            }
            .padding(12)
        case .ringing:
            HStack(spacing: 16) {
                if viewModel.direction == .characterInitiated {
                    Button {
                        Task { await viewModel.accept() }
                    } label: {
                        Label(loc("接听"), systemImage: "phone.fill")
                            .frame(width: 100)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.green)
                    .disabled(viewModel.isBusy)
                }
                Button(role: .destructive) {
                    Task { await viewModel.reject() }
                } label: {
                    Label(loc("挂断"), systemImage: "phone.down.fill")
                        .frame(width: 100)
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .disabled(viewModel.isBusy)
            }
            .padding(12)
        case .active:
            VStack(spacing: 8) {
                HStack(spacing: 10) {
                    Toggle(isOn: bargeInBinding) {
                        Label(loc("打断"), systemImage: "bolt.fill")
                            .font(.caption)
                    }
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .disabled(viewModel.isBusy)
                    .help(loc("barge-in：新语音输入到达时中断当前 TTS 播放"))

                    Button {
                        showSongSheet = true
                    } label: {
                        Label(loc("唱歌"), systemImage: "music.note")
                            .font(.caption)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(viewModel.isBusy)

                    Spacer()

                    Button(role: .destructive) {
                        Task { await viewModel.end() }
                    } label: {
                        Label(loc("挂断"), systemImage: "phone.down.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                    .disabled(viewModel.isBusy)
                }

                HStack(alignment: .bottom, spacing: 8) {
                    TextField(loc("说点什么…"), text: $inputText)
                        .textFieldStyle(.plain)
                        .font(.system(size: max(theme.fontSize, 13)))
                        .padding(8)
                        .background(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(Color(.textBackgroundColor))
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
                        )
                        .onSubmit {
                            Task { await send() }
                        }
                        .disabled(viewModel.isBusy || viewModel.isRecording)

                    // 麦克风按钮：点击开始录音，再点停止并发送（录音中变红）
                    Button {
                        if viewModel.isRecording {
                            Task { await viewModel.stopRecordingAndSend() }
                        } else {
                            viewModel.startRecording()
                        }
                    } label: {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 24))
                            .foregroundStyle(viewModel.isRecording ? Color.red : theme.accentColor)
                            .symbolEffect(.pulse, options: .repeating, isActive: viewModel.isRecording)
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.isBusy)
                    .help(viewModel.isRecording ? loc("停止并发送") : loc("开始录音"))

                    Button {
                        Task { await send() }
                    } label: {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 26))
                            .foregroundStyle(theme.accentColor)
                    }
                    .buttonStyle(.plain)
                    .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isBusy || viewModel.isRecording)
                    .help(loc("发送"))
                }

                // 录音 / 播放状态指示
                HStack(spacing: 12) {
                    if viewModel.isRecording {
                        HStack(spacing: 6) {
                            Circle()
                                .fill(Color.red)
                                .frame(width: 8, height: 8)
                            Text(loc("录音中…"))
                                .font(.caption)
                        }
                        .foregroundStyle(Color.red)
                    }
                    if viewModel.isPlayingAudio {
                        HStack(spacing: 6) {
                            Image(systemName: "speaker.wave.2.fill")
                                .font(.caption2)
                            Text(loc("播放中…"))
                                .font(.caption)
                        }
                        .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(.horizontal, 2)
            }
            .padding(12)
        case .ended:
            HStack(spacing: 16) {
                Button(loc("重新拨打")) {
                    if let id = viewModel.characterID {
                        let name = viewModel.characterName
                        Task {
                            await viewModel.startCall(characterId: id, characterName: name)
                        }
                    }
                }
                .buttonStyle(.borderedProminent)
                Button(loc("关闭")) { dismiss() }
                .buttonStyle(.bordered)
            }
            .padding(12)
        }
        }
        // Apple 风格：底部控制区毛玻璃（与顶部栏材质一致）
        .background(.bar)
    }

    private var bargeInBinding: Binding<Bool> {
        Binding(
            get: { viewModel.bargeInActive },
            set: { _ in
                Task { await viewModel.toggleBargeIn() }
            }
        )
    }

    private func send() async {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        inputText = ""
        await viewModel.sendText(text)
    }

    // MARK: 歌唱输入

    private var songSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(loc("让角色唱首歌"), systemImage: "music.note")
                .font(.headline)

            Text(loc("输入歌词，角色将以歌声回应（DiffSinger 引擎接入前返回不可用提示）"))
                .font(.caption)
                .foregroundStyle(.secondary)

            TextEditor(text: $songLyrics)
                .font(.body)
                .frame(minHeight: 120)
                .padding(4)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color(.textBackgroundColor))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color.secondary.opacity(0.3), lineWidth: 1)
                )

            HStack {
                Spacer()
                Button(loc("取消")) { showSongSheet = false }
                Button(loc("开唱")) {
                    let lyrics = songLyrics
                    songLyrics = ""
                    showSongSheet = false
                    Task { await viewModel.sing(lyrics: lyrics) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(songLyrics.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 380, height: 280)
    }
}

// MARK: - 通话角色选择器（入口弹层）

/// 通话入口的角色选择器：列出角色，选择后回调 `onPick`（由接入点创建
/// `VoiceCallViewModel` 并 `startCall`）。
struct VoiceCallCharacterPicker: View {
    var onPick: (CharacterInfo) -> Void

    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @State private var characters: [CharacterInfo] = []
    @State private var isLoading = false
    @State private var loadFailed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(loc("选择要通话的角色"))
                .font(.headline)

            if isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if characters.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "person.crop.circle.badge.questionmark")
                        .font(.system(size: 28))
                        .foregroundStyle(.tertiary)
                    Text(loadFailed ? loc("角色加载失败，请确认 Core 已启动") : loc("暂无角色，请先在角色页导入资源包"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                    if loadFailed {
                        Button(loc("重试")) {
                            Task { await load() }
                        }
                        .controlSize(.small)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 4) {
                        ForEach(characters) { character in
                            Button {
                                onPick(character)
                            } label: {
                                HStack(spacing: 10) {
                                    ZStack {
                                        Circle()
                                            .fill(theme.accentColor.opacity(0.18))
                                            .frame(width: 30, height: 30)
                                        Text(String(character.displayName.prefix(1)))
                                            .font(.caption)
                                            .foregroundStyle(theme.accentColor)
                                    }
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(character.displayName)
                                            .font(.body)
                                            .foregroundStyle(.primary)
                                        if character.isLoaded {
                                            Text(loc("已加载"))
                                                .font(.caption2)
                                                .foregroundStyle(.green)
                                        }
                                    }
                                    Spacer()
                                    Image(systemName: "phone.circle.fill")
                                        .font(.system(size: 20))
                                        .foregroundStyle(theme.accentColor)
                                }
                                .padding(.vertical, 5)
                                .padding(.horizontal, 6)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
        }
        .padding(14)
        .frame(width: 280, height: 320)
        .task {
            await load()
        }
    }

    private func load() async {
        guard let client = core.makeClient() else {
            loadFailed = true
            characters = []
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            characters = try await client.listCharacters()
            loadFailed = false
        } catch {
            loadFailed = true
            characters = []
        }
    }
}

import SwiftUI
import XiJianKit

/// 设置：Core 服务、主题个性化、安全、备份、剧情
struct SettingsView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme

    @State private var showResetConfirm = false
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var showLogViewer = false

    var body: some View {
        NavigationStack {
            List {
                Section("Core 服务") {
                    NavigationLink {
                        ServerSettingsSection()
                    } label: {
                        Label {
                            Text("服务器与进程")
                        } icon: {
                            Image(systemName: "server.rack")
                                .foregroundStyle(theme.accentColor)
                        }
                    }
                    NavigationLink {
                        LogViewerSection()
                    } label: {
                        Label("查看 Core 日志", systemImage: "doc.text.magnifyingglass")
                            .foregroundStyle(theme.accentColor)
                    }
                }

                Section("个性化") {
                    NavigationLink {
                        ThemeSettingsSection()
                    } label: {
                        Label {
                            Text("主题与外观")
                        } icon: {
                            Image(systemName: "paintpalette")
                                .foregroundStyle(theme.accentColor)
                        }
                    }
                }

                Section("数据管理") {
                    NavigationLink {
                        BackupSettingsView()
                    } label: {
                        Label("备份与受保护模块", systemImage: "externaldrive.badge.timemachine")
                            .foregroundStyle(theme.accentColor)
                    }
                    Button(role: .destructive) {
                        showResetConfirm = true
                    } label: {
                        Label("重置 Core 数据", systemImage: "trash")
                    }
                }

                Section("安全与剧情") {
                    NavigationLink {
                        SafetySettingsView()
                    } label: {
                        Label("安全模块", systemImage: "shield.checkered")
                            .foregroundStyle(theme.accentColor)
                    }
                    NavigationLink {
                        PlotSettingsView()
                    } label: {
                        Label("剧情系统", systemImage: "film.stack")
                            .foregroundStyle(theme.accentColor)
                    }
                }

                Section("关于") {
                    LabeledContent("版本", value: "1.0.0")
                    LabeledContent("协议", value: "本地 API · Bearer Token")
                }
            }
            .navigationTitle("设置")
        }
        .confirmationDialog("重置 Core 数据", isPresented: $showResetConfirm, titleVisibility: .visible) {
            Button("删除 Core 数据", role: .destructive) {
                Task { await core.resetCoreData() }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("将停止 Core 并删除 Core 数据目录（仅 ~/Library/Application Support/XiJian/Core，含复制出的 Core 程序、日志与数据）。不会影响 XiJian 目录下的其他应用数据。重新启动时会自动从 App 内置资源重新复制。")
        }
        .alert("出错了", isPresented: $showError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
    }
}

// MARK: - 服务器与进程

/// Core 服务器设置：状态、端口、自定义服务器、启动/停止/重启
struct ServerSettingsSection: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @State private var portText = ""
    @State private var customURLText = ""
    @State private var customTokenText = ""
    @State private var useCustom = false
    @State private var isWorking = false
    @State private var copiedToken = false

    var body: some View {
        Form {
            Section("运行状态") {
                HStack {
                    StatusIndicatorView()
                }
                LabeledContent("PID", value: core.pid.map(String.init) ?? "—")
                LabeledContent("Token", value: displayedToken)

                if case .error(let message) = core.state {
                    Text(message)
                        .font(.caption)
                        .foregroundStyle(.red)
                }

                HStack(spacing: 10) {
                    Button {
                        Task { await core.startCore() }
                    } label: {
                        Label("启动", systemImage: "play.fill")
                    }
                    .disabled(isRunningOrBusy)

                    Button {
                        Task { await core.stopCore() }
                    } label: {
                        Label("停止", systemImage: "stop.fill")
                    }
                    .disabled(!isRunningOrBusy)

                    Button {
                        Task { await core.restartCore() }
                    } label: {
                        Label("重启", systemImage: "arrow.clockwise")
                    }
                    .disabled(isWorking)
                }
                .disabled(isWorking)
            }

            Section("连接设置") {
                Toggle("使用自定义服务器", isOn: $useCustom)
                    .onChange(of: useCustom) { _, newValue in
                        core.useCustomServer = newValue
                        if !newValue { useCustom = false }
                    }
                    .onAppear {
                        useCustom = core.useCustomServer
                    }

                if !useCustom {
                    HStack {
                        Text("端口")
                        TextField("18500", text: $portText)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 120)
                            .onAppear { portText = String(core.port) }
                            .onChange(of: portText) { _, newValue in
                                if let value = Int(newValue), (1...65535).contains(value) {
                                    core.port = value
                                }
                            }
                    }
                    Text("默认 18500，修改后需重启 Core 生效。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    TextField("http://127.0.0.1:18500", text: $customURLText)
                        .textFieldStyle(.roundedBorder)
                        .onAppear { customURLText = core.customBaseURL }
                        .onChange(of: customURLText) { _, newValue in
                            core.customBaseURL = newValue
                        }
                    TextField("访问令牌（可选）", text: $customTokenText)
                        .textFieldStyle(.roundedBorder)
                        .onAppear { customTokenText = core.customToken }
                        .onChange(of: customTokenText) { _, newValue in
                            core.customToken = newValue
                        }
                    Text("使用外部地址时，App 将不再管理本机 Core 进程；若服务器要求鉴权，请在此填写访问令牌。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("工具") {
                Button("打开日志目录") {
                    core.openLogDirectory()
                }
                Button("复制 Token") {
                    let value = core.useCustomServer ? core.customToken : (core.token ?? "")
                    guard !value.isEmpty else { return }
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(value, forType: .string)
                    copiedToken = true
                }
                .disabled(core.useCustomServer ? core.customToken.isEmpty : core.token == nil)

                if copiedToken {
                    Text("已复制到剪贴板")
                        .font(.caption)
                        .foregroundStyle(.green)
                }

                LabeledContent("Core 目录", value: core.coreDirectory?.path ?? "—")
                    .font(.caption)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("服务器与进程")
    }

    private var isRunningOrBusy: Bool {
        switch core.state {
        case .running, .starting, .extracting: return true
        default: return false
        }
    }

    /// 展示用 Token（自定义服务器模式显示 customToken，本机模式显示读取到的 token）
    private var displayedToken: String {
        if core.useCustomServer {
            return core.customToken.isEmpty ? "—" : String(core.customToken.prefix(8)) + "…"
        }
        return core.token.map { String($0.prefix(8)) + "…" } ?? "—"
    }
}

// MARK: - 日志查看器

/// 进程输出日志查看（最近 1000 行）
struct LogViewerSection: View {
    @Environment(CoreManager.self) private var core

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("最近日志")
                    .font(.headline)
                Spacer()
                Button("打开日志目录") {
                    core.openLogDirectory()
                }
                .controlSize(.small)
            }
            .padding(10)

            Divider()

            if core.recentLogs.isEmpty {
                VStack(spacing: 8) {
                    Text("暂无日志")
                        .foregroundStyle(.tertiary)
                    Text("启动 Core 后，其输出会显示在这里。")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    ScrollViewReader { proxy in
                        LazyVStack(alignment: .leading, spacing: 2) {
                            ForEach(Array(core.recentLogs.enumerated()), id: \.offset) { _, line in
                                Text(line)
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                        .padding(8)
                        .onAppear {
                            proxy.scrollTo(core.recentLogs.count - 1, anchor: .bottom)
                        }
                        .onChange(of: core.recentLogs.count) {
                            proxy.scrollTo(core.recentLogs.count - 1, anchor: .bottom)
                        }
                    }
                }
                .background(Color(.textBackgroundColor))
            }
        }
        .navigationTitle("Core 日志")
    }
}

// MARK: - 主题个性化

/// 主题设置：主题色、外观模式、气泡样式、字号、圆角
struct ThemeSettingsSection: View {
    @Environment(ThemeSettings.self) private var theme
    @State private var showColorPicker = false
    @State private var customColor = Color.clear

    var body: some View {
        Form {
            Section("主题色") {
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 4), spacing: 10) {
                    ForEach(ThemeSettings.presets) { preset in
                        let isSelected = !theme.useCustomAccent && theme.accentHex.lowercased() == preset.hex.lowercased()
                        Button {
                            theme.accentHex = preset.hex
                            theme.useCustomAccent = false
                        } label: {
                            VStack(spacing: 6) {
                                Circle()
                                    .fill(Color(hex: preset.hex) ?? .gray)
                                    .frame(width: 36, height: 36)
                                    .overlay(
                                        Circle().strokeBorder(isSelected ? Color.primary : .clear, lineWidth: 2)
                                    )
                                    .overlay(
                                        Image(systemName: "checkmark")
                                            .font(.caption.bold())
                                            .foregroundStyle(.white)
                                            .opacity(isSelected ? 1 : 0)
                                    )
                                Text(preset.name)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .buttonStyle(.plain)
                    }

                    // 自定义颜色
                    Button {
                        customColor = Color(hex: theme.useCustomAccent ? theme.accentHex : ThemeSettings.presets[0].hex) ?? .accentColor
                        showColorPicker = true
                    } label: {
                        VStack(spacing: 6) {
                            Circle()
                                .fill(Color(hex: theme.accentHex) ?? .gray)
                                .frame(width: 36, height: 36)
                                .overlay(
                                    Circle().strokeBorder(theme.useCustomAccent ? Color.primary : .clear, lineWidth: 2)
                                )
                                .overlay(
                                    Image(systemName: "paintbrush.pointed.fill")
                                        .font(.caption)
                                        .foregroundStyle(.white)
                                        .opacity(theme.useCustomAccent ? 1 : 0)
                                )
                            Text("自定义")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .buttonStyle(.plain)
                }
                .padding(.vertical, 4)
            }

            Section("外观") {
                Picker("外观模式", selection: Bindable(theme).appearanceMode) {
                    ForEach(ThemeSettings.AppearanceMode.allCases) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
            }

            Section("气泡") {
                Picker("气泡样式", selection: Bindable(theme).bubbleStyle) {
                    ForEach(ThemeSettings.BubbleStyle.allCases) { style in
                        Text(style.rawValue).tag(style)
                    }
                }
                .pickerStyle(.segmented)

                HStack {
                    Text("圆角")
                    Slider(value: Bindable(theme).cornerRadius, in: 0...24, step: 1)
                    Text("\(Int(theme.cornerRadius))")
                        .foregroundStyle(.secondary)
                        .frame(width: 30)
                }

                HStack {
                    Text("气泡不透明度")
                    Slider(value: Bindable(theme).bubbleOpacity, in: 0.4...1.0, step: 0.05)
                    Text(String(format: "%.0f%%", theme.bubbleOpacity * 100))
                        .foregroundStyle(.secondary)
                        .frame(width: 44)
                }

                Toggle("显示时间戳", isOn: Bindable(theme).showTimestamps)
            }

            Section("文字") {
                HStack {
                    Text("基础字号")
                    Slider(value: Bindable(theme).fontSize, in: 10...28, step: 1)
                    Text("\(Int(theme.fontSize))")
                        .foregroundStyle(.secondary)
                        .frame(width: 30)
                }

                // 预览
                VStack(alignment: .leading, spacing: 8) {
                    Text("预览")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    MessageBubbleView(message: ChatMessage(role: "user", content: "这是一条用户消息的预览。"))
                    MessageBubbleView(message: ChatMessage(role: "assistant", content: "这是 **助手** 消息的预览，支持 *Markdown* 渲染。"))
                }
                .padding(.vertical, 4)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("主题与外观")
        .sheet(isPresented: $showColorPicker) {
            ColorPickerSheet(
                color: $customColor,
                onConfirm: { color in
                    if let hex = color.hexString {
                        theme.accentHex = hex
                        theme.useCustomAccent = true
                    }
                }
            )
        }
    }
}

/// 自定义颜色选择面板
struct ColorPickerSheet: View {
    @Binding var color: Color
    var onConfirm: (Color) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("自定义主题色")
                .font(.title3)
                .bold()

            ColorPicker("选择颜色", selection: $color, supportsOpacity: false)

            HStack {
                Text("HEX")
                Text(color.hexString ?? "—")
                    .font(.system(.body, design: .monospaced))
                    .foregroundStyle(.secondary)
            }

            HStack {
                Spacer()
                Button("取消") { dismiss() }
                Button("应用") {
                    onConfirm(color)
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 320)
    }
}

import SwiftUI
import UniformTypeIdentifiers
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
                Section(loc("Core 服务")) {
                    NavigationLink {
                        ServerSettingsSection()
                    } label: {
                        Label {
                            Text(loc("服务器与进程"))
                        } icon: {
                            Image(systemName: "server.rack")
                                .foregroundStyle(theme.accentColor)
                        }
                    }
                    NavigationLink {
                        LogViewerSection()
                    } label: {
                        Label(loc("查看 Core 日志"), systemImage: "doc.text.magnifyingglass")
                            .foregroundStyle(theme.accentColor)
                    }
                }

                Section(loc("个性化")) {
                    NavigationLink {
                        ThemeSettingsSection()
                    } label: {
                        Label {
                            Text(loc("主题与外观"))
                        } icon: {
                            Image(systemName: "paintpalette")
                                .foregroundStyle(theme.accentColor)
                        }
                    }
                    NavigationLink {
                        BackgroundSettingsSection()
                    } label: {
                        Label(loc("界面背景"), systemImage: "photo.on.rectangle.angled")
                            .foregroundStyle(theme.accentColor)
                    }
                    NavigationLink {
                        ProfileSettingsSection()
                    } label: {
                        Label(loc("用户资料"), systemImage: "person.crop.circle")
                            .foregroundStyle(theme.accentColor)
                    }
                }

Section(loc("数据管理")) {
                    NavigationLink {
                        BackupSettingsView()
                    } label: {
                        Label(loc("备份与受保护模块"), systemImage: "externaldrive.badge.timemachine")
                            .foregroundStyle(theme.accentColor)
                    }
                    Button(role: .destructive) {
                        showResetConfirm = true
                    } label: {
                        Label(loc("重置 Core 数据"), systemImage: "trash")
                    }
                }

                Section(loc("AI 后端与模型")) {
                    NavigationLink {
                        AIBackendSettingsView()
                    } label: {
                        Label("AI 后端配置", systemImage: "server.rack")
                            .foregroundStyle(theme.accentColor)
                    }
                    NavigationLink {
                        ModelManagementView()
                    } label: {
                        Label("模型管理", systemImage: "cube.box.fill")
                            .foregroundStyle(theme.accentColor)
                    }
                    NavigationLink {
                        CoreConfigEditorView()
                    } label: {
                        Label(loc("Core 配置编辑器"), systemImage: "gearshape.2.fill")
                            .foregroundStyle(theme.accentColor)
                    }
                }

                Section(loc("安全与剧情")) {
                    NavigationLink {
                        SafetySettingsView()
                    } label: {
                        Label(loc("安全模块"), systemImage: "shield.checkered")
                            .foregroundStyle(theme.accentColor)
                    }
                    NavigationLink {
                        PlotSettingsView()
                    } label: {
                        Label(loc("剧情系统"), systemImage: "film.stack")
                            .foregroundStyle(theme.accentColor)
                    }
                }

                Section(loc("关于")) {
                    LabeledContent(loc("版本"), value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0")
                    LabeledContent(loc("连接密钥"), value: loc("本地 API · 连接密钥"))
                    Text(loc("连接密钥是访问本机 Core API 的凭据，可在“服务器与进程”中查看或复制。"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button {
                        // 再次查看新人引导：重置完成标记，主界面自动切回引导页
                        UserProfileSettings.shared.onboardingCompleted = false
                    } label: {
                        Label(loc("再次查看新人引导"), systemImage: "sparkles")
                    }
                }
            }
            .navigationTitle(loc("设置"))
        }
        .confirmationDialog(loc("重置 Core 数据"), isPresented: $showResetConfirm, titleVisibility: .visible) {
            Button(loc("删除 Core 数据"), role: .destructive) {
                Task { await core.resetCoreData() }
            }
            Button(loc("取消"), role: .cancel) {}
        } message: {
            VStack(alignment: .leading, spacing: 6) {
                Text(loc("将删除本机全部角色、世界与聊天数据。"))
                Text(loc("完整路径：%@", core.coreDirectory?.path ?? "—"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
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
            Section(loc("运行状态")) {
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
                        Label(loc("启动"), systemImage: "play.fill")
                    }
                    .disabled(isRunningOrBusy)

                    Button {
                        Task { await core.stopCore() }
                    } label: {
                        Label(loc("停止"), systemImage: "stop.fill")
                    }
                    .disabled(!isRunningOrBusy)

                    Button {
                        Task { await core.restartCore() }
                    } label: {
                        Label(loc("重启"), systemImage: "arrow.clockwise")
                    }
                    .disabled(isWorking)
                }
                .disabled(isWorking)
            }

            Section(loc("连接设置")) {
                Toggle(loc("使用自定义服务器"), isOn: $useCustom)
                    .onChange(of: useCustom) { _, newValue in
                        core.useCustomServer = newValue
                        if !newValue { useCustom = false }
                    }
                    .onAppear {
                        useCustom = core.useCustomServer
                    }

                if !useCustom {
                    HStack {
                        Text(loc("端口"))
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
                    Text(loc("默认 18500，修改后需重启 Core 生效。"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    TextField("http://127.0.0.1:18500", text: $customURLText)
                        .textFieldStyle(.roundedBorder)
                        .onAppear { customURLText = core.customBaseURL }
                        .onChange(of: customURLText) { _, newValue in
                            core.customBaseURL = newValue
                        }
                    TextField(loc("访问令牌（可选）"), text: $customTokenText)
                        .textFieldStyle(.roundedBorder)
                        .onAppear { customTokenText = core.customToken }
                        .onChange(of: customTokenText) { _, newValue in
                            core.customToken = newValue
                        }
                    Text(loc("使用外部地址时，App 将不再管理本机 Core 进程；若服务器要求鉴权，请在此填写访问令牌。"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section(loc("工具")) {
                Button(loc("打开日志目录")) {
                    core.openLogDirectory()
                }
                Button(loc("复制 Token")) {
                    let value = core.useCustomServer ? core.customToken : (core.token ?? "")
                    guard !value.isEmpty else { return }
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(value, forType: .string)
                    copiedToken = true
                }
                .disabled(core.useCustomServer ? core.customToken.isEmpty : core.token == nil)

                if copiedToken {
                    Text(loc("已复制到剪贴板"))
                        .font(.caption)
                        .foregroundStyle(.green)
                }

                LabeledContent(loc("Core 目录"), value: core.coreDirectory?.path ?? "—")
                    .font(.caption)
            }
        }
        .formStyle(.grouped)
        .navigationTitle(loc("服务器与进程"))
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

/// 日志级别筛选选项（全部 = 不筛选；其余为最低显示级别）
private enum LogFilter: Int, CaseIterable, Identifiable {
    case all, debug, info, warning, error, critical

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .all: return loc("全部")
        case .debug: return loc("调试")
        case .info: return loc("信息")
        case .warning: return loc("警告")
        case .error: return loc("错误")
        case .critical: return loc("严重")
        }
    }

    /// 对应的最低显示级别（全部 = nil）
    var minimumLevel: LogLevel? {
        switch self {
        case .all: return nil
        case .debug: return .debug
        case .info: return .info
        case .warning: return .warning
        case .error: return .error
        case .critical: return .critical
        }
    }
}

/// 日志查看器：Core 日志文件 + App 捕获的进程输出（支持分级筛选、复制、导出）
struct LogViewerSection: View {
    @Environment(CoreManager.self) private var core

    @State private var filter: LogFilter = .all
    @State private var copied = false
    @State private var showAlert = false
    @State private var alertTitle = ""
    @State private var alertMessage = ""

    private var allEntries: [LogEntry] { core.logEntries }

    private var filteredEntries: [LogEntry] {
        allEntries.filter { $0.matches(levelFilter: filter.minimumLevel) }
    }

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            if !core.logFileExists && !allEntries.isEmpty {
                Text(loc("未找到 Core 日志文件（logs/xijian-api.log），以下为 App 捕获的进程输出。"))
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 10)
                    .padding(.top, 6)
            }
            if filteredEntries.isEmpty {
                emptyState
            } else {
                logList
            }
        }
        .navigationTitle(loc("Core 日志"))
        .onAppear {
            core.refreshLogs()
        }
        .onChange(of: core.recentLogs.count) {
            core.refreshLogs()
        }
        .onChange(of: core.state) {
            core.refreshLogs()
        }
        .alert(alertTitle, isPresented: $showAlert) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(alertMessage)
        }
    }

    // MARK: 工具栏

    private var toolbar: some View {
        VStack(spacing: 8) {
            HStack(spacing: 10) {
                Picker(loc("级别筛选"), selection: $filter) {
                    ForEach(LogFilter.allCases) { option in
                        Text(option.title).tag(option)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: 460)
                Spacer()
                Text(countText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            HStack(spacing: 8) {
                Button {
                    core.refreshLogs()
                } label: {
                    Label(loc("刷新"), systemImage: "arrow.clockwise")
                }
                .controlSize(.small)

                Button {
                    copyFilteredLogs()
                } label: {
                    Label(copied ? loc("已复制") : loc("复制"), systemImage: copied ? "checkmark" : "doc.on.doc")
                }
                .controlSize(.small)
                .disabled(filteredEntries.isEmpty)

                Button {
                    exportFilteredLogs()
                } label: {
                    Label(loc("导出"), systemImage: "square.and.arrow.up")
                }
                .controlSize(.small)
                .disabled(filteredEntries.isEmpty)

                Button(loc("打开日志目录")) {
                    core.openLogDirectory()
                }
                .controlSize(.small)

                Spacer()
            }
        }
        .padding(10)
    }

    /// 条数说明：有筛选时显示「共 N 条（筛选自 M 条）」
    private var countText: String {
        if filter.minimumLevel != nil {
            return loc("共 %lld 条（筛选自 %lld 条）", filteredEntries.count, allEntries.count)
        }
        return loc("共 %lld 条", allEntries.count)
    }

    // MARK: 列表 / 空态

    private var logList: some View {
        ScrollView {
            ScrollViewReader { proxy in
                LazyVStack(alignment: .leading, spacing: 2) {
                    ForEach(filteredEntries) { entry in
                        LogEntryRow(entry: entry)
                    }
                }
                .padding(8)
                .onAppear {
                    scrollToBottom(proxy)
                }
                .onChange(of: filteredEntries.count) {
                    scrollToBottom(proxy)
                }
            }
        }
        .background(Color(.textBackgroundColor))
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        if let lastID = filteredEntries.last?.id {
            proxy.scrollTo(lastID, anchor: .bottom)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            if allEntries.isEmpty && !core.logFileExists {
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.largeTitle)
                    .foregroundStyle(.tertiary)
                Text(loc("暂无日志"))
                    .foregroundStyle(.secondary)
                Text(loc("未找到 Core 日志文件：\n%@\n启动 Core 后日志会自动生成。", core.coreLogFileURL?.path ?? "—"))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
            } else if allEntries.isEmpty {
                Image(systemName: "doc.text")
                    .font(.largeTitle)
                    .foregroundStyle(.tertiary)
                Text(loc("暂无日志"))
                    .foregroundStyle(.secondary)
                Text(core.logFileLoadError ?? loc("Core 日志文件为空，暂无进程输出。"))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
            } else {
                Image(systemName: "line.3.horizontal.decrease.circle")
                    .font(.largeTitle)
                    .foregroundStyle(.tertiary)
                Text(loc("没有符合当前筛选条件的日志"))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 复制 / 导出

    /// 复制当前筛选后的全部日志（含级别中文名与时间）到剪贴板
    private func copyFilteredLogs() {
        let text = logText(for: filteredEntries)
        guard !text.isEmpty else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        copied = true
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            copied = false
        }
    }

    /// 导出当前筛选后的全部日志到用户选择的文件（默认下载目录，默认名 xijian-logs-时间戳.log）
    private func exportFilteredLogs() {
        let text = logText(for: filteredEntries)
        guard !text.isEmpty else { return }
        let panel = NSSavePanel()
        panel.title = loc("导出日志")
        panel.message = loc("将当前筛选后的全部日志导出为文本文件")
        panel.nameFieldStringValue = Self.exportFileName()
        panel.allowedContentTypes = [.plainText]
        panel.directoryURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        panel.begin { response in
            Task { @MainActor [self] in
                guard response == .OK, let url = panel.url else { return }
                do {
                    try text.write(to: url, atomically: true, encoding: .utf8)
                    self.alertTitle = loc("导出成功")
                    self.alertMessage = loc("日志已导出到：\n%@", url.path)
                    self.showAlert = true
                } catch {
                    self.alertTitle = loc("导出失败")
                    self.alertMessage = loc("写入文件失败：%@", error.localizedDescription)
                    self.showAlert = true
                }
            }
        }
    }

    /// 日志文本格式：[级别中文名] 时间 消息（复制 / 导出共用）
    private func logText(for entries: [LogEntry]) -> String {
        entries.map { entry in
            var parts = ["[\(entry.level.displayName)]"]
            if let timestamp = entry.timestamp {
                parts.append(Self.logTextFormatter.string(from: timestamp))
            }
            parts.append(entry.message)
            return parts.joined(separator: " ")
        }
        .joined(separator: "\n")
    }

    private static let logTextFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter
    }()

    private static func exportFileName() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return "xijian-logs-\(formatter.string(from: Date())).log"
    }
}

/// 单条日志行：级别徽标 + 时间 + 消息（按级别着色）
struct LogEntryRow: View {
    let entry: LogEntry

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(entry.level.displayName)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(entry.level.color)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(entry.level.color.opacity(0.12), in: Capsule())

            Text(timeText)
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(entry.level.color)

            Text(entry.message)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(entry.level.color)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 1)
    }

    private var timeText: String {
        guard let timestamp = entry.timestamp else { return "—" }
        return Self.timeFormatter.string(from: timestamp)
    }

    private static let timeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return formatter
    }()
}

// MARK: - 主题个性化

/// 主题设置：主题色、外观模式、气泡样式、字号、圆角
struct ThemeSettingsSection: View {
    @Environment(ThemeSettings.self) private var theme
    @State private var showColorPicker = false
    @State private var customColor = Color.clear

    var body: some View {
        Form {
            Section(loc("主题色")) {
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
                                Text(Bundle.xiJian.localizedString(forKey: preset.name, value: preset.name, table: nil))
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
                            Text(loc("自定义"))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .buttonStyle(.plain)
                }
                .padding(.vertical, 4)
            }

            Section(loc("外观")) {
                Picker(loc("外观模式"), selection: Bindable(theme).appearanceMode) {
                    ForEach(ThemeSettings.AppearanceMode.allCases) { mode in
                        Text(mode.displayName).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
            }

            Section(loc("气泡")) {
                Picker(loc("气泡样式"), selection: Bindable(theme).bubbleStyle) {
                    ForEach(ThemeSettings.BubbleStyle.allCases) { style in
                        Text(style.displayName).tag(style)
                    }
                }
                .pickerStyle(.segmented)

                HStack {
                    Text(loc("圆角"))
                    Slider(value: Bindable(theme).cornerRadius, in: 0...24, step: 1)
                    Text("\(Int(theme.cornerRadius))")
                        .foregroundStyle(.secondary)
                        .frame(width: 30)
                }

                HStack {
                    Text(loc("气泡不透明度"))
                    Slider(value: Bindable(theme).bubbleOpacity, in: 0.4...1.0, step: 0.05)
                    Text(String(format: "%.0f%%", theme.bubbleOpacity * 100))
                        .foregroundStyle(.secondary)
                        .frame(width: 44)
                }

                Toggle(loc("显示时间戳"), isOn: Bindable(theme).showTimestamps)
            }

            Section(loc("文字")) {
                HStack {
                    Text(loc("基础字号"))
                    Slider(value: Bindable(theme).fontSize, in: 10...28, step: 1)
                    Text("\(Int(theme.fontSize))")
                        .foregroundStyle(.secondary)
                        .frame(width: 30)
                }

                // 预览
                VStack(alignment: .leading, spacing: 8) {
                    Text(loc("预览"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    MessageBubbleView(message: ChatMessage(role: "user", content: loc("这是一条用户消息的预览。")))
                    MessageBubbleView(message: ChatMessage(role: "assistant", content: loc("这是 **助手** 消息的预览，支持 *Markdown* 渲染。")))
                }
                .padding(.vertical, 4)
            }
        }
        .formStyle(.grouped)
        .navigationTitle(loc("主题与外观"))
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
            Text(loc("自定义主题色"))
                .font(.title3)
                .bold()

            ColorPicker(loc("选择颜色"), selection: $color, supportsOpacity: false)

            HStack {
                Text("HEX")
                Text(color.hexString ?? "—")
                    .font(.system(.body, design: .monospaced))
                    .foregroundStyle(.secondary)
            }

            HStack {
                Spacer()
                Button(loc("取消")) { dismiss() }
                Button(loc("应用")) {
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

// MARK: - Core 配置编辑器

/// Core 配置编辑器：通过 GUI 编辑 config.toml 关键字段，避免手写 TOML。
struct CoreConfigEditorView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @State private var showAlert = false
    @State private var alertTitle = ""
    @State private var alertMessage = ""
    @State private var isSaving = false

    // Server settings
    @State private var host = "127.0.0.1"
    @State private var port = 18500
    @State private var devMode = false
    @State private var keepTokenFile = false
    @State private var driver = "auto" // auto | werkzeug | waitress

    // Storage settings
    @State private var baseDir = "~/Library/Application Support/XiJian/Core"
    @State private var modelsSubdir = "models"

    // Features
    @State private var seedDefaultData = false
    @State private var protectionModule = true
    @State private var rateLimit = false

    // Overload
    @State private var overloadMonitor = true
    @State private var overloadTier = "medium" // medium | strict

    var body: some View {
        Form {
            Section(loc("服务器")) {
                XJSettingRow(title: loc("监听地址"), subtitle: loc("默认 127.0.0.1，仅本地回环")) {
                    TextField("127.0.0.1", text: $host)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 200)
                }

                XJSettingRow(title: loc("端口"), subtitle: loc("默认 18500，1-65535")) {
                    TextField("18500", value: $port, formatter: NumberFormatter())
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 120)
                }

                XJSettingRow(title: loc("开发模式"), subtitle: loc("启用测试路由、自动生成 token、详细错误")) {
                    Toggle("", isOn: $devMode)
                }

                XJSettingRow(title: loc("保留 token 文件"), subtitle: loc("关闭时退出后删除 token")) {
                    Toggle("", isOn: $keepTokenFile)
                }

                XJSettingRow(title: loc("WSGI 驱动"), subtitle: loc("waitress 不支持 WebSocket")) {
                    Picker("", selection: $driver) {
                        Text(loc("自动 (werkzeug)")).tag("auto")
                        Text("werkzeug").tag("werkzeug")
                        Text("waitress").tag("waitress")
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 300)
                }
            }

            Section(loc("存储")) {
                XJSettingRow(title: loc("基础目录"), subtitle: loc("模型权重、上传、快照根目录；可用 XIJIAN_DATA_DIR 覆盖")) {
                    TextField(baseDir, text: $baseDir)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: .infinity)
                }

                XJSettingRow(title: loc("模型子目录"), subtitle: loc("相对基础目录，默认 models")) {
                    TextField("models", text: $modelsSubdir)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 200)
                }
            }

            Section(loc("功能开关")) {
                XJSettingRow(title: loc("预填充演示数据"), subtitle: loc("启动时创建 Yuki、Modern Tokyo 等演示记录")) {
                    Toggle("", isOn: $seedDefaultData)
                }

                XJSettingRow(title: loc("保护模块"), subtitle: loc("启用 OOC 检测、提示词注入防护、数据回滚")) {
                    Toggle("", isOn: $protectionModule)
                }

                XJSettingRow(title: loc("限流"), subtitle: loc("启用 API 请求限流（生产环境建议开启）")) {
                    Toggle("", isOn: $rateLimit)
                }
            }

            Section(loc("过载保护")) {
                XJSettingRow(title: loc("监控开启"), subtitle: loc("后台 1Hz 采样 CPU/内存/磁盘，超阈值自动限流/终止")) {
                    Toggle("", isOn: $overloadMonitor)
                }

                XJSettingRow(title: loc("严格度"), subtitle: loc("MacBook Air 推荐 strict；Mac mini/Pro 推荐 medium")) {
                    Picker("", selection: $overloadTier) {
                        Text(loc("适中")).tag("medium")
                        Text(loc("严格")).tag("strict")
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 300)
                }
            }

            Section {
                HStack {
                    Spacer()
                    Button(loc("保存到 config.toml")) {
                        saveConfig()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(isSaving)
                    .controlSize(.large)

                    if isSaving {
                        ProgressView()
                            .controlSize(.small)
                    }
                    Spacer()
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(loc("Core 配置编辑器"))
        .onAppear {
            loadConfig()
        }
        .alert(alertTitle, isPresented: $showAlert) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(alertMessage)
        }
    }

    private func loadConfig() {
        // Read from core's current config if available, otherwise use defaults
        // This is a simplified version — in production you'd parse the actual config.toml
        let config = core.currentConfig
        host = config.host
        port = config.port
        devMode = config.devMode
        keepTokenFile = config.keepTokenFile
        driver = config.driver
        baseDir = config.baseDir
        modelsSubdir = config.modelsSubdir
        seedDefaultData = config.seedDefaultData
        protectionModule = config.protectionModule
        rateLimit = config.rateLimit
        overloadMonitor = config.overloadMonitor
        overloadTier = config.overloadTier
    }

    private func saveConfig() {
        isSaving = true
        Task {
            do {
                try core.updateConfig(
                    host: host,
                    port: port,
                    devMode: devMode,
                    keepTokenFile: keepTokenFile,
                    driver: driver,
                    baseDir: baseDir,
                    modelsSubdir: modelsSubdir,
                    seedDefaultData: seedDefaultData,
                    protectionModule: protectionModule,
                    rateLimit: rateLimit,
                    overloadMonitor: overloadMonitor,
                    overloadTier: overloadTier
                )
                await MainActor.run {
                    isSaving = false
                    alertTitle = loc("保存成功")
                    alertMessage = loc("配置已写入 config.toml，重启 Core 后生效。")
                    showAlert = true
                }
            } catch {
                await MainActor.run {
                    isSaving = false
                    alertTitle = loc("保存失败")
                    alertMessage = error.localizedDescription
                    showAlert = true
                }
            }
        }
    }
}

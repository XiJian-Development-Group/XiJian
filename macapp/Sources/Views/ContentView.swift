import SwiftUI
import XiJianManagerKit

/// 主界面：NavigationSplitView，侧边栏包含 Hermes 控制 / 终端 / Dashboard / 设置
public struct ContentView: View {
    @Environment(AppLifecycleManager.self) private var lifecycle
    @Environment(ThemeSettings.self) private var theme
    @Environment(UserProfileSettings.self) private var profile
    @State private var appVM = AppViewModel.shared

    public init() {}

    public var body: some View {
        @Bindable var lifecycle = lifecycle
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 200, ideal: 230, max: 300)
        } detail: {
            detail
                .frame(minWidth: 600, minHeight: 500)
        }
        .navigationTitle(Text(xj: "隙间 XiJian Manager"))
        .alert(loc("出错了"), isPresented: $appVM.showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(appVM.errorMessage ?? "")
        }
    }

    private var sidebar: some View {
        List(selection: $appVM.selectedTab) {
            Section(loc("Hermes")) {
                ForEach(AppViewModel.Tab.allCases) { tab in
                    Label(tab.displayName, systemImage: tab.icon)
                        .tag(tab)
                }
            }
        }
        .listStyle(.sidebar)
    }

    @ViewBuilder
    private var detail: some View {
        switch appVM.selectedTab {
        case .status:
            HermesStatusView()
        case .terminal:
            HermesTerminalView()
        case .dashboard:
            HermesDashboardView()
        case .workspace:
            WorkspaceSwitcherView()
        case .character:
            CharacterEditorView()
        case .memory:
            MemoryEditorView()
        case .mcp:
            MCPView()
        case .skills:
            SkillHubView()
        case .settings:
            HermesSettingsView()
        }
    }
}

// MARK: - Hermes 状态视图

struct HermesStatusView: View {
    @Environment(AppLifecycleManager.self) private var lifecycle
    @State private var isWorking = false

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Circle()
                    .fill(lifecycle.gatewayState.color)
                    .frame(width: 14, height: 14)
                Text(lifecycle.gatewayState.label)
                    .font(.title2)
                Spacer()
            }

            Divider()

            HStack(spacing: 12) {
                Button(loc("启动")) {
                    Task { await action { try await lifecycle.startGateway() } }
                }
                .disabled(isWorking || lifecycle.gatewayState == .running || lifecycle.gatewayState == .notInstalled)

                Button(loc("停止")) {
                    Task { await action { try await lifecycle.stopGateway() } }
                }
                .disabled(isWorking || lifecycle.gatewayState != .running)

                Button(loc("重启")) {
                    Task { await action { try await lifecycle.restartGateway() } }
                }
                .disabled(isWorking || (lifecycle.gatewayState != .running && lifecycle.gatewayState != .crashed))
            }

            if lifecycle.gatewayState == .notInstalled {
                Divider()
                VStack(alignment: .leading, spacing: 8) {
                    Text("Hermes 未安装")
                        .font(.headline)
                    Text("去「设置」页面安装 Hermes（从 GitHub releases 下载 hermes-agent）。")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }

            if let err = lifecycle.lastError {
                Text(err)
                    .font(.callout)
                    .foregroundStyle(.red)
            }

            Spacer()
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func action(_ work: () async throws -> Void) async {
        isWorking = true
        defer { isWorking = false }
        do {
            try await work()
        } catch {
            // 错误已记录到 lifecycle.lastError
        }
    }
}

// MARK: - 终端视图（Phase 1.6 PTY 接入）

struct HermesTerminalView: View {
    @Environment(AppLifecycleManager.self) private var lifecycle
    @State private var terminalModel = TerminalSessionModel()

    var body: some View {
        VStack(spacing: 0) {
            // 工具栏
            HStack {
                Button(loc("连接")) {
                    Task { await terminalModel.connect(hermesPath: lifecycle.installedHermesPath) }
                }
                .disabled(terminalModel.isConnected || lifecycle.installedHermesPath == nil)
                Button(loc("断开")) {
                    Task { await terminalModel.disconnect() }
                }
                .disabled(!terminalModel.isConnected)
                Spacer()
                if let path = lifecycle.installedHermesPath {
                    Text(path.path).font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(8)
            .background(Color.gray.opacity(0.05))

            Divider()

            if terminalModel.isConnected {
                TerminalScrollView(lines: terminalModel.lines)
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "terminal.fill")
                        .font(.system(size: 48))
                        .foregroundStyle(.secondary)
                    Text(loc("点击「连接」启动终端"))
                        .font(.title3)
                    Text("PTY → Hermes CLI 子进程")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            // 输入框
            if terminalModel.isConnected {
                TerminalInputBar { text in
                    Task { await terminalModel.send(text) }
                }
            }
        }
    }
}

/// 终端会话模型：包装 PTYTerminal。
@MainActor
final class TerminalSessionModel: ObservableObject {
    @Published var isConnected = false
    @Published var lines: [TerminalLine] = []
    private var pty: PTYTerminal?

    func connect(hermesPath: URL?) async {
        guard let path = hermesPath else { return }
        let config = PTYTerminal.Config(command: path.path, arguments: ["cli"])
        let pty = PTYTerminal(config: config)
        do {
            try await pty.spawn()
            self.pty = pty
            isConnected = true
            await pty.startReading { [weak self] data in
                Task { @MainActor in
                    self?.append(data: data)
                }
            }
        } catch {
            lines.append(.error("连接失败：\(error)"))
        }
    }

    func disconnect() async {
        await pty?.terminate()
        await pty?.waitForExit()
        pty = nil
        isConnected = false
    }

    func send(_ text: String) async {
        guard let pty else { return }
        try? await pty.write(text + "\n")
    }

    private func append(data: Data) {
        // 简化：用 ANSI parser 解析后渲染（纯文本降级）
        var parser = ANSIParser()
        let tokens = parser.parse(data)
        for token in tokens {
            if case let .text(s) = token {
                for line in s.split(separator: "\n", omittingEmptySubsequences: false) {
                    lines.append(.plain(String(line)))
                }
            }
        }
    }
}

struct TerminalLine: Identifiable {
    let id = UUID()
    let content: String
    let isError: Bool
    static func plain(_ s: String) -> TerminalLine {
        TerminalLine(content: s, isError: false)
    }
    static func error(_ s: String) -> TerminalLine {
        TerminalLine(content: s, isError: true)
    }
}

struct TerminalScrollView: View {
    let lines: [TerminalLine]
    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(lines) { line in
                        Text(line.content)
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(line.isError ? Color.red : Color.primary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 1)
                            .id(line.id)
                    }
                }
            }
            .background(Color.black.opacity(0.9))
            .foregroundStyle(.green)
            .onChange(of: lines.count) { _ in
                if let last = lines.last {
                    proxy.scrollTo(last.id, anchor: .bottom)
                }
            }
        }
    }
}

struct TerminalInputBar: View {
    @State private var input = ""
    let onSend: (String) -> Void

    var body: some View {
        HStack {
            TextField("输入命令", text: $input)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, design: .monospaced))
                .onSubmit { send() }
            Button(loc("发送")) { send() }
                .disabled(input.isEmpty)
        }
        .padding(8)
    }

    private func send() {
        let text = input
        input = ""
        onSend(text)
    }
}

// MARK: - Dashboard 视图

struct HermesDashboardView: View {
    @Environment(AppLifecycleManager.self) private var lifecycle

    var body: some View {
        Group {
            if let port = lifecycle.dashboardPort {
                WebView(url: URL(string: "http://127.0.0.1:\(port)/")!)
                    .frame(minWidth: 600, minHeight: 400)
            } else {
                ContentUnavailableView(
                    "Dashboard 未启动",
                    systemImage: "globe",
                    description: Text("去「状态」页点击「启动」")
                )
            }
        }
    }
}

#if canImport(WebKit)
import WebKit
struct WebView: NSViewRepresentable {
    let url: URL
    func makeNSView(context: Context) -> WKWebView {
        WKWebView()
    }
    func updateNSView(_ nsView: WKWebView, context: Context) {
        nsView.load(URLRequest(url: url))
    }
}
#endif

// MARK: - 设置视图

struct HermesSettingsView: View {
    @Environment(AppLifecycleManager.self) private var lifecycle
    @State private var isInstalling = false

    var body: some View {
        Form {
            Section("Hermes 安装") {
                if let path = lifecycle.installedHermesPath {
                    LabeledContent("路径", value: path.path)
                        .font(.callout)
                } else {
                    Text("未检测到 Hermes")
                        .foregroundStyle(.secondary)
                }
                Button(loc("从 GitHub 安装 Hermes")) {
                    Task { await install() }
                }
                .disabled(isInstalling)
            }
            Section("日志") {
                LabeledContent("目录", value: lifecycle.logsDirectory.path)
                    .font(.callout)
                Button(loc("打开日志目录")) {
                    NSWorkspace.shared.open(lifecycle.logsDirectory)
                }
            }
        }
        .padding()
    }

    private func install() async {
        isInstalling = true
        defer { isInstalling = false }
        do {
            try await lifecycle.installHermes()
        } catch {
            // 错误已记录
        }
    }
}

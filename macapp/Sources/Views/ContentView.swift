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
            StatusPlaceholderView()
        case .terminal:
            TerminalPlaceholderView()
        case .dashboard:
            DashboardPlaceholderView()
        case .settings:
            SettingsPlaceholderView()
        }
    }
}

/// Hermes 状态概览占位（Phase 1 任务 1.8 实现完整版）
struct StatusPlaceholderView: View {
    @Environment(AppLifecycleManager.self) private var lifecycle
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(xj: "Hermes 状态")
                .font(.title)
            Text("Phase 1.8 实现：start / stop / restart + 健康检查 + 日志路径")
                .font(.callout)
                .foregroundStyle(.secondary)
            Divider()
            HStack {
                Circle()
                    .fill(lifecycle.gatewayState.color)
                    .frame(width: 12, height: 12)
                Text(lifecycle.gatewayState.label)
            }
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// 终端占位（Phase 1 任务 1.6 + 1.7 实现 PTYTerminal + HermesCommandRunner）
struct TerminalPlaceholderView: View {
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "terminal.fill")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)
            Text(xj: "内嵌终端对话")
                .font(.title2)
            Text("Phase 1.6-1.7 实现：PTYTerminal + ANSIParser + Hermes CLI 子进程")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 400)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// Dashboard 占位（Phase 1 任务 1.7 实现 WKWebView 嵌入 hermes dashboard）
struct DashboardPlaceholderView: View {
    @Environment(AppLifecycleManager.self) private var lifecycle
    var body: some View {
        if let port = lifecycle.dashboardPort {
            VStack(spacing: 12) {
                Text("Dashboard 端口: \(port)")
                Text("Phase 1.7 实现 WKWebView")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .padding()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ContentUnavailableView(
                "Dashboard 未启动",
                systemImage: "globe",
                description: Text("点击侧栏「启动 Hermes」按钮")
            )
        }
    }
}

/// 设置占位（Phase 1 任务 1.8 实现完整设置面板）
struct SettingsPlaceholderView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(xj: "设置")
                .font(.title)
            Text("Phase 1.8 实现：安装路径 / CN 镜像 / CA 验证 / 备份位置")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

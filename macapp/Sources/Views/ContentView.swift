import SwiftUI
import XiJianKit

/// 主界面：NavigationSplitView，侧边栏包含 对话/角色/世界/记忆/设置
public struct ContentView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @State private var appVM = AppViewModel.shared
    @State private var chatVM = ChatViewModel()
    @State private var characterVM = CharacterViewModel()
    @State private var worldVM = WorldViewModel()
    @State private var memoryVM = MemoryViewModel()
    @State private var packVM = PackViewModel()

    public init() {}

    public var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 200, ideal: 230, max: 300)
        } detail: {
            detail
                .frame(minWidth: 600, minHeight: 500)
        }
        .navigationTitle("隙间 XiJian")
        .alert("出错了", isPresented: $appVM.showError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(appVM.errorMessage ?? "")
        }
        .task {
            // 首次出现时预加载模型
            await chatVM.loadModels()
        }
    }

    // MARK: 侧边栏

    private var sidebar: some View {
        List(selection: $appVM.selectedTab) {
            Section("功能") {
                ForEach(AppViewModel.Tab.allCases) { tab in
                    Label(tab.rawValue, systemImage: tab.icon)
                        .tag(tab)
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            StatusIndicatorView()
        }
    }

    // MARK: 详情区

    @ViewBuilder
    private var detail: some View {
        switch appVM.selectedTab {
        case .chat:
            ChatView(viewModel: chatVM)
        case .characters:
            CharacterListView(viewModel: characterVM)
        case .worlds:
            WorldListView(viewModel: worldVM)
        case .packs:
            PackListView(viewModel: packVM)
        case .memory:
            MemoryView(viewModel: memoryVM)
        case .settings:
            SettingsView()
        }
    }
}

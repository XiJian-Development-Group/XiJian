import SwiftUI
import XiJianKit

/// 主界面：NavigationSplitView，侧边栏包含 首页/对话/角色/世界/记忆/设置
/// 首次启动（未完成新人引导）时展示 OnboardingView，完成后进入主界面（默认首页）。
public struct ContentView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @Environment(UserProfileSettings.self) private var profile
    @State private var appVM = AppViewModel.shared
    @State private var chatVM = ChatViewModel()
    @State private var characterVM = CharacterViewModel()
    @State private var worldVM = WorldViewModel()
    @State private var memoryVM = MemoryViewModel()
    @State private var packVM = PackViewModel()

    public init() {}

    public var body: some View {
        Group {
            if !profile.onboardingCompleted {
                OnboardingView()
            } else {
                mainInterface
            }
        }
        .animation(.spring(response: 0.4, dampingFraction: 1.0), value: profile.onboardingCompleted)
        // 全局错误弹窗挂在最外层：引导页 / 主界面均可呈现
        .alert(loc("出错了"), isPresented: $appVM.showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(appVM.errorMessage ?? "")
        }
    }

    // MARK: 主界面

    private var mainInterface: some View {
        ZStack {
            // 全局 UI 背景（图片 / GIF / 视频，可模糊）
            BackgroundLayerView()

            NavigationSplitView {
                sidebar
                    .navigationSplitViewColumnWidth(min: 200, ideal: 230, max: 300)
            } detail: {
                detail
                    .frame(minWidth: 600, minHeight: 500)
            }
        }
        .navigationTitle(Text(xj: "隙间 XiJian"))
        .task {
            // 首次出现时预加载模型
            await chatVM.loadModels()
        }
    }

    // MARK: 侧边栏

    private var sidebar: some View {
        List(selection: $appVM.selectedTab) {
            Section(loc("功能")) {
                ForEach(AppViewModel.Tab.allCases) { tab in
                    Label(tab.displayName, systemImage: tab.icon)
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
        case .home:
            HomeView()
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

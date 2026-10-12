import Foundation
import Observation

/// 应用级 ViewModel：导航、全局设置、错误提示
@Observable
@MainActor
public final class AppViewModel {

    public static let shared = AppViewModel()

    /// 侧边栏标签
    public enum Tab: String, CaseIterable, Identifiable {
        case status = "状态"
        case terminal = "终端"
        case dashboard = "Dashboard"
        case workspace = "Workspace"
        case character = "角色"
        case memory = "记忆"
        case mcp = "MCP"
        case skills = "Skills"
        case tokens = "Tokens"
        case network = "网络"
        case theme = "主题"
        case settings = "设置"

        public var id: String { rawValue }

        public var displayName: String {
            switch self {
            case .status: return loc("状态")
            case .terminal: return loc("终端")
            case .dashboard: return loc("Dashboard")
            case .workspace: return loc("Workspace")
            case .character: return loc("角色")
            case .memory: return loc("记忆")
            case .mcp: return loc("MCP")
            case .skills: return loc("Skills")
            case .tokens: return loc("Tokens")
            case .network: return loc("网络")
            case .theme: return loc("主题")
            case .settings: return loc("设置")
            }
        }

        public var icon: String {
            switch self {
            case .status: return "circle.fill"
            case .terminal: return "terminal.fill"
            case .dashboard: return "globe"
            case .workspace: return "folder.fill"
            case .character: return "person.crop.circle"
            case .memory: return "brain.head.profile"
            case .mcp: return "server.rack"
            case .skills: return "shippingbox"
            case .tokens: return "chart.bar"
            case .network: return "network"
            case .theme: return "paintbrush"
            case .settings: return "gearshape"
            }
        }
    }

    // MARK: 导航

    public var selectedTab: Tab = .status

    // MARK: 全局错误

    public var showError: Bool = false
    public var errorMessage: String?

    public init() {}

    public func reportError(_ message: String) {
        errorMessage = message
        showError = true
    }
}

import Foundation
import Observation

/// 应用级 ViewModel：导航、全局设置、错误提示
@Observable
@MainActor
public final class AppViewModel {

    public static let shared = AppViewModel()

    /// 侧边栏标签
    enum Tab: String, CaseIterable, Identifiable {
        case home = "首页"
        case chat = "对话"
        case characters = "角色"
        case worlds = "世界"
        case packs = "资源包"
        case memory = "记忆"
        case settings = "设置"

        var id: String { rawValue }

        /// 本地化显示名
        var displayName: String {
            switch self {
            case .home: return loc("首页")
            case .chat: return loc("对话")
            case .characters: return loc("角色")
            case .worlds: return loc("世界")
            case .packs: return loc("资源包")
            case .memory: return loc("记忆")
            case .settings: return loc("设置")
            }
        }

        var icon: String {
            switch self {
            case .home: return "house.fill"
            case .chat: return "bubble.left.and.bubble.right"
            case .characters: return "person.2"
            case .worlds: return "globe.asia.australia"
            case .packs: return "shippingbox"
            case .memory: return "brain.head.profile"
            case .settings: return "gearshape"
            }
        }
    }

    // MARK: 导航

    var selectedTab: Tab = .home

    // MARK: 全局聊天参数（UserDefaults 持久化）

    var temperature: Double {
        didSet {
            let clamped = min(max(temperature, 0), 2)
            if temperature != clamped { temperature = clamped; return }
            UserDefaults.standard.set(temperature, forKey: XJDefaultsKey.chatTemperature)
        }
    }

    var maxTokens: Int {
        didSet {
            let clamped = min(max(maxTokens, 64), 32768)
            if maxTokens != clamped { maxTokens = clamped; return }
            UserDefaults.standard.set(maxTokens, forKey: XJDefaultsKey.chatMaxTokens)
        }
    }

    /// 是否启用记忆召回
    var recallEnabled: Bool {
        didSet { UserDefaults.standard.set(recallEnabled, forKey: XJDefaultsKey.chatRecall) }
    }

    /// 默认角色（发送聊天时注入 character_id）
    var selectedCharacterID: String? {
        didSet { UserDefaults.standard.set(selectedCharacterID, forKey: XJDefaultsKey.chatCharacter) }
    }

    /// 默认世界（发送聊天时注入 world_id）
    var selectedWorldID: String? {
        didSet { UserDefaults.standard.set(selectedWorldID, forKey: XJDefaultsKey.chatWorld) }
    }

    // MARK: 全局错误

    var errorMessage: String?
    var showError = false

    // MARK: 初始化

    init() {
        let defaults = UserDefaults.standard
        temperature = defaults.object(forKey: XJDefaultsKey.chatTemperature) as? Double ?? 0.7
        maxTokens = defaults.object(forKey: XJDefaultsKey.chatMaxTokens) as? Int ?? 2048
        recallEnabled = defaults.object(forKey: XJDefaultsKey.chatRecall) as? Bool ?? true
        selectedCharacterID = defaults.string(forKey: XJDefaultsKey.chatCharacter)
        selectedWorldID = defaults.string(forKey: XJDefaultsKey.chatWorld)
    }

    // MARK: 错误呈现

    func presentError(_ message: String) {
        errorMessage = message
        showError = true
    }

    func presentError(_ error: Error) {
        if let apiError = error as? APIError {
            presentError(apiError.message)
        } else {
            presentError(error.localizedDescription)
        }
    }
}

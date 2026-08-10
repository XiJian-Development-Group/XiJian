import Foundation
import Observation

/// 用户资料与偏好设置（新人引导第二页 + 设置页共用）。
/// `@Observable` + UserDefaults 持久化；通过 `@Environment(UserProfileSettings.self)` 注入视图。
@Observable
@MainActor
public final class UserProfileSettings {

    /// 全局单例（App 启动时注入环境）
    public static let shared = UserProfileSettings()

    // MARK: 枚举

    /// AI 功能来源
    enum AISource: String, CaseIterable, Identifiable {
        case local = "local"
        case remote = "remote"

        var id: String { rawValue }

        /// 本地化显示名
        var displayName: String {
            switch self {
            case .local: return loc("本地")
            case .remote: return loc("远程")
            }
        }
    }

    /// 通知权限状态（与系统授权状态同步，展示用）
    enum NotificationState: String {
        case notDetermined = "notDetermined"
        case authorized = "authorized"
        case denied = "denied"

        /// 本地化显示名
        var displayName: String {
            switch self {
            case .notDetermined: return loc("未请求")
            case .authorized: return loc("已授权")
            case .denied: return loc("已拒绝")
            }
        }
    }

    // MARK: 持久化键

    private enum Key {
        static let userName = "xijian.profile.userName"
        static let aliases = "xijian.profile.aliases"
        static let identity = "xijian.profile.identity"
        static let onboardingCompleted = "xijian.profile.onboardingCompleted"
        static let aiSource = "xijian.profile.aiSource"
        static let remoteEndpoint = "xijian.profile.remoteEndpoint"
        static let remoteToken = "xijian.profile.remoteToken"
        static let remoteModelID = "xijian.profile.remoteModelID"
        static let notificationState = "xijian.profile.notificationState"
        static let backgroundActivity = "xijian.profile.backgroundActivity"
    }

    // MARK: 属性（didSet 持久化）

    /// 用户名（可空）
    var userName: String { didSet { UserDefaults.standard.set(userName, forKey: Key.userName) } }
    /// 别称（多个，可空数组）
    var aliases: [String] { didSet { UserDefaults.standard.set(aliases, forKey: Key.aliases) } }
    /// 用户身份描述
    var identityDescription: String { didSet { UserDefaults.standard.set(identityDescription, forKey: Key.identity) } }
    /// 新人引导是否完成
    public var onboardingCompleted: Bool { didSet { UserDefaults.standard.set(onboardingCompleted, forKey: Key.onboardingCompleted) } }
    /// AI 功能来源
    var aiSource: AISource { didSet { UserDefaults.standard.set(aiSource.rawValue, forKey: Key.aiSource) } }
    /// 远程 API 端点
    var remoteEndpoint: String { didSet { UserDefaults.standard.set(remoteEndpoint, forKey: Key.remoteEndpoint) } }
    /// 远程 Token
    var remoteToken: String { didSet { UserDefaults.standard.set(remoteToken, forKey: Key.remoteToken) } }
    /// 远程模型 ID（暂不指定，可空）
    var remoteModelID: String { didSet { UserDefaults.standard.set(remoteModelID, forKey: Key.remoteModelID) } }
    /// 通知权限状态
    var notificationState: NotificationState { didSet { UserDefaults.standard.set(notificationState.rawValue, forKey: Key.notificationState) } }
    /// 后台活动权限（防 App Nap）
    public var backgroundActivityEnabled: Bool { didSet { UserDefaults.standard.set(backgroundActivityEnabled, forKey: Key.backgroundActivity) } }

    // MARK: 初始化（从 UserDefaults 读取，带默认值）

    /// 初始化（internal：单例 shared 供 App 使用；测试可自行创建实例）
    init() {
        let d = UserDefaults.standard
        userName = d.string(forKey: Key.userName) ?? ""
        aliases = d.stringArray(forKey: Key.aliases) ?? []
        identityDescription = d.string(forKey: Key.identity) ?? ""
        onboardingCompleted = d.bool(forKey: Key.onboardingCompleted)
        aiSource = AISource(rawValue: d.string(forKey: Key.aiSource) ?? "") ?? .local
        remoteEndpoint = d.string(forKey: Key.remoteEndpoint) ?? ""
        remoteToken = d.string(forKey: Key.remoteToken) ?? ""
        remoteModelID = d.string(forKey: Key.remoteModelID) ?? ""
        notificationState = NotificationState(rawValue: d.string(forKey: Key.notificationState) ?? "") ?? .notDetermined
        backgroundActivityEnabled = d.bool(forKey: Key.backgroundActivity)
    }

    // MARK: 操作

    /// 追加一个别称（空白忽略；重复忽略）。
    /// 注意：数组原地修改（append / subscript）不会触发 didSet 持久化，
    /// 因此统一用「整数组赋值」的方式提交，保证改动落到 UserDefaults。
    func addAlias(_ alias: String) {
        let trimmed = alias.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !aliases.contains(trimmed) else { return }
        var updated = aliases
        updated.append(trimmed)
        aliases = updated
    }

    /// 追加一个空别称槽位（「添加别称」按钮用；用户随后填写，留空不持久化额外内容）
    func appendEmptyAlias() {
        var updated = aliases
        updated.append("")
        aliases = updated
    }

    /// 更新指定位置的别称（TextField 逐字编辑用；整数组赋值触发持久化）
    func updateAlias(_ alias: String, at index: Int) {
        guard aliases.indices.contains(index) else { return }
        var updated = aliases
        updated[index] = alias
        aliases = updated
    }

    /// 移除指定位置的别称
    func removeAlias(at index: Int) {
        guard aliases.indices.contains(index) else { return }
        var updated = aliases
        updated.remove(at: index)
        aliases = updated
    }

    /// 移除指定位置的别称（IndexSet 版本）
    func removeAlias(at offsets: IndexSet) {
        var updated = aliases
        updated.remove(atOffsets: offsets)
        aliases = updated
    }

    /// 将 AI 来源应用到 CoreManager（远程 → useCustomServer；本地 → 关闭）。
    /// 与 CoreManager 同 module，直接引用 CoreManager.shared。
    public func applyAISourceToCore() {
        let core = CoreManager.shared
        switch aiSource {
        case .local:
            core.useCustomServer = false
        case .remote:
            core.useCustomServer = true
            if !remoteEndpoint.isEmpty { core.customBaseURL = remoteEndpoint }
            core.customToken = remoteToken
        }
    }
}

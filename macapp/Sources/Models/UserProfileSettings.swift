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

    // MARK: 持久化键（统一见 XJDefaultsKey）

    // MARK: 属性（didSet 持久化）

    /// 用户名（可空）
    var userName: String { didSet { UserDefaults.standard.set(userName, forKey: XJDefaultsKey.profileUserName) } }
    /// 别称（多个，可空数组）
    var aliases: [String] { didSet { UserDefaults.standard.set(aliases, forKey: XJDefaultsKey.profileAliases) } }
    /// 用户身份描述
    var identityDescription: String { didSet { UserDefaults.standard.set(identityDescription, forKey: XJDefaultsKey.profileIdentity) } }
    /// 新人引导是否完成
    public var onboardingCompleted: Bool { didSet { UserDefaults.standard.set(onboardingCompleted, forKey: XJDefaultsKey.profileOnboardingCompleted) } }
    /// AI 功能来源
    var aiSource: AISource { didSet { UserDefaults.standard.set(aiSource.rawValue, forKey: XJDefaultsKey.profileAISource) } }
    /// 远程 API 端点
    var remoteEndpoint: String { didSet { UserDefaults.standard.set(remoteEndpoint, forKey: XJDefaultsKey.profileRemoteEndpoint) } }
    /// 远程 Token（Keychain 持久化，UserDefaults 仅存「已配置」标记，S7）
    var remoteToken: String {
        didSet {
            if remoteToken.isEmpty {
                _ = KeychainStore.shared.delete(forKey: Self.remoteTokenKeychainAccount)
                UserDefaults.standard.removeObject(forKey: XJDefaultsKey.profileRemoteTokenConfigured)
            } else {
                _ = KeychainStore.shared.save(remoteToken, forKey: Self.remoteTokenKeychainAccount)
                UserDefaults.standard.set(true, forKey: XJDefaultsKey.profileRemoteTokenConfigured)
            }
        }
    }
    /// Keychain 中远程 token 的 account 名
    static let remoteTokenKeychainAccount = "xijian.profile.remoteToken"
    /// 远程模型 ID（暂不指定，可空）
    var remoteModelID: String { didSet { UserDefaults.standard.set(remoteModelID, forKey: XJDefaultsKey.profileRemoteModelID) } }
    /// 通知权限状态
    var notificationState: NotificationState { didSet { UserDefaults.standard.set(notificationState.rawValue, forKey: XJDefaultsKey.profileNotificationState) } }
    /// 后台活动权限（防 App Nap）
    public var backgroundActivityEnabled: Bool { didSet { UserDefaults.standard.set(backgroundActivityEnabled, forKey: XJDefaultsKey.profileBackgroundActivity) } }

    // MARK: 初始化（从 UserDefaults 读取，带默认值）

    /// 初始化（internal：单例 shared 供 App 使用；测试可自行创建实例）
    init() {
        let d = UserDefaults.standard
        userName = d.string(forKey: XJDefaultsKey.profileUserName) ?? ""
        aliases = d.stringArray(forKey: XJDefaultsKey.profileAliases) ?? []
        identityDescription = d.string(forKey: XJDefaultsKey.profileIdentity) ?? ""
        onboardingCompleted = d.bool(forKey: XJDefaultsKey.profileOnboardingCompleted)
        aiSource = AISource(rawValue: d.string(forKey: XJDefaultsKey.profileAISource) ?? "") ?? .local
        remoteEndpoint = d.string(forKey: XJDefaultsKey.profileRemoteEndpoint) ?? ""
        // S7 迁移：UserDefaults 旧版明文 token 搬入 Keychain 并删除明文。
        if let legacy = d.string(forKey: "xijian.profile.remoteToken"), !legacy.isEmpty {
            _ = KeychainStore.shared.save(legacy, forKey: Self.remoteTokenKeychainAccount)
            d.removeObject(forKey: "xijian.profile.remoteToken")
            d.set(true, forKey: XJDefaultsKey.profileRemoteTokenConfigured)
        }
        remoteToken = KeychainStore.shared.load(forKey: Self.remoteTokenKeychainAccount) ?? ""
        remoteModelID = d.string(forKey: XJDefaultsKey.profileRemoteModelID) ?? ""
        notificationState = NotificationState(rawValue: d.string(forKey: XJDefaultsKey.profileNotificationState) ?? "") ?? .notDetermined
        backgroundActivityEnabled = d.bool(forKey: XJDefaultsKey.profileBackgroundActivity)
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

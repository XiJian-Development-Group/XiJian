import Foundation

/// macapp 全部 UserDefaults 键的唯一常量表（架构统一：键不再散落各处）。
enum XJDefaultsKey {
    // MARK: 主题（ThemeSettings）
    static let themeAccentHex = "xijian.theme.accentHex"
    static let themeAppearance = "xijian.theme.appearance"
    static let themeBubbleStyle = "xijian.theme.bubbleStyle"
    static let themeFontSize = "xijian.theme.fontSize"
    static let themeCornerRadius = "xijian.theme.cornerRadius"
    static let themeShowTimestamps = "xijian.theme.showTimestamps"
    static let themeBubbleOpacity = "xijian.theme.bubbleOpacity"
    static let themeCustomAccent = "xijian.theme.customAccent"

    // MARK: 聊天（AppViewModel）
    static let chatTemperature = "xijian.chat.temperature"
    static let chatMaxTokens = "xijian.chat.maxTokens"
    static let chatRecall = "xijian.chat.recall"
    static let chatCharacter = "xijian.chat.character"
    static let chatWorld = "xijian.chat.world"

    // MARK: Core（CoreManager）
    static let corePort = "xijian.core.port"
    static let coreUseCustomServer = "xijian.core.useCustomServer"
    static let coreCustomBaseURL = "xijian.core.customBaseURL"
    /// 自定义服务器 token 的「已配置」标记（明文值本身在 Keychain，见 S7）
    static let coreCustomTokenConfigured = "xijian.core.customTokenConfigured"

    // MARK: 用户资料（UserProfileSettings）
    static let profileUserName = "xijian.profile.userName"
    static let profileAliases = "xijian.profile.aliases"
    static let profileIdentity = "xijian.profile.identity"
    static let profileOnboardingCompleted = "xijian.profile.onboardingCompleted"
    static let profileAISource = "xijian.profile.aiSource"
    static let profileRemoteEndpoint = "xijian.profile.remoteEndpoint"
    /// 远程 API token 的「已配置」标记（明文值本身在 Keychain，见 S7）
    static let profileRemoteTokenConfigured = "xijian.profile.remoteTokenConfigured"
    static let profileRemoteModelID = "xijian.profile.remoteModelID"
    static let profileNotificationState = "xijian.profile.notificationState"
    static let profileBackgroundActivity = "xijian.profile.backgroundActivity"

    // MARK: 背景（BackgroundSettings）
    static let backgroundKind = "xijian.background.kind"
    static let backgroundPath = "xijian.background.path"
    static let backgroundBlurred = "xijian.background.blurred"

    // MARK: 首页（HomeView）
    static let pinnedCharacters = "xijian.home.pinnedCharacters"
    static let characterLastChatTime = "xijian.character.lastChatTime"
}

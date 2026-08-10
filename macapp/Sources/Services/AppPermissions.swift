import Foundation
import UserNotifications
import AppKit

/// 权限服务：通知权限、后台活动（防 App Nap）、launchctl 守护注册。
/// 单例使用；通知相关方法需在 MainActor（UI 触发场景）调用。
@MainActor
public final class AppPermissions {

    /// 全局单例
    public static let shared = AppPermissions()

    /// 后台活动 token（防 App Nap；nil = 未开启）
    private var activityToken: NSObjectProtocol?
    /// 是否已注册 launchctl 守护
    private(set) var launchAgentInstalled = false

    private init() {}

    // MARK: - 通知权限

    /// 请求通知权限（options: .alert/.sound/.badge）。返回是否已授权。
    /// 必须在用户触发场景调用（引导页点「允许」时）；App 未签名时系统不弹窗。
    @discardableResult
    func requestNotificationPermission() async -> Bool {
        let center = UNUserNotificationCenter.current()
        do {
            _ = try await center.requestAuthorization(options: [.alert, .sound, .badge])
            return await notificationAuthorized()
        } catch {
            return false
        }
    }

    /// 查询当前通知授权状态（同步到 UserProfileSettings.notificationState）
    func refreshNotificationStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        let state: UserProfileSettings.NotificationState
        // 注意：.ephemeral 仅 iOS/tvOS 可用，macOS 无此状态
        switch settings.authorizationStatus {
        case .authorized, .provisional:
            state = .authorized
        case .denied:
            state = .denied
        case .notDetermined:
            state = .notDetermined
        @unknown default:
            state = .notDetermined
        }
        UserProfileSettings.shared.notificationState = state
    }

    /// 查询当前是否已授权（.authorized / .provisional / .ephemeral 视为已授权）
    private func notificationAuthorized() async -> Bool {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        // 注意：.ephemeral 仅 iOS/tvOS 可用，macOS 无此状态
        return settings.authorizationStatus == .authorized
            || settings.authorizationStatus == .provisional
    }

    /// 打开系统「通知」设置面板（用户拒绝后的引导）
    func openNotificationSystemSettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings") {
            NSWorkspace.shared.open(url)
        }
    }

    // MARK: - 后台活动（防 App Nap）

    /// 开启后台活动：持有 beginActivity（允许空闲睡眠），免疫 App Nap。
    /// 幂等：已有 token 时直接返回。
    func startBackgroundActivity() {
        guard activityToken == nil else { return }
        activityToken = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiatedAllowingIdleSystemSleep],
            reason: "XiJian 后台活动：动态壁纸 / 主动通话 / 桌宠"
        )
    }

    /// 关闭后台活动
    public func stopBackgroundActivity() {
        guard let token = activityToken else { return }
        ProcessInfo.processInfo.endActivity(token)
        activityToken = nil
    }

    /// 根据开关状态同步后台活动（开关 on → start，off → stop）
    public func syncBackgroundActivity(_ enabled: Bool) {
        if enabled {
            startBackgroundActivity()
        } else {
            stopBackgroundActivity()
        }
    }

    // MARK: - launchctl 守护

    /// LaunchAgent plist 路径：~/Library/LaunchAgents/com.xijian.background.plist
    static var launchAgentPlistURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/com.xijian.background.plist")
    }

    /// 注册 launchctl 守护（KeepAlive 仅异常退出时重启，登录自启）。
    /// 先 bootout 清理旧实例再 bootstrap；失败抛错（UI 显示错误，不影响主功能）。
    func installLaunchAgent() throws {
        let plistURL = Self.launchAgentPlistURL
        let fm = FileManager.default
        try fm.createDirectory(at: plistURL.deletingLastPathComponent(), withIntermediateDirectories: true)

        guard let exeURL = Bundle.main.executableURL else {
            throw CocoaError(.fileNoSuchFile)
        }

        let plist: [String: Any] = [
            "Label": "com.xijian.background",
            "ProgramArguments": [exeURL.path],
            "RunAtLoad": false,
            "KeepAlive": ["SuccessfulExit": false],
            "ProcessType": "Background",
            "StandardOutPath": "/tmp/xijian-agent.log",
            "StandardErrorPath": "/tmp/xijian-agent.log",
        ]
        let data = try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
        try data.write(to: plistURL, options: .atomic)

        // 先卸载旧实例（忽略失败），再加载
        try? runLaunchctl(["bootout", "gui/\(getuid())/com.xijian.background"])
        try runLaunchctl(["bootstrap", "gui/\(getuid())", plistURL.path])
        launchAgentInstalled = true
    }

    /// 卸载 launchctl 守护并删除 plist
    func removeLaunchAgent() {
        _ = try? runLaunchctl(["bootout", "gui/\(getuid())/com.xijian.background"])
        let plistURL = Self.launchAgentPlistURL
        try? FileManager.default.removeItem(at: plistURL)
        launchAgentInstalled = false
    }

    /// 查询当前是否已安装守护（以 plist 是否存在为准）
    func refreshLaunchAgentStatus() {
        let plistURL = Self.launchAgentPlistURL
        launchAgentInstalled = FileManager.default.fileExists(atPath: plistURL.path)
    }

    /// 执行 launchctl 命令；非零退出码抛错（含 stderr 信息）
    private func runLaunchctl(_ args: [String]) throws {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        proc.arguments = args
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        try proc.run()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 else {
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let msg = String(data: data, encoding: .utf8) ?? "launchctl error"
            throw NSError(domain: "AppPermissions", code: Int(proc.terminationStatus),
                          userInfo: [NSLocalizedDescriptionKey: msg])
        }
    }
}

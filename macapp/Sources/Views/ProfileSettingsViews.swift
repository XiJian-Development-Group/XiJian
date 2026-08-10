import SwiftUI
import AppKit

// MARK: - 界面背景设置（设置页）

/// 界面背景设置：复用 BackgroundConfigSection，展示背景文件 / 模糊开关。
struct BackgroundSettingsSection: View {
    var body: some View {
        Form {
            Section {
                BackgroundConfigSection()
            } footer: {
                Text(loc("支持图片、视频、GIF，可选择是否模糊。背景将显示在整个窗口的内容层之下。"))
            }
        }
        .formStyle(.grouped)
        .navigationTitle(loc("界面背景"))
    }
}

// MARK: - 用户资料设置（设置页）

/// 用户资料设置：通知 / 后台活动权限、用户名与别称、身份描述、AI 功能来源。
/// 与新人引导第二页共用同一套配置组件（ConfigSections）。
struct ProfileSettingsSection: View {
    @Environment(UserProfileSettings.self) private var profile
    @Environment(BackgroundSettings.self) private var bg
    @State private var launchAgentInstalled = false
    @State private var showLaunchctlError = false
    @State private var launchctlErrorMessage = ""

    var body: some View {
        Form {
            Section {
                PermissionConfigSection()
            } header: {
                Text(loc("请求"))
            } footer: {
                Text(loc("通知权限用于主动发起通话；后台活动权限用于动态壁纸、主动通话、桌宠等功能。拒绝不会影响基本功能。"))
            }

            Section {
                Toggle(loc("后台活动"), isOn: Bindable(profile).backgroundActivityEnabled)
                    .onChange(of: profile.backgroundActivityEnabled) { _, enabled in
                        AppPermissions.shared.syncBackgroundActivity(enabled)
                    }
                LabeledContent(loc("launchctl 守护进程"), value: launchAgentInstalled ? loc("已安装") : loc("未安装"))
                if launchAgentInstalled {
                    Button(role: .destructive) {
                        AppPermissions.shared.removeLaunchAgent()
                        launchAgentInstalled = false
                    } label: {
                        Label(loc("卸载守护进程"), systemImage: "xmark.circle")
                    }
                } else {
                    Button {
                        do {
                            try AppPermissions.shared.installLaunchAgent()
                            launchAgentInstalled = true
                        } catch {
                            launchctlErrorMessage = error.localizedDescription
                            showLaunchctlError = true
                        }
                    } label: {
                        Label(loc("安装守护进程"), systemImage: "shield.lefthalf.filled")
                    }
                }
            } header: {
                Text(loc("后台守护"))
            } footer: {
                Text(loc("后台活动开启时防止 App 被系统节能暂停；守护进程可在异常退出时自动重启。"))
            }

            Section {
                IdentityConfigSection()
            } header: {
                Text(loc("用户名与别称"))
            }

            Section {
                IdentityDescriptionSection()
            } header: {
                Text(loc("用户身份描述"))
            } footer: {
                Text(loc("本字段用于向角色描述你的身份，但可能不在所有情况下生效。"))
            }

            Section {
                AISourceConfigSection()
            } header: {
                Text(loc("AI 功能来源"))
            } footer: {
                Text(loc("本地使用内置 Core；远程使用自定义 API 端点与 Token。"))
            }
        }
        .formStyle(.grouped)
        .navigationTitle(loc("用户资料"))
        .onAppear {
            AppPermissions.shared.refreshLaunchAgentStatus()
            launchAgentInstalled = AppPermissions.shared.launchAgentInstalled
            Task {
                await AppPermissions.shared.refreshNotificationStatus()
            }
        }
        .alert(loc("出错了"), isPresented: $showLaunchctlError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(launchctlErrorMessage)
        }
    }
}

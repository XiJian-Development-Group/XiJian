import SwiftUI
import UniformTypeIdentifiers
import AppKit

// MARK: - 引导页 / 设置页复用配置块
//
// 新人引导第二页的五个配置块独立成组件，设置页可直接复用：
// 均通过 @Environment 读取 UserProfileSettings / BackgroundSettings。

// MARK: - UI 背景配置

/// UI 背景配置：选择背景文件（图片 / GIF / 视频）、模糊开关、移除
struct BackgroundConfigSection: View {
    @Environment(BackgroundSettings.self) private var bg

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Button {
                    pickBackgroundFile()
                } label: {
                    Label(loc("选择背景文件"), systemImage: "photo.on.rectangle.angled")
                }
                if bg.kind != .none {
                    Button(role: .destructive) {
                        bg.clear()
                    } label: {
                        Label(loc("移除"), systemImage: "trash")
                    }
                }
            }
            if bg.kind != .none, let url = bg.fileURL {
                Text(url.lastPathComponent)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Toggle(loc("模糊背景"), isOn: Bindable(bg).isBlurred)
                .disabled(bg.kind == .none)
        }
    }

    /// 打开文件选择面板，按扩展名推断背景类型并应用
    private func pickBackgroundFile() {
        let panel = NSOpenPanel()
        panel.title = loc("选择背景文件")
        panel.allowedContentTypes = [
            .image, .gif, .mpeg4Movie, .quickTimeMovie,
            UTType(filenameExtension: "webp") ?? .image,
        ]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        if panel.runModal() == .OK, let url = panel.url {
            bg.apply(fileURL: url)
        }
    }
}

// MARK: - 权限配置

/// 权限配置：通知权限（请求 / 去设置开启）+ 后台活动开关
struct PermissionConfigSection: View {
    @Environment(UserProfileSettings.self) private var profile

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // 通知权限
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(loc("通知权限"))
                        .font(.body.weight(.medium))
                    Text(loc("用于主动发起通话"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                notificationTrailing
            }

            Divider()

            // 后台活动权限
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(loc("后台活动权限"))
                        .font(.body.weight(.medium))
                    Text(loc("用于动态壁纸、主动通话、桌宠等功能"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Toggle("", isOn: Bindable(profile).backgroundActivityEnabled)
                    .labelsHidden()
            }
        }
    }

    /// 通知权限尾随控件：已授权 → 绿色状态；已拒绝 → 去设置开启；未请求 → 允许
    @ViewBuilder
    private var notificationTrailing: some View {
        switch profile.notificationState {
        case .authorized:
            Text(loc("已授权"))
                .font(.caption)
                .foregroundStyle(.green)
        case .denied:
            Button(loc("去设置开启")) {
                AppPermissions.shared.openNotificationSystemSettings()
            }
            .font(.caption)
        case .notDetermined:
            Button(loc("允许")) {
                requestNotification()
            }
            .font(.caption.weight(.medium))
        }
    }

    /// 请求通知权限并刷新状态（拒绝后 UI 自动切换为「去设置开启」）
    private func requestNotification() {
        Task {
            _ = await AppPermissions.shared.requestNotificationPermission()
            await AppPermissions.shared.refreshNotificationStatus()
        }
    }
}

// MARK: - 用户名与别称

/// 用户名与别称配置：主名称 + 多个别称（可添加 / 编辑 / 删除）
struct IdentityConfigSection: View {
    @Environment(UserProfileSettings.self) private var profile

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField(loc("你的名字"), text: Bindable(profile).userName)
                .textFieldStyle(.roundedBorder)

            ForEach(profile.aliases.indices, id: \.self) { index in
                HStack(spacing: 8) {
                    TextField(loc("别称"), text: Binding(
                        get: { alias(at: index) },
                        set: { profile.updateAlias($0, at: index) }
                    ))
                    .textFieldStyle(.roundedBorder)
                    Button {
                        profile.removeAlias(at: index)
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }

            Button {
                profile.appendEmptyAlias()
            } label: {
                Label(loc("添加别称"), systemImage: "plus.circle")
                    .font(.caption)
            }
            .buttonStyle(.plain)
        }
    }

    /// 越界安全读取别称（列表行在删除动画期间可能短暂持有旧索引）
    private func alias(at index: Int) -> String {
        profile.aliases.indices.contains(index) ? profile.aliases[index] : ""
    }
}

// MARK: - 用户身份描述

/// 用户身份描述：多行文本编辑（用于向角色描述身份）
struct IdentityDescriptionSection: View {
    @Environment(UserProfileSettings.self) private var profile

    var body: some View {
        TextEditor(text: Bindable(profile).identityDescription)
            .font(.body)
            .frame(minHeight: 80)
            .padding(6)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color(.textBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
            )
    }
}

// MARK: - AI 功能来源

/// AI 功能来源：本地 Core / 远程 API（端点、Token、模型 ID、帮助文档）
struct AISourceConfigSection: View {
    @Environment(UserProfileSettings.self) private var profile

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("", selection: Bindable(profile).aiSource) {
                ForEach(UserProfileSettings.AISource.allCases) { source in
                    Text(source.displayName).tag(source)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            switch profile.aiSource {
            case .local:
                Label(loc("使用本地 Core；模型下载功能即将开放"), systemImage: "internaldrive")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            case .remote:
                VStack(alignment: .leading, spacing: 8) {
                    TextField(loc("API 端点"), text: Bindable(profile).remoteEndpoint)
                        .textFieldStyle(.roundedBorder)
                    SecureField(loc("Token"), text: Bindable(profile).remoteToken)
                        .textFieldStyle(.roundedBorder)
                    TextField(loc("模型 ID（暂不指定）"), text: Bindable(profile).remoteModelID)
                        .textFieldStyle(.roundedBorder)
                    Button {
                        if let url = URL(string: "https://xijian.wiki.skyc8266.uk") {
                            NSWorkspace.shared.open(url)
                        }
                    } label: {
                        Label(loc("查看帮助文档"), systemImage: "questionmark.circle")
                            .font(.caption)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

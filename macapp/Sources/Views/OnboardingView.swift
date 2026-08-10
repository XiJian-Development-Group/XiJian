import SwiftUI
import AppKit

/// 新人引导（首次启动 + 设置中再次查看）。
/// 三页：欢迎 → 基础配置 → 烟花结束页；底部左右两键翻页。
/// 主界面 ContentView 根据 `UserProfileSettings.onboardingCompleted` 决定是否展示。
struct OnboardingView: View {
    /// 完成引导后的回调（由主界面切换回主内容；设置页重新查看时传空即可）
    var onFinish: () -> Void = {}

    @Environment(UserProfileSettings.self) private var profile
    @Environment(BackgroundSettings.self) private var bg
    @State private var page = 0

    var body: some View {
        ZStack {
            // 背景（引导页也应用 UI 背景，弱化保证文字可读）
            BackgroundLayerView()
                .opacity(0.5)

            VStack(spacing: 0) {
                // 顶部：进度指示（三颗圆点）
                header
                Spacer()
                pageContent
                    .id(page)   // 换页重建触发转场
                    .transition(.asymmetric(
                        insertion: .move(edge: .trailing).combined(with: .opacity),
                        removal: .move(edge: .leading).combined(with: .opacity)
                    ))
                Spacer()
                bottomBar
            }
            .padding(.horizontal, 48)
            .padding(.vertical, 32)
        }
        .frame(minWidth: 720, minHeight: 560)
        .animation(.spring(response: 0.4, dampingFraction: 1.0), value: page)
    }

    // MARK: 头部

    /// 顶部进度指示：三颗圆点，当前页高亮放大
    private var header: some View {
        HStack {
            Spacer()
            HStack(spacing: 8) {
                ForEach(0..<3, id: \.self) { i in
                    Circle()
                        .fill(i == page ? Color.accentColor : Color.secondary.opacity(0.3))
                        .frame(width: 8, height: 8)
                        .scaleEffect(i == page ? 1.2 : 1)
                        .animation(.spring(response: 0.3, dampingFraction: 1.0), value: page)
                }
            }
            Spacer()
        }
    }

    // MARK: 页面内容

    @ViewBuilder
    private var pageContent: some View {
        switch page {
        case 0: WelcomePageView()
        case 1: ConfigPageView()
        default: FinalPageView()
        }
    }

    // MARK: 底部左右按钮

    /// 底部按钮栏：左 = 返回上一页（第一页隐藏），右 = 下一页 / 开始使用
    private var bottomBar: some View {
        HStack {
            if page > 0 {
                Button {
                    withAnimation(.spring(response: 0.4, dampingFraction: 1.0)) { page -= 1 }
                } label: {
                    Label(loc("返回"), systemImage: "chevron.left")
                }
                .xjPrimaryButton(prominent: false)
                .frame(width: 120)
            } else {
                Color.clear.frame(width: 120)
            }
            Spacer()
            Button {
                advance()
            } label: {
                if page >= 2 {
                    Label(loc("开始使用"), systemImage: "sparkles")
                } else {
                    Label(loc("下一页"), systemImage: "chevron.right")
                        .labelStyle(.titleAndIcon)
                }
            }
            .xjPrimaryButton()
            .frame(width: 140)
        }
    }

    // MARK: 翻页 / 完成

    private func advance() {
        if page < 2 {
            withAnimation(.spring(response: 0.4, dampingFraction: 1.0)) { page += 1 }
        } else {
            complete()
        }
    }

    /// 完成引导：标记完成 + 应用 AI 来源到 Core + 同步后台活动开关 + 回调
    private func complete() {
        profile.onboardingCompleted = true
        profile.applyAISourceToCore()
        AppPermissions.shared.syncBackgroundActivity(profile.backgroundActivityEnabled)
        onFinish()
    }
}

// MARK: - 第一页：欢迎

/// 欢迎页：应用图标 + 标语 + 欢迎文案
private struct WelcomePageView: View {
    var body: some View {
        VStack(spacing: XJSpacing.lg) {
            // 图标：xijian_icon（主代理已加入 Assets 资源）
            if let icon = NSImage(named: "xijian_icon") {
                Image(nsImage: icon)
                    .resizable()
                    .interpolation(.high)
                    .frame(width: 128, height: 128)
                    .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
                    .shadow(color: .black.opacity(0.18), radius: 24, y: 12)
                    .xjFadeUp()
            }
            Text("XiJian by XiJian Development Group")
                .font(.caption)
                .foregroundStyle(.secondary)
                .xjFadeUp(delay: 0.1)
            Text(xj: "你好，欢迎来到隙间，希望你能在这里找到一段故事～如果遇到问题，可以随时与我们联系：support@mail.skyc8266.uk")
                .font(.body)
                .foregroundStyle(.primary)
                .multilineTextAlignment(.center)
                .lineSpacing(6)
                .frame(maxWidth: 460)
                .xjFadeUp(delay: 0.2)
        }
    }
}

// MARK: - 第二页：基础配置

/// 基础配置页：UI 背景 / 请求权限 / 用户名与别称 / 身份描述 / AI 来源
/// 五个配置块来自 ConfigSections.swift（设置页复用同一套组件）
private struct ConfigPageView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            Text(loc("基础配置"))
                .font(.title2.bold())
            Text(loc("在开始之前，请完成下列设置，这样也许能让你获得更好的体验。"))
                .font(.caption)
                .foregroundStyle(.secondary)

            ScrollView {
                VStack(alignment: .leading, spacing: XJSpacing.md) {
                    XJSettingRow(title: loc("UI 背景"), subtitle: loc("图片、视频、GIF 均支持，可选择是否模糊")) {
                        BackgroundConfigSection()
                    }
                    .xjCard()

                    XJSettingRow(title: loc("请求"), subtitle: loc("可以拒绝，拒绝不会影响基本功能")) {
                        PermissionConfigSection()
                    }
                    .xjCard()

                    XJSettingRow(title: loc("用户名与别称"), subtitle: loc("别称可多个，也可不填")) {
                        IdentityConfigSection()
                    }
                    .xjCard()

                    XJSettingRow(title: loc("用户身份描述"), subtitle: loc("用于向角色描述你的身份，但可能不在所有情况下生效")) {
                        IdentityDescriptionSection()
                    }
                    .xjCard()

                    XJSettingRow(title: loc("AI 功能来源")) {
                        AISourceConfigSection()
                    }
                    .xjCard()
                }
            }
            .frame(maxHeight: 380)
        }
        .frame(maxWidth: 640)
    }
}

// MARK: - 第三页：结束（烟花）

/// 结束页：烟花动效 + 结束语
private struct FinalPageView: View {
    var body: some View {
        ZStack {
            FireworksView()
                .ignoresSafeArea()
            VStack(spacing: XJSpacing.md) {
                Text(loc("那么，接下来，是属于你的故事啦～"))
                    .font(.title2.bold())
                    .multilineTextAlignment(.center)
                    .shadow(color: .black.opacity(0.4), radius: 8)
                    .xjFadeUp(delay: 0.5)
            }
        }
    }
}

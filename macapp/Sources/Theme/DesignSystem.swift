import SwiftUI

// MARK: - 间距 / 圆角

/// 间距节奏常量（Apple 设计规范：4 / 8 / 16 / 24 / 48）
enum XJSpacing {
    /// 4pt — 极小间距（图标与文字、徽标内部）
    static let xs: CGFloat = 4
    /// 8pt — 小间距（行内元素之间）
    static let sm: CGFloat = 8
    /// 16pt — 中间距（卡片内分组、控件间距）
    static let md: CGFloat = 16
    /// 24pt — 大间距（区块之间、页面边距）
    static let lg: CGFloat = 24
    /// 48pt — 超大间距（页面级留白）
    static let xl: CGFloat = 48
}

/// 圆角节奏常量（小控件 10 / 卡片 16 / 大面板 24，按钮用胶囊 980）
enum XJRadius {
    /// 小控件圆角
    static let small: CGFloat = 10
    /// 卡片圆角
    static let card: CGFloat = 16
    /// 大面板圆角
    static let panel: CGFloat = 24
}

// MARK: - 卡片修饰器

/// 毛玻璃圆角卡片：regularMaterial 背景 + 极细描边
struct XJCardModifier: ViewModifier {
    /// 卡片内边距
    var padding: CGFloat = 16

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: XJRadius.card, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.06), lineWidth: 1)
            )
    }
}

extension View {
    /// 卡片样式：毛玻璃圆角卡片（设置项 / 引导页配置块通用）
    func xjCard(padding: CGFloat = 16) -> some View {
        modifier(XJCardModifier(padding: padding))
    }
}

// MARK: - 按钮样式

/// 胶囊按钮：prominent 为主色实底（白字），否则毛玻璃描边。
/// 按下有 0.97 缩放反馈（critically damped spring）。
struct XJPrimaryButtonStyle: ButtonStyle {
    /// 是否为主按钮（主色实底）
    var prominent: Bool = true

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.medium))
            .padding(.horizontal, 20)
            .padding(.vertical, 10)
            .foregroundStyle(prominent ? Color.white : Color.primary)
            .background(
                prominent
                    ? AnyShapeStyle(Color.accentColor)
                    : AnyShapeStyle(.regularMaterial),
                in: Capsule()
            )
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.spring(response: 0.3, dampingFraction: 1.0), value: configuration.isPressed)
    }
}

extension View {
    /// 应用胶囊按钮样式（.plain 基础上带按下缩放反馈）
    func xjPrimaryButton(prominent: Bool = true) -> some View {
        buttonStyle(XJPrimaryButtonStyle(prominent: prominent))
    }
}

// MARK: - 进入动效

/// 渐显上移动效（opacity 0→1 + translateY 20→0，0.35s easeOut）
struct XJFadeUpModifier: ViewModifier {
    /// 延迟（秒），用于错落进入
    var delay: Double = 0
    @State private var appeared = false

    func body(content: Content) -> some View {
        content
            .opacity(appeared ? 1 : 0)
            .offset(y: appeared ? 0 : 20)
            .onAppear {
                withAnimation(.easeOut(duration: 0.35).delay(delay)) { appeared = true }
            }
    }
}

extension View {
    /// 渐显上移动效（Apple 风格进入）
    func xjFadeUp(delay: Double = 0) -> some View {
        modifier(XJFadeUpModifier(delay: delay))
    }
}

// MARK: - 设置行样式

/// 设置行容器：标题 + 可选副标题在上，内容控件在下（表单行统一排版）。
/// 用法：`XJSettingRow(title: "标题", subtitle: "副标题") { Toggle(...) }`
struct XJSettingRow<Content: View>: View {
    /// 标题
    let title: String
    /// 副标题（可选）
    var subtitle: String? = nil
    /// 行内内容（trailing 闭包）
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                if let subtitle {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

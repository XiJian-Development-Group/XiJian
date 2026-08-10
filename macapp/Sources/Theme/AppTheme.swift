import SwiftUI
import Observation

// MARK: - 颜色扩展

extension Color {
    /// 从十六进制字符串（#RRGGBB 或 RRGGBB）创建颜色
    init?(hex: String) {
        var value = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.hasPrefix("#") { value.removeFirst() }
        guard value.count == 6, let int = Int(value, radix: 16) else { return nil }
        self.init(
            red: Double((int >> 16) & 0xFF) / 255.0,
            green: Double((int >> 8) & 0xFF) / 255.0,
            blue: Double(int & 0xFF) / 255.0
        )
    }

    /// 转十六进制字符串
    var hexString: String? {
        let ns = NSColor(self).usingColorSpace(.sRGB) ?? NSColor(self)
        return String(format: "#%02X%02X%02X",
                      Int(round(ns.redComponent * 255)),
                      Int(round(ns.greenComponent * 255)),
                      Int(round(ns.blueComponent * 255)))
    }
}

// MARK: - 主题设置

/// 主题设置 — 高度个性化：主题色、深浅色、气泡样式、字体大小等。
/// UserDefaults 持久化，通过 @Observable 注入环境。
@Observable
public final class ThemeSettings {
    public static let shared = ThemeSettings()

    // MARK: 枚举

    /// 外观模式
    enum AppearanceMode: String, CaseIterable, Identifiable {
        case system = "跟随系统"
        case light = "浅色"
        case dark = "深色"
        var id: String { rawValue }
        /// 本地化显示名（rawValue 为持久化存储值，展示时用 displayName）
        var displayName: String {
            switch self {
            case .system: return loc("跟随系统")
            case .light: return loc("浅色")
            case .dark: return loc("深色")
            }
        }
    }

    /// 气泡样式
    enum BubbleStyle: String, CaseIterable, Identifiable {
        case rounded = "圆角"
        case outlined = "描边"
        case flat = "扁平"
        var id: String { rawValue }
        /// 本地化显示名（rawValue 为持久化存储值，展示时用 displayName）
        var displayName: String {
            switch self {
            case .rounded: return loc("圆角")
            case .outlined: return loc("描边")
            case .flat: return loc("扁平")
            }
        }
    }

    /// 主题色预设
    struct AccentPreset: Identifiable {
        let id: String
        let name: String
        let hex: String
    }

    static let presets: [AccentPreset] = [
        AccentPreset(id: "violet", name: "隙间紫", hex: "#8B5CF6"),
        AccentPreset(id: "sakura", name: "樱花粉", hex: "#F472B6"),
        AccentPreset(id: "mint", name: "薄荷绿", hex: "#34D399"),
        AccentPreset(id: "ocean", name: "海洋蓝", hex: "#3B82F6"),
        AccentPreset(id: "sunset", name: "夕阳橙", hex: "#F97316"),
        AccentPreset(id: "charcoal", name: "炭黑", hex: "#475569"),
        AccentPreset(id: "rose", name: "玫瑰红", hex: "#E11D48"),
        AccentPreset(id: "gold", name: "鎏金", hex: "#D97706"),
    ]

    // MARK: 持久化键（统一见 XJDefaultsKey）

    // MARK: 属性（didSet 持久化）

    /// 主题色（十六进制）
    var accentHex: String {
        didSet { UserDefaults.standard.set(accentHex, forKey: XJDefaultsKey.themeAccentHex) }
    }

    /// 是否使用自定义主题色（覆盖预设）
    var useCustomAccent: Bool {
        didSet { UserDefaults.standard.set(useCustomAccent, forKey: XJDefaultsKey.themeCustomAccent) }
    }

    /// 外观模式
    var appearanceMode: AppearanceMode {
        didSet { UserDefaults.standard.set(appearanceMode.rawValue, forKey: XJDefaultsKey.themeAppearance) }
    }

    /// 气泡样式
    var bubbleStyle: BubbleStyle {
        didSet { UserDefaults.standard.set(bubbleStyle.rawValue, forKey: XJDefaultsKey.themeBubbleStyle) }
    }

    /// 基础字号
    var fontSize: Double {
        didSet {
            let clamped = min(max(fontSize, 10), 28)
            if fontSize != clamped { fontSize = clamped; return }
            UserDefaults.standard.set(fontSize, forKey: XJDefaultsKey.themeFontSize)
        }
    }

    /// 气泡圆角
    var cornerRadius: Double {
        didSet {
            let clamped = min(max(cornerRadius, 0), 24)
            if cornerRadius != clamped { cornerRadius = clamped; return }
            UserDefaults.standard.set(cornerRadius, forKey: XJDefaultsKey.themeCornerRadius)
        }
    }

    /// 气泡透明度（0.5-1.0）
    var bubbleOpacity: Double {
        didSet {
            let clamped = min(max(bubbleOpacity, 0.4), 1.0)
            if bubbleOpacity != clamped { bubbleOpacity = clamped; return }
            UserDefaults.standard.set(bubbleOpacity, forKey: XJDefaultsKey.themeBubbleOpacity)
        }
    }

    /// 是否显示时间戳
    var showTimestamps: Bool {
        didSet { UserDefaults.standard.set(showTimestamps, forKey: XJDefaultsKey.themeShowTimestamps) }
    }

    // MARK: 初始化

    private init() {
        let defaults = UserDefaults.standard
        accentHex = defaults.string(forKey: XJDefaultsKey.themeAccentHex) ?? ThemeSettings.presets[0].hex
        useCustomAccent = defaults.bool(forKey: XJDefaultsKey.themeCustomAccent)
        appearanceMode = AppearanceMode(rawValue: defaults.string(forKey: XJDefaultsKey.themeAppearance) ?? "") ?? .system
        bubbleStyle = BubbleStyle(rawValue: defaults.string(forKey: XJDefaultsKey.themeBubbleStyle) ?? "") ?? .rounded
        fontSize = defaults.object(forKey: XJDefaultsKey.themeFontSize) as? Double ?? 15
        cornerRadius = defaults.object(forKey: XJDefaultsKey.themeCornerRadius) as? Double ?? 14
        bubbleOpacity = defaults.object(forKey: XJDefaultsKey.themeBubbleOpacity) as? Double ?? 0.92
        showTimestamps = defaults.object(forKey: XJDefaultsKey.themeShowTimestamps) as? Bool ?? false
    }

    // MARK: 计算属性

    /// 当前主题色
    var accentColor: Color {
        Color(hex: accentHex) ?? .accentColor
    }

    /// 深色模式下主题色的亮色变体
    var accentBright: Color {
        guard let base = Color(hex: accentHex) else { return .accentColor }
        let ns = NSColor(base).usingColorSpace(.sRGB) ?? NSColor(base)
        let h = max(ns.redComponent, ns.greenComponent, ns.blueComponent)
        let scale = 0.45 + h * 0.6
        return Color(
            red: min(ns.redComponent * scale + 0.12, 1),
            green: min(ns.greenComponent * scale + 0.12, 1),
            blue: min(ns.blueComponent * scale + 0.12, 1)
        )
    }

    /// 用户气泡背景色
    var userBubbleColor: Color {
        switch bubbleStyle {
        case .outlined:
            return accentColor.opacity(bubbleOpacity * 0.15)
        default:
            return accentColor.opacity(bubbleOpacity)
        }
    }

    /// 助手气泡背景色
    var assistantBubbleColor: Color {
        Color(.controlBackgroundColor).opacity(bubbleOpacity)
    }

    /// 用户文字颜色
    var userTextColor: Color {
        bubbleStyle == .outlined ? .primary : .white
    }

    /// 应用外观
    var colorScheme: ColorScheme? {
        switch appearanceMode {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }
}

// MARK: - 视图扩展

extension View {
    /// 应用主题外观（preferredColorScheme）
    func themeColorScheme(_ theme: ThemeSettings) -> some View {
        preferredColorScheme(theme.colorScheme)
    }
}

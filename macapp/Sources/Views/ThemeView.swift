import SwiftUI

/// 主题管理视图：外观、主题色预设、气泡样式、字号、圆角、透明度、时间戳
struct ThemeView: View {
    @State private var theme = ThemeSettings.shared
    @State private var showCustomColor = false
    @State private var customHex: String = ""

    var body: some View {
        Form {
            Section("外观") {
                Picker("模式", selection: $theme.appearanceMode) {
                    ForEach(ThemeSettings.AppearanceMode.allCases) { mode in
                        Text(mode.displayName).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
            }

            Section("主题色") {
                Toggle("使用自定义颜色", isOn: $theme.useCustomAccent)
                if theme.useCustomAccent {
                    HStack {
                        TextField("十六进制颜色", text: $theme.accentHex)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 160)
                        ColorPicker("选色器", selection: accentColorBinding, supportsOpacity: false)
                            .labelsHidden()
                    }
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 60))], spacing: 12) {
                        ForEach(ThemeSettings.presets) { preset in
                            VStack(spacing: 4) {
                                Circle()
                                    .fill(Color(hex: preset.hex) ?? .gray)
                                    .frame(width: 32, height: 32)
                                    .overlay(
                                        Circle()
                                            .stroke(theme.accentHex == preset.hex ? Color.primary : Color.clear, lineWidth: 2)
                                    )
                                    .onTapGesture { theme.accentHex = preset.hex }
                                Text(preset.name)
                                    .font(.caption)
                            }
                        }
                    }
                }
            }

            Section("气泡样式") {
                Picker("样式", selection: $theme.bubbleStyle) {
                    ForEach(ThemeSettings.BubbleStyle.allCases) { style in
                        Text(style.displayName).tag(style)
                    }
                }
                .pickerStyle(.segmented)
                HStack {
                    Text("圆角")
                    Slider(value: $theme.cornerRadius, in: 0...24, step: 1)
                    Text("\(Int(theme.cornerRadius))").frame(width: 30)
                }
                HStack {
                    Text("透明度")
                    Slider(value: $theme.bubbleOpacity, in: 0.4...1.0)
                    Text(String(format: "%.0f%%", theme.bubbleOpacity * 100)).frame(width: 40)
                }
            }

            Section("字体") {
                HStack {
                    Text("字号")
                    Slider(value: $theme.fontSize, in: 10...28, step: 1)
                    Text("\(Int(theme.fontSize))").frame(width: 30)
                }
            }

            Section("其他") {
                Toggle("显示时间戳", isOn: $theme.showTimestamps)
            }

            Section("预览") {
                preview
            }
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private var preview: some View {
        VStack(spacing: 8) {
            HStack {
                Text("用户消息")
                    .padding(8)
                    .background(theme.userBubbleColor)
                    .foregroundColor(theme.userTextColor)
                    .clipShape(RoundedRectangle(cornerRadius: theme.cornerRadius))
                Spacer()
            }
            HStack {
                Spacer()
                Text("助手回复")
                    .padding(8)
                    .background(theme.assistantBubbleColor)
                    .clipShape(RoundedRectangle(cornerRadius: theme.cornerRadius))
            }
        }
        .font(.system(size: theme.fontSize))
    }

    /// ColorPicker 用的 binding：从 hex 字符串与 Color 互转
    private var accentColorBinding: Binding<Color> {
        Binding(
            get: { Color(hex: theme.accentHex) ?? .accentColor },
            set: { newColor in
                theme.accentHex = newColor.hexString ?? theme.accentHex
            }
        )
    }
}

import SwiftUI

/// 环形进度条组件 — 展示单个状态维度的值 / 上限，点击触发回调。
/// 用 Path 画环形，不依赖第三方库；颜色由调用方注入（跟随主题）。
struct CharacterStatRing: View {
    let title: String
    let icon: String
    let value: Double
    let max: Double
    let color: Color
    var action: () -> Void = {}

    /// 归一化进度（0...1，防越界）
    private var progress: Double {
        guard max > 0 else { return 0 }
        return Swift.min(Swift.max(value / max, 0), 1)
    }

    var body: some View {
        Button(action: action) {
            VStack(spacing: 5) {
                ZStack {
                    // 底环
                    Circle()
                        .stroke(color.opacity(0.15), lineWidth: 9)
                    // 进度环（从 12 点方向顺时针）
                    Circle()
                        .trim(from: 0, to: progress)
                        .stroke(
                            AngularGradient(
                                colors: [color.opacity(0.55), color],
                                center: .center,
                                startAngle: .degrees(0),
                                endAngle: .degrees(360)
                            ),
                            style: StrokeStyle(lineWidth: 9, lineCap: .round)
                        )
                        .rotationEffect(.degrees(-90))
                        .animation(.snappy(duration: 0.25), value: progress)
                    // 中央：图标 + 数值
                    VStack(spacing: 1) {
                        Image(systemName: icon)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(color)
                        Text(String(format: "%.0f", value))
                            .font(.system(size: 16, weight: .semibold))
                            .monospacedDigit()
                            .contentTransition(.numericText())
                    }
                }
                .frame(width: 66, height: 66)

                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(loc("上限 %.0f", max))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// 状态调节弹窗 — Slider 调整个别维度并提交 PATCH
struct CharacterStatSliderSheet: View {
    @Bindable var viewModel: CharacterViewModel
    let characterID: String
    let dimension: CharacterStatusDimension
    let max: Double

    @Environment(\.dismiss) private var dismiss
    @State private var value: Double
    @State private var isSubmitting = false
    @State private var showError = false
    @State private var errorMessage = ""

    /// 初始值取当前状态；状态缺失时从 0 开始
    init(viewModel: CharacterViewModel, characterID: String, dimension: CharacterStatusDimension) {
        self.viewModel = viewModel
        self.characterID = characterID
        self.dimension = dimension
        self.max = viewModel.state?.summary?.max(for: dimension) ?? 100
        _value = State(initialValue: viewModel.state?.summary?.value(for: dimension) ?? 0)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(loc("调整%@", dimension.displayName))
                .font(.title3)
                .bold()

            HStack(spacing: 10) {
                Image(systemName: dimension.iconName)
                    .foregroundStyle(.secondary)
                Slider(value: $value, in: 0...max, step: 1)
                Text(String(format: "%.0f / %.0f", value, max))
                    .font(.body)
                    .monospacedDigit()
                    .frame(width: 90, alignment: .trailing)
            }

            Text(loc("保存后通过状态接口提交，Core 会进行钳制与日志记录。"))
                .font(.caption)
                .foregroundStyle(.tertiary)

            HStack {
                Spacer()
                Button(loc("取消")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(loc("保存")) {
                    Task { await save() }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(isSubmitting)
            }
        }
        .padding(20)
        .frame(width: 380)
        .alert(loc("调整失败"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
    }

    private func save() async {
        isSubmitting = true
        defer { isSubmitting = false }
        let ok = await viewModel.adjustState(dimension, to: value)
        if ok {
            dismiss()
        } else {
            errorMessage = viewModel.errorMessage ?? loc("状态调整失败，请稍后重试。")
            showError = true
        }
    }
}

// MARK: - 预览

#Preview("CharacterStatRing") {
    HStack(spacing: 24) {
        CharacterStatRing(title: loc("饱食"), icon: "fork.knife", value: 72, max: 100, color: .orange) {}
        CharacterStatRing(title: loc("健康"), icon: "heart.fill", value: 35, max: 100, color: .red) {}
    }
    .padding()
}

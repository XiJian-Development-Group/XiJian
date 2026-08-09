import SwiftUI
import XiJianKit

/// Core 状态指示灯
struct StatusIndicatorView: View {
    @Environment(CoreManager.self) private var core

    var body: some View {
        HStack(spacing: 8) {
            circle
            Text(statusText)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            if isBusy {
                ProgressView()
                    .controlSize(.small)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.bar)
    }

    private var isBusy: Bool {
        switch core.state {
        case .extracting, .starting: return true
        default: return false
        }
    }

    private var circle: some View {
        Circle()
            .fill(color)
            .frame(width: 10, height: 10)
            .overlay(Circle().stroke(.white.opacity(0.4), lineWidth: 0.5))
            .shadow(color: color.opacity(0.6), radius: 3)
    }

    private var color: Color {
        switch core.state {
        case .stopped: return .gray
        case .extracting, .starting: return .orange
        case .running: return .green
        case .customServer: return .blue
        case .error: return .red
        }
    }

    private var statusText: String {
        switch core.state {
        case .stopped: return loc("Core 未运行")
        case .extracting: return loc("正在复制 Core 组件...")
        case .starting: return loc("Core 启动中...")
        case .running(let port): return loc("Core 运行中 · 端口 %lld", port)
        case .customServer: return loc("使用自定义服务器")
        case .error(let message): return loc("Core 异常：%@", message)
        }
    }
}

#Preview {
    StatusIndicatorView()
        .environment(CoreManager.shared)
}

import SwiftUI
import AppKit

/// 全局快捷键监听：cmd + 鼠标滚轮 → 调 ThemeSettings.fontSize。
public struct ScrollZoomModifier: ViewModifier {
    @Bindable public var theme: ThemeSettings
    public let min: Double
    public let max: Double
    public let step: Double

    public init(theme: ThemeSettings, min: Double = 10, max: Double = 28, step: Double = 1) {
        self.theme = theme
        self.min = min
        self.max = max
        self.step = step
    }

    public func body(content: Content) -> some View {
        content.background(
            ScrollWheelHandler { direction in
                let flags = NSEvent.modifierFlags
                if flags.contains(.command) {
                    if direction > 0 {
                        theme.fontSize = Swift.min(theme.fontSize + step, max)
                    } else {
                        theme.fontSize = Swift.max(theme.fontSize - step, min)
                    }
                }
            }
        )
    }
}

extension View {
    /// 启用 cmd+滚轮调字号
    public func scrollZoomFont(_ theme: ThemeSettings) -> some View {
        modifier(ScrollZoomModifier(theme: theme))
    }
}

/// 通过 NSEvent.addLocalMonitorForEvents 监听鼠标滚轮事件
private struct ScrollWheelHandler: NSViewRepresentable {
    let onScroll: (Int) -> Void

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            context.coordinator.start(onScroll: onScroll)
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        context.coordinator.onScroll = onScroll
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var monitor: Any?
        var onScroll: ((Int) -> Void)?

        func start(onScroll: @escaping (Int) -> Void) {
            self.onScroll = onScroll
            if monitor == nil {
                monitor = NSEvent.addLocalMonitorForEvents(matching: .scrollWheel) { event in
                    if event.modifierFlags.contains(.command) {
                        let dy = event.scrollingDeltaY
                        if abs(dy) > 0.5 {
                            onScroll(dy > 0 ? 1 : -1)
                        }
                    }
                    return event
                }
            }
        }

        deinit {
            if let m = monitor { NSEvent.removeMonitor(m) }
        }
    }
}

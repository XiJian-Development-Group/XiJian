import SwiftUI
import AppKit
import AVFoundation

/// 全局背景层：按 BackgroundSettings 渲染图片 / GIF / 视频背景，可选模糊。
/// 放在主界面 ZStack 最底层，`.allowsHitTesting(false)` 不影响任何交互。
struct BackgroundLayerView: View {
    @Environment(BackgroundSettings.self) private var bg

    var body: some View {
        ZStack {
            switch bg.kind {
            case .none:
                EmptyView()
            case .image:
                if let url = bg.fileURL {
                    StaticImageView(url: url)
                        .blur(radius: bg.isBlurred ? 24 : 0)
                }
            case .gif:
                if let url = bg.fileURL {
                    AnimatedGIFView(url: url)
                        .blur(radius: bg.isBlurred ? 24 : 0)
                }
            case .video:
                if let url = bg.fileURL {
                    LoopingVideoBackground(url: url)
                        .blur(radius: bg.isBlurred ? 24 : 0)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipped()
        .allowsHitTesting(false)
        .ignoresSafeArea()
    }
}

// MARK: - 静态图片

/// 静态图片背景（铺满，保持比例裁剪）
private struct StaticImageView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> NSImageView {
        let view = NSImageView()
        view.imageScaling = .scaleProportionallyUpOrDown
        view.image = NSImage(contentsOf: url)
        return view
    }

    func updateNSView(_ nsView: NSImageView, context: Context) {}
}

// MARK: - GIF

/// GIF 动画背景（NSImageView.animates = true 循环播放）。
/// 尊重系统「减弱动态效果」：开启时静态显示（不播放动画）。
private struct AnimatedGIFView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> NSImageView {
        let view = NSImageView()
        view.imageScaling = .scaleProportionallyUpOrDown
        view.image = NSImage(contentsOf: url)
        view.animates = !reduceMotionEnabled
        return view
    }

    func updateNSView(_ nsView: NSImageView, context: Context) {
        nsView.animates = !reduceMotionEnabled
    }

    /// 系统「减弱动态效果」开关。
    /// 直接读 NSWorkspace 而非 @Environment，避免 NSViewRepresentable
    /// 的 nonisolated 上下文访问环境属性带来的隔离问题（Swift 5.10）。
    private var reduceMotionEnabled: Bool {
        NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    }
}

// MARK: - 视频

/// 循环视频背景（AVQueuePlayer + AVPlayerLooper，静音，aspectFill，无控制条）
private struct LoopingVideoBackground: NSViewRepresentable {
    let url: URL

    func makeCoordinator() -> Coordinator { Coordinator(url: url) }

    func makeNSView(context: Context) -> NSView {
        let view = VideoBackgroundNSView()
        let layer = AVPlayerLayer()
        layer.videoGravity = .resizeAspectFill
        layer.player = context.coordinator.player
        view.wantsLayer = true
        view.layer?.addSublayer(layer)
        view.playerLayer = layer
        context.coordinator.start()
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        // 播放层尺寸由 VideoBackgroundNSView.layout() 持续同步，无需在此处理
    }

    func dismantleNSView(_ nsView: NSView, coordinator: Coordinator) {
        coordinator.teardown()
    }

    /// 播放器协调器：持有 AVQueuePlayer + AVPlayerLooper，负责生命周期
    final class Coordinator {
        let player: AVQueuePlayer
        private var looper: AVPlayerLooper?

        init(url: URL) {
            let item = AVPlayerItem(url: url)
            let queue = AVQueuePlayer()
            queue.isMuted = true
            looper = AVPlayerLooper(player: queue, templateItem: item)
            player = queue
        }

        func start() {
            player.play()
        }

        func teardown() {
            player.pause()
            looper?.disableLooping()
            looper = nil
        }
    }
}

/// 承载播放层的 NSView：layout 时同步播放层 frame，
/// 保证窗口缩放 / 视图布局变化后视频始终铺满（makeNSView 时 bounds 尚为零，
/// 不能依赖创建时的 frame）。
private final class VideoBackgroundNSView: NSView {
    weak var playerLayer: AVPlayerLayer?

    override func layout() {
        super.layout()
        playerLayer?.frame = bounds
    }
}

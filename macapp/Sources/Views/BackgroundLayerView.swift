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

/// 静态图片背景：`layer.contentsGravity = .resizeAspectFill` 缩放裁剪铺满整个窗口
/// （不按比例留边，图片超出部分被裁剪）。
private struct StaticImageView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> NSView {
        let view = StaticBackgroundNSView()
        view.wantsLayer = true
        view.layer?.contents = NSImage(contentsOf: url)
        // aspectFill：等比缩放填满，超出部分裁剪，不改变图片纵横比
        view.layer?.contentsGravity = .resizeAspectFill
        view.layer?.masksToBounds = true
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        nsView.layer?.contents = NSImage(contentsOf: url)
    }
}

/// 承载静态背景图的 NSView：layout 时同步 contents 尺寸，窗口缩放后图片持续铺满。
private final class StaticBackgroundNSView: NSView {
    override func layout() {
        super.layout()
        layer?.frame = bounds
        layer?.contentsCenter = CGRect(x: 0, y: 0, width: 1, height: 1)
    }
}

// MARK: - GIF

/// GIF 动画背景：NSImageView 循环播放；用 GeometryReader 按图片纵横比放大 frame
/// 到覆盖容器后裁剪，达到 aspectFill 效果（NSImageView 自身无 aspect fill 选项）。
private struct AnimatedGIFView: View {
    let url: URL
    @State private var image: NSImage?

    var body: some View {
        GeometryReader { proxy in
            if let image {
                let imageSize = image.size
                let scale = max(
                    proxy.size.width / max(imageSize.width, 1),
                    proxy.size.height / max(imageSize.height, 1)
                )
                GIFImageView(url: url, image: image)
                    .frame(width: imageSize.width * scale, height: imageSize.height * scale)
                    .frame(maxWidth: .infinity, maxHeight: .infinity) // 居中
            }
        }
        .clipped()
        .onAppear {
            if image == nil { image = NSImage(contentsOf: url) }
        }
    }
}

/// 实际承载 GIF 的 NSImageView（animates 循环播放，尊重减弱动态效果）
private struct GIFImageView: NSViewRepresentable {
    let url: URL
    let image: NSImage

    func makeNSView(context: Context) -> NSImageView {
        let view = NSImageView()
        view.imageScaling = .scaleProportionallyUpOrDown
        view.image = image
        view.animates = !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        return view
    }

    func updateNSView(_ nsView: NSImageView, context: Context) {
        nsView.image = image
        nsView.animates = !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
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

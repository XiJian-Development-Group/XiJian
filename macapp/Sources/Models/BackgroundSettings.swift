import Foundation
import Observation

/// UI 背景设置：图片 / 视频 / GIF，可选模糊。整窗口背景，UserDefaults 持久化。
/// `@Observable` + 单例；通过 `@Environment(BackgroundSettings.self)` 注入视图。
@Observable
@MainActor
public final class BackgroundSettings {

    /// 全局单例（App 启动时注入环境）
    public static let shared = BackgroundSettings()

    /// 背景类型
    enum BackgroundKind: String {
        /// 无背景（系统默认）
        case none = "none"
        /// 静态图片
        case image = "image"
        /// GIF 动画
        case gif = "gif"
        /// 视频
        case video = "video"
    }

    private enum Key {
        static let kind = "xijian.background.kind"
        static let path = "xijian.background.path"
        static let blurred = "xijian.background.blurred"
    }

    // MARK: 属性（didSet 持久化）

    /// 背景类型
    var kind: BackgroundKind { didSet { UserDefaults.standard.set(kind.rawValue, forKey: Key.kind) } }
    /// 背景文件绝对路径（非沙盒可直接访问；路径失效时上层回退到 none）
    var filePath: String? { didSet { UserDefaults.standard.set(filePath, forKey: Key.path) } }
    /// 是否模糊
    var isBlurred: Bool { didSet { UserDefaults.standard.set(isBlurred, forKey: Key.blurred) } }

    // MARK: 初始化（从 UserDefaults 读取，带默认值）

    /// 初始化（internal：单例 shared 供 App 使用；测试可自行创建实例）
    init() {
        let d = UserDefaults.standard
        kind = BackgroundKind(rawValue: d.string(forKey: Key.kind) ?? "") ?? .none
        filePath = d.string(forKey: Key.path)
        isBlurred = d.bool(forKey: Key.blurred)
    }

    // MARK: 操作

    /// 文件 URL（存在且是文件时返回；路径失效返回 nil）
    var fileURL: URL? {
        guard let path = filePath, FileManager.default.fileExists(atPath: path) else { return nil }
        return URL(fileURLWithPath: path)
    }

    /// 按扩展名推断类型并应用（.png/.jpg/.jpeg/.webp → image；.gif → gif；.mp4/.mov/.m4v → video）
    func apply(fileURL: URL) {
        let ext = fileURL.pathExtension.lowercased()
        switch ext {
        case "gif": kind = .gif
        case "mp4", "mov", "m4v": kind = .video
        default: kind = .image
        }
        filePath = fileURL.path
    }

    /// 清除背景（恢复系统默认）
    func clear() {
        kind = .none
        filePath = nil
        isBlurred = false
    }
}

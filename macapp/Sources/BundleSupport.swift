import Foundation

/// BundleToken 编译进 XiJianKit 框架，用于定位框架自身 bundle。
/// String Catalog（Localizable.xcstrings）作为 XiJianKit 的资源打进框架，
/// 因此所有本地化查找都必须显式使用 `Bundle.xiJian`，
/// 而不能依赖默认的 main bundle（App target 的 bundle 内没有该资源）。
private final class BundleToken {}

extension Bundle {
    /// XiJianKit 框架 bundle（zh-Hans / en / ja String Catalog 资源所在）。
    public static let xiJian: Bundle = Bundle(for: BundleToken.self)
}

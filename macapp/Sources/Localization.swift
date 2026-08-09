import Foundation
import SwiftUI

// MARK: - 本地化便捷封装（String Catalog: zh-Hans / en / ja）

/// 从 XiJianKit bundle 读取本地化字符串并（可选）填充格式参数。
///
/// - 无参数：`loc("设置")` 返回当前语言下的文案；
/// - 带参数：key 使用 String Catalog 格式占位符（`%@` / `%lld` 等），
///   例如 `loc("网络错误：%@", detail)`、`loc("Core 运行中 · 端口 %lld", port)`。
///
/// 适用于非 UI 文案（错误消息、状态文本、菜单标题等）。
/// 跨模块使用（App target 调用 XiJianKit 的函数）需保持 public。
public func loc(_ key: String.LocalizationValue, _ args: CVarArg...) -> String {
    let localized = String(localized: key, bundle: .xiJian)
    guard !args.isEmpty else { return localized }
    return String(format: localized, arguments: args)
}

extension Text {
    /// 使用 XiJianKit bundle 本地化的 SwiftUI 文本。
    /// 用法：`Text(xj: "设置")`；插值文案如 `Text(xj: "Core 运行中 · 端口 \(port)")`
    /// 由 String Catalog 按 `Core 运行中 · 端口 %lld` 自动匹配。
    public init(xj key: LocalizedStringKey, bundle: Bundle = .xiJian) {
        self.init(key, bundle: bundle)
    }
}

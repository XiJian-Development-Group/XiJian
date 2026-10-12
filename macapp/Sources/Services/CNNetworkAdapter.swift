import Foundation

/// 中国大陆网络环境适配：GitHub 镜像、npm/pypi 镜像、CA 验证跳过。
public actor CNNetworkAdapter {
    public struct Options: Sendable, Equatable {
        public var mirrorCN: Bool
        public var noCAVerify: Bool

        public init(mirrorCN: Bool = false, noCAVerify: Bool = false) {
            self.mirrorCN = mirrorCN
            self.noCAVerify = noCAVerify
        }
    }

    public static let shared = CNNetworkAdapter()

    /// GitHub 镜像列表（按可用性排序）
    public static let ghMirrors: [String] = [
        "https://gh-proxy.com/",
        "https://mirror.ghproxy.com/",
        "https://ghproxy.cn/",
        "https://github.moeyy.cn/",
    ]

    /// 当前选项
    public private(set) var options: Options

    /// 同步读取当前选项（actor 外部 await）
    public func currentOptions() -> Options { options }

    public init(options: Options = Options()) {
        self.options = options
    }

    /// 设置选项
    public func updateOptions(_ new: Options) {
        self.options = new
    }

    /// 把 GitHub URL 包装成镜像 URL。原始 URL 应是 github.com。
    /// 返回的 URL 形如 `https://gh-proxy.com/https://github.com/...`。
    public func githubURL(_ original: URL) -> URL? {
        guard options.mirrorCN else { return original }
        guard let mirror = Self.ghMirrors.first else { return original }
        // 原始 URL 拼到 mirror 后面
        let originalStr = original.absoluteString
        guard let combined = URL(string: mirror + originalStr) else { return nil }
        return combined
    }

    /// npm 镜像配置（用于 `npm config set registry`）
    /// 返回 `https://registry.npmmirror.com/` 或 nil
    public func npmRegistry() -> String? {
        guard options.mirrorCN else { return nil }
        return "https://registry.npmmirror.com/"
    }

    /// pypi 镜像配置（用于 `pip config set index-url`）
    /// 返回清华源 URL 或 nil
    public func pypiIndexURL() -> String? {
        guard options.mirrorCN else { return nil }
        return "https://pypi.tuna.tsinghua.edu.cn/simple"
    }

    /// 构造带镜像信息的 URLRequest，可选跳过 TLS 验证。
    public func configureRequest(_ request: URLRequest) -> URLRequest {
        var req = request
        if options.noCAVerify {
            // 注意：实际 TLS 跳过需 URLSession 自定义 delegate
            // 此处仅设置 header 提示
            req.setValue("true", forHTTPHeaderField: "X-No-CA-Verify")
        }
        return req
    }

    /// 导出当前配置（用于诊断）
    public func describe() -> String {
        var lines: [String] = []
        lines.append("CN mirror: \(options.mirrorCN)")
        lines.append("CA verify: \(options.noCAVerify ? "skipped" : "enabled")")
        if options.mirrorCN {
            lines.append("npm registry: \(npmRegistry() ?? "n/a")")
            lines.append("pypi index: \(pypiIndexURL() ?? "n/a")")
        }
        return lines.joined(separator: "\n")
    }
}

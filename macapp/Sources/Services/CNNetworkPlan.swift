import Foundation

/// 一次探测的结果。
public struct CNProbeResult: Sendable, Equatable, Identifiable {
    /// 探的是哪个源。
    public let mirror: CNMirror
    /// 通不通。
    public let reachable: Bool
    /// 怎么回事（状态码、错误信息）。
    public let detail: String
    /// 用了多久（毫秒）。
    public let latencyMS: Int?

    public var id: String { mirror.id }

    public init(mirror: CNMirror, reachable: Bool, detail: String = "", latencyMS: Int? = nil) {
        self.mirror = mirror
        self.reachable = reachable
        self.detail = detail
        self.latencyMS = latencyMS
    }
}

/// 一套「现在能用」的加速方案。
///
/// 每类各挑一个；某类一个都不通，那类就是空的（走直连）。
public struct CNNetworkPlan: Sendable, Equatable {
    /// 选中的 Python 包索引。
    public var pypi: CNMirror?
    /// 选中的 Git 仓库镜像。
    public var git: CNMirror?
    /// 选中的 raw / release 代理。
    public var raw: CNMirror?
    /// npm 源地址。
    public var npmRegistry: String?
    /// 二进制安装包镜像。
    public var binaryMirror: String?

    public init(
        pypi: CNMirror? = nil,
        git: CNMirror? = nil,
        raw: CNMirror? = nil,
        npmRegistry: String? = nil,
        binaryMirror: String? = nil
    ) {
        self.pypi = pypi
        self.git = git
        self.raw = raw
        self.npmRegistry = npmRegistry
        self.binaryMirror = binaryMirror
    }

    /// 这套方案什么都没加速。
    public var isEmpty: Bool {
        pypi == nil && git == nil && raw == nil && npmRegistry == nil && binaryMirror == nil
    }

    /// 给人看的一行行说明。
    public var summary: [String] {
        var lines: [String] = []
        if let pypi { lines.append("PyPI：\(pypi.name)") }
        if let git { lines.append("Git：\(git.name)") }
        if let raw { lines.append("raw：\(raw.name)") }
        if let npmRegistry { lines.append("npm：\(npmRegistry)") }
        if let binaryMirror { lines.append("二进制：\(binaryMirror)") }
        return lines.isEmpty ? ["没有可用的加速源，全部走直连"] : lines
    }
}

/// 把探测结果变成方案，并组装要注入子进程的环境变量。
///
/// **这一层是纯逻辑，不发请求**，所以能离线测。
public enum CNNetworkPlanner {

    /// 按探测结果选源。
    ///
    /// **每类取第一个可用的**，候选表本身已经按优先级排好了。
    /// 同一类里要是前面那个慢得离谱，也照样选它 —— 优先级是「实测更可靠」，
    /// 不是「这次延迟最低」。
    public static func plan(from results: [CNProbeResult]) -> CNNetworkPlan {
        func firstUsable(_ kind: CNMirrorKind) -> CNMirror? {
            for result in results where result.mirror.kind == kind && result.reachable {
                return result.mirror
            }
            return nil
        }
        return CNNetworkPlan(
            pypi: firstUsable(.pypi),
            git: firstUsable(.git),
            raw: firstUsable(.raw)
        )
    }

    /// 方案对应的环境变量。
    ///
    /// 这些变量是给 `hermes update`、官方安装脚本、pip / uv / npm 这些
    /// 子进程用的 —— **必须真的注入到进程环境里**，光在界面上显示一行字
    /// 等于没开。
    public static func environment(for plan: CNNetworkPlan) -> [String: String] {
        var env: [String: String] = [:]

        if let pypi = plan.pypi {
            env["UV_INDEX_URL"] = pypi.url
            env["UV_DEFAULT_INDEX"] = pypi.url
            env["PIP_INDEX_URL"] = pypi.url
            env["UV_INSTALL_MIRROR"] = CNMirrorCatalog.binaryMirror
            env["UV_PYTHON_INSTALL_MIRROR"] = CNMirrorCatalog.binaryMirror
        }
        if let npmRegistry = plan.npmRegistry {
            env["npm_config_registry"] = npmRegistry
            env["NODEJS_ORG_MIRROR"] = CNMirrorCatalog.binaryMirror
            env["NVM_NODEJS_ORG_MIRROR"] = CNMirrorCatalog.binaryMirror
        }
        if let raw = plan.raw {
            env["HERMES_RAW_BASE"] = raw.url
        }
        if let git = plan.git {
            // git 的写法是「把上游换成镜像」。
            //
            // 键名里的地址已经带了 .git，后面不能再加斜杠 —— 加了替换出来的
            // 地址会多出一截，git 就找不到仓库了。
            let upstreamForms = [
                "https://github.com/NousResearch/hermes-agent.git",
                "https://github.com/NousResearch/hermes-agent",
            ]
            env["GIT_CONFIG_COUNT"] = String(upstreamForms.count)
            for (index, upstream) in upstreamForms.enumerated() {
                env["GIT_CONFIG_KEY_\(index)"] = "url.\(git.url).insteadOf"
                env["GIT_CONFIG_VALUE_\(index)"] = upstream
            }
        }
        return env
    }

    /// 把 GitHub API 给的 tarball 地址换成能走镜像的 archive 地址。
    ///
    /// `https://api.github.com/repos/{owner}/{repo}/tarball/{ref}`
    /// → `https://github.com/{owner}/{repo}/archive/refs/tags/{ref}.tar.gz`
    ///
    /// **必须先换再套镜像。** 国内镜像代理的是 github.com 上的文件，
    /// api.github.com 是另一个域名，套上去取不到东西。
    public static func archiveURL(fromTarball tarball: URL) -> URL? {
        let parts = tarball.pathComponents
        // /repos/{owner}/{repo}/tarball/{ref...}
        guard let tarballIndex = parts.firstIndex(of: "tarball"),
              tarballIndex >= 2,
              tarballIndex + 1 < parts.count else {
            return nil
        }
        let owner = parts[tarballIndex - 2]
        let repo = parts[tarballIndex - 1]
        let ref = parts.suffix(from: tarballIndex + 1).joined(separator: "/")
        guard !owner.isEmpty, !repo.isEmpty, !ref.isEmpty else { return nil }
        var string = "https://github.com/\(owner)/\(repo)/archive/refs/tags/\(ref).tar.gz"
        // tag 里带斜杠时（少见）GitHub 用的是 heads 而不是 tags
        if ref.contains("/") {
            string = "https://github.com/\(owner)/\(repo)/archive/refs/heads/\(ref).tar.gz"
        }
        return URL(string: string)
    }

    /// 把选中的 Git 镜像套到任意 GitHub 地址上。
    ///
    /// `https://github.com/owner/repo/releases/...` → `https://ghproxy.icu/https://github.com/owner/repo/releases/...`
    public static func mirroredGitHubURL(_ original: URL, using mirror: CNMirror?) -> URL {
        guard let mirror else { return original }
        guard let combined = URL(string: mirror.url + original.absoluteString) else { return original }
        return combined
    }
}

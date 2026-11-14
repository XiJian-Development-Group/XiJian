import Foundation

/// 加速源的类型。
public enum CNMirrorKind: String, Sendable, Equatable, CaseIterable {
    /// Python 包索引（uv / pip 用）。
    case pypi
    /// Git 仓库镜像（clone / update 用）。
    case git
    /// GitHub raw 与 release 资产的代理前缀。
    case raw
}

/// 一个候选加速源。
///
/// **候选表不是拍脑袋列的。** 每一条都来自 hermes-cn 的实测：
/// 能用的、要怎么探测、覆盖到什么程度，都是跑出来的事实，不是猜的。
public struct CNMirror: Identifiable, Sendable, Equatable, Hashable {
    /// 源的显示名。
    public let name: String
    /// 这条源是哪一类的。
    public let kind: CNMirrorKind
    /// 怎么用它。同一类里含义一致：
    ///
    /// - `pypi`：simple 索引地址（`https://.../simple`）。
    /// - `git`：镜像上的仓库完整地址。
    /// - `raw`：代理前缀，拼在 GitHub 原始地址前面。
    public let url: String
    /// PyPI 制品地址前缀。**必须以 `/packages/` 结尾**，替换时整段顶掉
    /// `https://files.pythonhosted.org/packages/`。
    ///
    /// Hermes 的 uv.lock 里固化了三千多处 `files.pythonhosted.org/packages/...`
    /// 绝对地址，镜像只有逐字节保持同样的目录结构，才能把前缀换掉。
    public let packagesPrefix: String?
    /// 探测用的地址。
    ///
    /// **Git 源探测的就是目标仓库本身**，不是拿一个样本仓库去探。
    /// 之前用小样本探，节点只要同步过那个小仓库就算「可用」，但它未必
    /// 同步了 hermes-agent，真去 clone 官方仓库就拉不到东西。
    public let probeURL: String
    /// 探测方式。
    public let probeMethod: CNProbeMethod

    public var id: String { "\(kind.rawValue):\(name)" }

    public init(
        name: String,
        kind: CNMirrorKind,
        url: String,
        packagesPrefix: String? = nil,
        probeURL: String? = nil,
        probeMethod: CNProbeMethod = .http
    ) {
        self.name = name
        self.kind = kind
        self.url = url
        self.packagesPrefix = packagesPrefix
        self.probeURL = probeURL ?? url
        self.probeMethod = probeMethod
    }
}

/// 探测一个源用什么方式。
public enum CNProbeMethod: String, Sendable, Equatable {
    /// 普通 HTTP 探测：发一个请求，拿到 200 就算通。
    case http
    /// Git 探测：必须真的拿到 refs。
    ///
    /// **不能只看 HTTP 200。** 有的代理对 `info/refs` 返回 200，响应体却是
    /// JS 反爬挑战页，`git clone` 会得到一个空仓库 —— 这种源必须判为不可用。
    case git
}

/// 国内加速源候选表。
///
/// 数据与探测结论来自 <https://github.com/Mofan2020/hermes-cn>，那边对
/// Hermes 的 uv.lock 全量制品逐个 GET 校验过覆盖度：清华、中科大、阿里云、
/// 腾讯云、北京外国语都是 3525/3525 全覆盖（华为云路径不同，兜底用）。
public enum CNMirrorCatalog {

    // MARK: - Python 包索引

    /// PyPI 镜像候选，按优先级排。
    public static let pypi: [CNMirror] = [
        CNMirror(
            name: "清华",
            kind: .pypi,
            url: "https://pypi.tuna.tsinghua.edu.cn/simple",
            packagesPrefix: "https://pypi.tuna.tsinghua.edu.cn/packages/",
            probeURL: "https://pypi.tuna.tsinghua.edu.cn/simple/requests/"
        ),
        CNMirror(
            name: "中科大",
            kind: .pypi,
            url: "https://mirrors.ustc.edu.cn/pypi/simple",
            packagesPrefix: "https://mirrors.ustc.edu.cn/pypi/packages/",
            probeURL: "https://mirrors.ustc.edu.cn/pypi/simple/requests/"
        ),
        CNMirror(
            name: "阿里云",
            kind: .pypi,
            url: "https://mirrors.aliyun.com/pypi/simple",
            packagesPrefix: "https://mirrors.aliyun.com/pypi/packages/",
            probeURL: "https://mirrors.aliyun.com/pypi/simple/requests/"
        ),
        CNMirror(
            name: "腾讯云",
            kind: .pypi,
            url: "https://mirrors.cloud.tencent.com/pypi/simple",
            packagesPrefix: "https://mirrors.cloud.tencent.com/pypi/packages/",
            probeURL: "https://mirrors.cloud.tencent.com/pypi/simple/requests/"
        ),
        CNMirror(
            name: "北京外国语",
            kind: .pypi,
            url: "https://mirrors.bfsu.edu.cn/pypi/web/simple",
            packagesPrefix: "https://mirrors.bfsu.edu.cn/pypi/web/packages/",
            probeURL: "https://mirrors.bfsu.edu.cn/pypi/web/simple/requests/"
        ),
        // 华为云的目录结构是 repository/pypi 而不是 pypi，而且对 HEAD 返回
        // 403，只能走 GET，所以放最后当兜底。
        CNMirror(
            name: "华为云",
            kind: .pypi,
            url: "https://mirrors.huaweicloud.com/repository/pypi/simple",
            packagesPrefix: "https://mirrors.huaweicloud.com/repository/pypi/packages/",
            probeURL: "https://mirrors.huaweicloud.com/repository/pypi/simple/requests/"
        ),
    ]

    // MARK: - Git 仓库镜像

    /// Git 仓库镜像候选，按优先级排。
    ///
    /// 2026-11-05 实测（探 hermes-agent 自己）：
    /// `ghproxy.icu`、`ghproxy.vip` 能拉到；`gh-proxy.cc`、`gh-proxy.com`、
    /// `www.gh-proxy.cc` 拉不到。
    public static let git: [CNMirror] = [
        CNMirror(
            name: "ghproxy.icu",
            kind: .git,
            url: "https://ghproxy.icu/https://github.com/NousResearch/hermes-agent.git",
            probeMethod: .git
        ),
        CNMirror(
            name: "ghproxy.vip",
            kind: .git,
            url: "https://ghproxy.vip/https://github.com/NousResearch/hermes-agent.git",
            probeMethod: .git
        ),
        CNMirror(
            name: "gh-proxy.cc",
            kind: .git,
            url: "https://gh-proxy.cc/https://github.com/NousResearch/hermes-agent.git",
            probeMethod: .git
        ),
        CNMirror(
            name: "gh-proxy.com",
            kind: .git,
            url: "https://gh-proxy.com/https://github.com/NousResearch/hermes-agent.git",
            probeMethod: .git
        ),
        CNMirror(
            name: "www.gh-proxy.cc",
            kind: .git,
            url: "https://www.gh-proxy.cc/https://github.com/NousResearch/hermes-agent.git",
            probeMethod: .git
        ),
    ]

    // MARK: - raw / release 代理

    /// GitHub raw 与 release 资产的代理前缀。
    ///
    /// 官方 raw 在大陆常年不可达，而 Hermes 的 `hermes_cli/github_api.py`
    /// 没有 base URL 入口，所以这里给的是前缀，由脚本自己拼。
    public static let raw: [CNMirror] = [
        CNMirror(
            name: "jsdelivr",
            kind: .raw,
            url: "https://fastly.jsdelivr.net/",
            probeURL: "https://fastly.jsdelivr.net/gh/NousResearch/hermes-agent@main/README.md"
        ),
        CNMirror(
            name: "gh-proxy.net",
            kind: .raw,
            url: "https://gh-proxy.net/",
            probeURL: "https://gh-proxy.net/https://raw.githubusercontent.com/NousResearch/hermes-agent/main/README.md"
        ),
    ]

    // MARK: - 二进制与包管理源

    /// npm 源。
    public static let npmRegistry = "https://registry.npmmirror.com/"

    /// node / npm / uv / python 安装包走的那几个镜像。
    public static let binaryMirror = "https://registry.npmmirror.com/-/binary/"

    /// 某类别的全部候选，按优先级。
    public static func candidates(of kind: CNMirrorKind) -> [CNMirror] {
        switch kind {
        case .pypi: return pypi
        case .git: return git
        case .raw: return raw
        }
    }
}

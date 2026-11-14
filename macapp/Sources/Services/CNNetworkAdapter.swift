import Foundation

/// 中国大陆网络环境适配：GitHub 镜像、Python / npm 源、真实探测与选择。
///
/// **这一层只做两件事：探测与选择。**
///
/// 怎么把选中的源用起来（下载走镜像、子进程注入环境变量）在
/// `CNNetworkPlanner` 与调用方那边。
///
/// **为什么探测要串行。** 之前对多个源并发探测，把可用的源误判成不可用
/// —— 并发抢带宽，慢源在超时窗口里拿不到东西。串行才稳。
public actor CNNetworkAdapter {
    public struct Options: Sendable, Equatable {
        /// 要不要走国内镜像。
        public var mirrorCN: Bool

        public init(mirrorCN: Bool = false) {
            self.mirrorCN = mirrorCN
        }
    }

    public static let shared = CNNetworkAdapter()

    /// 当前选项。
    public private(set) var options: Options

    /// 上一次探测的结果。
    public private(set) var probeResults: [CNProbeResult] = []

    /// 当前方案：由探测结果算出。
    public private(set) var plan: CNNetworkPlan = CNNetworkPlan()

    private let session: URLSession
    private let gitProbeTimeout: TimeInterval

    public init(
        options: Options = Options(),
        session: URLSession = .shared,
        gitProbeTimeout: TimeInterval = 25
    ) {
        self.options = options
        self.session = session
        self.gitProbeTimeout = gitProbeTimeout
    }

    // MARK: - 选项

    /// 读当前选项。
    public func currentOptions() -> Options { options }

    /// 设置选项。
    ///
    /// 关掉镜像时方案一并清空：留着旧方案会让「已关闭加速」这件事名不副实。
    public func updateOptions(_ new: Options) {
        options = new
        if !new.mirrorCN {
            probeResults = []
            plan = CNNetworkPlan()
        }
    }

    // MARK: - 探测

    /// 挨个探测候选源，按优先级串行来。
    ///
    /// 探测的是**目标资源本身**：Git 源探的是 hermes-agent 仓库，不是拿
    /// 一个小样本仓库去探（节点同步过小仓库不代表同步了 hermes-agent）。
    public func probeAll() async -> [CNProbeResult] {
        guard options.mirrorCN else {
            probeResults = []
            plan = CNNetworkPlan()
            return []
        }

        var results: [CNProbeResult] = []
        for kind in CNMirrorKind.allCases {
            for mirror in CNMirrorCatalog.candidates(of: kind) {
                let result = await probe(mirror)
                results.append(result)
            }
        }
        probeResults = results
        plan = CNNetworkPlanner.plan(from: results)
        return results
    }

    /// 探测单个源。
    private func probe(_ mirror: CNMirror) async -> CNProbeResult {
        let start = Date()
        switch mirror.probeMethod {
        case .http:
            return await probeHTTP(mirror, startedAt: start)
        case .git:
            return await probeGit(mirror, startedAt: start)
        }
    }

    private func probeHTTP(_ mirror: CNMirror, startedAt: Date) async -> CNProbeResult {
        guard let url = URL(string: mirror.probeURL) else {
            return CNProbeResult(mirror: mirror, reachable: false, detail: "地址不合法")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        do {
            let (_, response) = try await session.data(for: request)
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            let elapsed = Int(Date().timeIntervalSince(startedAt) * 1000)
            if (200..<400).contains(code) {
                return CNProbeResult(mirror: mirror, reachable: true, detail: "HTTP \(code)", latencyMS: elapsed)
            }
            return CNProbeResult(mirror: mirror, reachable: false, detail: "HTTP \(code)", latencyMS: elapsed)
        } catch {
            let elapsed = Int(Date().timeIntervalSince(startedAt) * 1000)
            return CNProbeResult(mirror: mirror, reachable: false, detail: shortError(error), latencyMS: elapsed)
        }
    }

    /// Git 源必须真的拿到 refs。
    ///
    /// **只看 HTTP 200 会误判。** 有的代理对 `info/refs` 返回 200，响应体
    /// 却是反爬挑战页，`git clone` 只会得到一个空仓库。
    private func probeGit(_ mirror: CNMirror, startedAt: Date) async -> CNProbeResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = ["ls-remote", mirror.probeURL, "HEAD"]
        var environment = ProcessInfo.processInfo.environment
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_ASKPASS"] = "echo"
        process.environment = environment

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()

        do {
            try process.run()
        } catch {
            return CNProbeResult(mirror: mirror, reachable: false, detail: "git 起不来：\(shortError(error))")
        }

        // 超时兜底：ls-remote 卡住不能把整个界面拖死。
        let deadline = Date().addingTimeInterval(gitProbeTimeout)
        while process.isRunning && Date() < deadline {
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        if process.isRunning {
            process.terminate()
            let elapsed = Int(Date().timeIntervalSince(startedAt) * 1000)
            return CNProbeResult(
                mirror: mirror,
                reachable: false,
                detail: "超时（\(Int(gitProbeTimeout)) 秒）",
                latencyMS: elapsed
            )
        }

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: data, encoding: .utf8) ?? ""
        let elapsed = Int(Date().timeIntervalSince(startedAt) * 1000)
        if process.terminationStatus == 0, !output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return CNProbeResult(mirror: mirror, reachable: true, detail: "拿到 refs", latencyMS: elapsed)
        }
        return CNProbeResult(mirror: mirror, reachable: false, detail: "没有 refs", latencyMS: elapsed)
    }

    private func shortError(_ error: Error) -> String {
        let text = (error as NSError).localizedDescription
        return text.count > 80 ? String(text.prefix(80)) + "…" : text
    }

    // MARK: - 读结果

    /// 当前方案。
    public func currentPlan() -> CNNetworkPlan { plan }

    /// 上一次探测结果。
    public func currentProbeResults() -> [CNProbeResult] { probeResults }

    // MARK: - 便捷入口

    /// 把 GitHub 地址套上选中的镜像。
    ///
    /// 没开镜像、或者还没探到可用的 Git 源，就返回原地址。
    public func githubURL(_ original: URL) -> URL? {
        guard options.mirrorCN else { return original }
        guard let git = plan.git else { return original }
        return CNNetworkPlanner.mirroredGitHubURL(original, using: git)
    }

    /// npm 源地址。没探到就是 nil（走默认）。
    public func npmRegistry() -> String? {
        guard options.mirrorCN else { return nil }
        return plan.npmRegistry
    }

    /// Python 包索引地址。没探到就是 nil（走默认）。
    public func pypiIndexURL() -> String? {
        guard options.mirrorCN else { return nil }
        return plan.pypi?.url
    }

    /// 要注入子进程的环境变量。
    public func environmentOverrides() -> [String: String] {
        guard options.mirrorCN else { return [:] }
        var plan = self.plan
        if plan.npmRegistry == nil, plan.pypi != nil {
            plan.npmRegistry = CNMirrorCatalog.npmRegistry
        }
        if plan.binaryMirror == nil, plan.pypi != nil {
            plan.binaryMirror = CNMirrorCatalog.binaryMirror
        }
        return CNNetworkPlanner.environment(for: plan)
    }

    /// 导出当前配置（用于诊断）。
    public func describe() -> String {
        var lines: [String] = []
        lines.append("CN mirror: \(options.mirrorCN)")
        if options.mirrorCN {
            if probeResults.isEmpty {
                lines.append("还没探测过")
            } else {
                lines.append(contentsOf: plan.summary)
                let usable = probeResults.filter(\.reachable).count
                lines.append("可用的源：\(usable) / \(probeResults.count)")
            }
        }
        return lines.joined(separator: "\n")
    }
}

import Foundation

/// Hermes 安装/卸载/备份/更新管理器。
///
/// 工作流：
/// 1. `detect()` 在 PATH 找 hermes 二进制
/// 2. `install(options:)` 从 GitHub releases 下载 wheel/tarball，解压到 installPath
/// 3. `uninstall(options:)` 备份后删除整个 installPath
/// 4. `backup(to:)` 把 installPath 打包成 .tar.gz
/// 5. `restore(from:)` 从备份恢复
/// 6. `checkForUpdate()` 检查 GitHub releases latest
/// 7. `update(toVersion:)` 下载新版本并替换
///
/// 网络层：默认走 GitHub API + 直接下载。
///
/// **开了国内加速就走镜像。** 下载地址会先换成 GitHub 的 archive 地址，
/// 再套上探测出来的镜像前缀 —— 之前这里写死 gh-proxy.com，那个源并不
/// 一定通，现在由 `CNNetworkAdapter` 探测后决定。
///
/// **没有「跳过 TLS 校验」这个选项。** 以前有个开关，勾上以后既没有真的
/// 跳过校验（只往请求里塞了个没用的头），又会让人以为证书校验被关了。
/// 需要改网络环境请走代理，那条路是干净的。
public actor HermesInstaller {
    public struct Status: Sendable, Equatable {
        public let installed: Bool
        public let path: URL?
        public let version: String?
    }

    public struct Release: Sendable, Equatable, Codable {
        public let tagName: String
        public let name: String
        public let publishedAt: Date
        public let tarballURL: URL
        public let zipballURL: URL

        enum CodingKeys: String, CodingKey {
            case tagName = "tag_name"
            case name
            case publishedAt = "published_at"
            case tarballURL = "tarball_url"
            case zipballURL = "zipball_url"
        }
    }

    private let searchPaths: [String]
    private let fileManager: FileManager
    private var urlSession: URLSession

    public init(
        searchPaths: [String] = ["/usr/local/bin", "/opt/homebrew/bin", "~/.local/bin", "/usr/bin"],
        fileManager: FileManager = .default
    ) {
        self.searchPaths = searchPaths
        self.fileManager = fileManager
        let config = URLSessionConfiguration.default
        self.urlSession = URLSession(configuration: config)
    }

    /// 检测 Hermes 是否已安装。在 PATH 中找 hermes 可执行文件，返回路径与版本。
    public func detect() async -> Status {
        for path in searchPaths {
            let expanded = (path as NSString).expandingTildeInPath
            let candidate = URL(fileURLWithPath: expanded).appendingPathComponent("hermes")
            if fileManager.isExecutableFile(atPath: candidate.path) {
                let version = await probeVersion(at: candidate)
                return Status(installed: true, path: candidate, version: version)
            }
        }
        return Status(installed: false, path: nil, version: nil)
    }

    private func probeVersion(at path: URL) async -> String? {
        let process = Process()
        process.executableURL = path
        process.arguments = ["--version"]
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe
        do {
            try process.run()
            process.waitUntilExit()
            let data = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
        } catch {
            return nil
        }
    }

    /// 获取最新 release 信息。
    public func checkForUpdate(owner: String = "NousResearch", repo: String = "hermes-agent") async throws -> Release? {
        let apiURL = URL(string: "https://api.github.com/repos/\(owner)/\(repo)/releases/latest")!
        var request = URLRequest(url: apiURL)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        let (data, response) = try await urlSession.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw InstallError.networkError(underlying: "Invalid response type")
        }
        if http.statusCode == 404 {
            return nil
        }
        if !(200..<300).contains(http.statusCode) {
            let msg = String(data: data, encoding: .utf8) ?? "<empty>"
            throw InstallError.githubAPIError(statusCode: http.statusCode, message: msg)
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(Release.self, from: data)
    }

    /// 安装 Hermes。从 GitHub releases 下载 tarball，解压到 installPath。
    public func install(options: HermesInstallOptions) async throws {
        // 已装则报错
        let current = await detect()
        if current.installed {
            throw InstallError.alreadyInstalled(path: current.path ?? options.installPath)
        }
        // 备份现有（如果存在）
        if options.backupBeforeInstall && fileManager.fileExists(atPath: options.installPath.path) {
            try await backup(installPath: options.installPath, to: options.backupDirectory)
        }
        // 找 release
        guard let release = try await checkForUpdate(owner: options.githubOwner, repo: options.githubRepo) else {
            throw InstallError.noReleasesAvailable
        }
        // 下载 + 解压
        try await downloadAndExtract(release: release, to: options.installPath)
    }

    /// 卸载 Hermes。备份后删除。
    public func uninstall(options: HermesInstallOptions) async throws {
        guard fileManager.fileExists(atPath: options.installPath.path) else {
            throw InstallError.notInstalled
        }
        if options.backupBeforeInstall {
            try await backup(installPath: options.installPath, to: options.backupDirectory)
        }
        try fileManager.removeItem(at: options.installPath)
    }

    /// 备份 installPath 到 destination，文件名格式：hermes-backup-<timestamp>.tar.gz
    public func backup(installPath: URL, to destination: URL) async throws {
        guard fileManager.fileExists(atPath: installPath.path) else {
            throw InstallError.backupFailed(reason: "源目录不存在：\(installPath.path)")
        }
        try fileManager.createDirectory(at: destination, withIntermediateDirectories: true)
        let timestamp = ISO8601DateFormatter().string(from: Date())
            .replacingOccurrences(of: ":", with: "-")
        let archiveName = "hermes-backup-\(timestamp).tar.gz"
        let archiveURL = destination.appendingPathComponent(archiveName)
        // 用 ditto（macOS 内置，支持长路径 + 隐藏文件）
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        process.arguments = ["-c", "-z", "-X", installPath.path, archiveURL.path]
        let stderrPipe = Pipe()
        process.standardError = stderrPipe
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let err = String(data: stderrPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw InstallError.backupFailed(reason: "ditto 退出码 \(process.terminationStatus): \(err)")
        }
    }

    /// 从备份恢复 installPath。
    public func restore(from backup: URL, to installPath: URL) async throws {
        guard fileManager.fileExists(atPath: backup.path) else {
            throw InstallError.backupFailed(reason: "备份文件不存在：\(backup.path)")
        }
        // 清空目标
        if fileManager.fileExists(atPath: installPath.path) {
            try fileManager.removeItem(at: installPath)
        }
        try fileManager.createDirectory(at: installPath, withIntermediateDirectories: true)
        // ditto 解压
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        process.arguments = ["-x", "-X", backup.path, installPath.path]
        let stderrPipe = Pipe()
        process.standardError = stderrPipe
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let err = String(data: stderrPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw InstallError.extractFailed(reason: "ditto 退出码 \(process.terminationStatus): \(err)")
        }
    }

    /// 更新 Hermes。下载最新 release，备份现有，替换。
    public func update(options: HermesInstallOptions, targetVersion: String? = nil) async throws {
        // 备份现有
        if fileManager.fileExists(atPath: options.installPath.path) {
            try await backup(installPath: options.installPath, to: options.backupDirectory)
        }
        // 找 release
        guard let release = try await checkForUpdate(owner: options.githubOwner, repo: options.githubRepo) else {
            throw InstallError.noReleasesAvailable
        }
        if let target = targetVersion, release.tagName != target {
            throw InstallError.githubAPIError(
                statusCode: 0,
                message: "目标版本 \(target) 与 latest \(release.tagName) 不一致；精确版本更新未实现"
            )
        }
        // 删除旧 installPath（已备份）
        if fileManager.fileExists(atPath: options.installPath.path) {
            try fileManager.removeItem(at: options.installPath)
        }
        // 下载新版本
        try await downloadAndExtract(release: release, to: options.installPath)
    }

    private func downloadAndExtract(release: Release, to destination: URL) async throws {
        // 下载 tarball。开了加速就套镜像。
        //
        // 先把 API 给的 tarball 地址换成 github.com 上的 archive 地址 ——
        // 国内镜像代理的是后者，套在 api.github.com 上取不到东西。
        let base = CNNetworkPlanner.archiveURL(fromTarball: release.tarballURL) ?? release.tarballURL
        let target = await CNNetworkAdapter.shared.githubURL(base) ?? base
        let (data, response) = try await urlSession.data(from: target)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let code = (response as? HTTPURLResponse)?.statusCode ?? -1
            throw InstallError.downloadFailed(url: target, statusCode: code)
        }
        // 写入临时文件
        let tmp = fileManager.temporaryDirectory.appendingPathComponent("hermes-\(UUID().uuidString).tar.gz")
        try data.write(to: tmp)
        defer { try? fileManager.removeItem(at: tmp) }
        // 解压到 destination
        try fileManager.createDirectory(at: destination, withIntermediateDirectories: true)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        process.arguments = ["-x", "-X", tmp.path, destination.path]
        let stderrPipe = Pipe()
        process.standardError = stderrPipe
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let err = String(data: stderrPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw InstallError.extractFailed(reason: "ditto 退出码 \(process.terminationStatus): \(err)")
        }
    }
}

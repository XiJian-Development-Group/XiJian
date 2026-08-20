import AppKit
import Foundation
import Observation

/// Core 进程管理器 — 负责将嵌入 App 的 Core（PyInstaller onedir 产物）复制到
/// 应用数据目录、启动子进程、健康检查、读取 token 以及优雅停止。
///
/// 启动流程（对应 docs/BuildGuide.md §6）：
/// 1. 检查 bundle 内 Resources/Core/xijian-api 是否存在
/// 2. 若 `~/Library/Application Support/XiJian/Core/` 不存在则整目录复制
/// 3. 以 `<dir>/xijian-api --port <port>` 启动子进程（不 zip 解压，避免首启慢）
/// 4. 读取 `tmp/xijian-<pid>.port` 获取实际生效端口（端口被占用时 Core 自动换端口）
/// 5. 在真实端口上轮询 `GET /healthz` 直到 200（超时 60 秒）
/// 6. 读取 ``tmp/xijian.token``（macapp 预置的稳定 token）作为 Bearer token
@Observable
@MainActor
public final class CoreManager {

    /// 运行状态机
    enum State: Equatable {
        /// 未运行
        case stopped
        /// 正在复制 Core 到应用数据目录
        case extracting
        /// 子进程启动中（等待健康检查 / token）
        case starting
        /// 运行中（含端口号）
        case running(port: Int)
        /// 使用自定义服务器（不管理本机 Core 进程）
        case customServer
        /// 出错（含中文错误描述）
        case error(String)
    }

    public static let shared = CoreManager()

    // MARK: - 可观察状态

    private(set) var state: State = .stopped
    /// Bearer token（macapp 启动前预置到 tmp/xijian.token，Core 经
    /// XIJIAN_TOKEN_FILE 环境变量读取）
    private(set) var token: String?
    /// 子进程 PID
    private(set) var pid: Int32?
    /// 实际生效端口（端口被占用时 Core 自动换端口后，从 tmp/xijian-<pid>.port 读取；
    /// nil 表示尚未确认，此时回落使用配置端口）
    private(set) var activePort: Int?
    /// 最近捕获的进程输出（环形缓冲，最多 1000 条，用于诊断与日志页）
    private(set) var recentLogs: [LogEntry] = []

    // MARK: - 连接设置（UserDefaults 持久化）

    /// 端口（默认 18500，与 Core 的 DEFAULT_PORT 一致）
    var port: Int {
        didSet {
            let clamped = min(max(port, 1), 65535)
            if port != clamped { port = clamped; return }
            UserDefaults.standard.set(port, forKey: XJDefaultsKey.corePort)
        }
    }

    /// 是否使用自定义服务器（跳过本机 Core 进程管理）
    var useCustomServer: Bool {
        didSet { UserDefaults.standard.set(useCustomServer, forKey: XJDefaultsKey.coreUseCustomServer) }
    }

    /// 自定义服务器地址（如 http://127.0.0.1:18500）
    var customBaseURL: String {
        didSet { UserDefaults.standard.set(customBaseURL, forKey: XJDefaultsKey.coreCustomBaseURL) }
    }

    /// 自定义服务器访问令牌（可选，Keychain 持久化；UserDefaults 仅存「已配置」标记，S7）
    var customToken: String {
        didSet {
            if customToken.isEmpty {
                _ = KeychainStore.shared.delete(forKey: Self.customTokenKeychainAccount)
                UserDefaults.standard.removeObject(forKey: XJDefaultsKey.coreCustomTokenConfigured)
            } else {
                _ = KeychainStore.shared.save(customToken, forKey: Self.customTokenKeychainAccount)
                UserDefaults.standard.set(true, forKey: XJDefaultsKey.coreCustomTokenConfigured)
            }
        }
    }

    /// Keychain 中自定义服务器 token 的 account 名
    static let customTokenKeychainAccount = "xijian.core.customToken"

    // MARK: - 内部状态

    private var process: Process?
    private var operationID = 0
    private var isStopping = false

    // MARK: - 测试钩子（默认值即生产行为）

    /// 测试用：覆盖 bundle 内 Core 路径探测
    var bundleCoreOverride: URL?
    /// 测试用：覆盖 Core 运行目录（隔离测试数据）
    var isolatedCoreDirectoryOverride: URL?
    /// 测试用：覆盖 makeClient 注入的 URLSession（默认 .shared，测试注入 MockURLProtocol）
    var clientSessionOverride: URLSession?
    /// 健康检查超时（秒）
    var healthTimeout: TimeInterval = 60
    /// 健康检查轮询间隔（秒）
    var healthPollInterval: TimeInterval = 0.3
    /// token 文件等待超时（秒）
    var tokenTimeout: TimeInterval = 30

    // MARK: - 初始化

    private init() {
        let defaults = UserDefaults.standard
        port = defaults.object(forKey: XJDefaultsKey.corePort) as? Int ?? 18500
        useCustomServer = defaults.bool(forKey: XJDefaultsKey.coreUseCustomServer)
        customBaseURL = defaults.string(forKey: XJDefaultsKey.coreCustomBaseURL) ?? ""
        // S7 迁移：若 UserDefaults 仍留有旧版明文 token，搬入 Keychain 并删除明文。
        // 新装用户直接读 Keychain（无则空串）。
        if let legacy = defaults.string(forKey: "xijian.core.customToken"), !legacy.isEmpty {
            _ = KeychainStore.shared.save(legacy, forKey: Self.customTokenKeychainAccount)
            defaults.removeObject(forKey: "xijian.core.customToken")
            defaults.set(true, forKey: XJDefaultsKey.coreCustomTokenConfigured)
        }
        customToken = KeychainStore.shared.load(forKey: Self.customTokenKeychainAccount) ?? ""
    }

    // MARK: - 路径

    // UserDefaults 持久化键统一见 XJDefaultsKey（Sources/Models/UserDefaultsKeys.swift）。

    /// bundle 内的 Core 资源目录（Resources/Core）
    var bundleCoreURL: URL? {
        if let override = bundleCoreOverride,
           FileManager.default.fileExists(atPath: override.path) {
            return override
        }
        guard let resources = Bundle.main.resourceURL else { return nil }
        let core = resources.appendingPathComponent("Core")
        return FileManager.default.fileExists(atPath: core.path) ? core : nil
    }

    /// 应用数据根目录：~/Library/Application Support/XiJian
    /// 支持通过 XIJIAN_DATA_DIR 环境变量覆盖（与 Core 配置一致）。
    var appSupportDirectory: URL {
        if let envDir = ProcessInfo.processInfo.environment["XIJIAN_DATA_DIR"],
           !envDir.isEmpty {
            let expanded = (envDir as NSString).expandingTildeInPath
            return URL(fileURLWithPath: expanded)
        }
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support")
        return base.appendingPathComponent("XiJian", isDirectory: true)
    }

    /// Core 运行目录：~/Library/Application Support/XiJian/Core
    public var coreDirectory: URL? {
        if let override = isolatedCoreDirectoryOverride { return override }
        return appSupportDirectory.appendingPathComponent("Core", isDirectory: true)
    }

    /// 数据存储根目录（Core 统一存储，见 runtime.default_storage_dir）。
    /// 注意：macapp 不直接写数据，仅 Core 子进程按自身配置使用。
    var dataDirectory: URL {
        appSupportDirectory.appendingPathComponent("Data", isDirectory: true)
    }

    /// 统一临时目录：~/Library/Application Support/XiJian/tmp（token/port 文件）
    /// 对应 runtime.default_tmp_dir()，所有 XiJian 组件共享；
    /// 不属于 Core 目录，重置 Core 数据时保留。
    var runtimeTmpDirectory: URL {
        appSupportDirectory.appendingPathComponent("tmp", isDirectory: true)
    }

    /// 有效端口：自定义服务器时取自定义 URL 中的端口，否则取实际生效端口
    ///（未确认时回落配置端口）
    var effectivePort: Int {
        if useCustomServer, let url = URL(string: customBaseURL), let p = url.port {
            return p
        }
        return activePort ?? port
    }

    /// API base URL（http://127.0.0.1:<port> 或自定义）
    var baseURL: URL {
        if useCustomServer, let url = URL(string: customBaseURL), !customBaseURL.isEmpty {
            return url
        }
        return URL(string: "http://127.0.0.1:\(activePort ?? port)")!
    }

    // MARK: - 启动

    /// 启动 Core（幂等：已在运行或启动中则直接返回）
    public func startCore() async {
        // 使用自定义服务器时不管理本机 Core 进程：直接进入自定义服务器状态，不启动子进程。
        if useCustomServer {
            activePort = nil
            state = .customServer
            return
        }
        if case .running = state { return }
        if case .starting = state { return }
        if case .extracting = state { return }
        if isStopping { return }

        operationID += 1
        let myID = operationID
        isStopping = false

        // S3：启动前清理上次运行残留的 token/port 发现文件（仅清理 pid 已死的）
        cleanupStaleDiscoveryFiles()

        // 1. bundle 内检查
        guard let bundleCore = bundleCoreURL else {
            state = .error(loc("未找到内置 Core 资源（Resources/Core/xijian-api）。请先运行 macapp/build-core.sh 构建 Core 后再启动。"))
            return
        }
        guard let coreDir = coreDirectory else {
            state = .error(loc("无法解析 Core 运行目录。"))
            return
        }

        // 2. 复制 onedir 产物（不 zip 解压）。
        // 注意：Core 的存储根目录与运行目录相同（~/Library/Application Support/XiJian/Core），
        // 目录可能已存在（含用户数据），因此按“缺可执行文件或 bundle 版本更新才合并复制”处理：
        // 把 bundle 内的 xijian-api / _internal / config.toml / README.txt 逐项合入，
        // 已存在的项（如用户数据、用户编辑过的 config.toml）保留。
        let exeURL = coreDir.appendingPathComponent("xijian-api")
        if Self.shouldMergeCore(from: bundleCore, to: coreDir) {
            state = .extracting
            let copied: Result<Void, Error> = await Task.detached(priority: .userInitiated) {
                do {
                    try Self.mergeCore(from: bundleCore, to: coreDir)
                    return .success(())
                } catch {
                    return .failure(error)
                }
            }.value
            guard myID == operationID else { return }
            switch copied {
            case .failure(let error):
                state = .error(loc("复制 Core 到应用数据目录失败：%@", error.localizedDescription))
                return
            case .success:
                break
            }
        }

        // 3. 启动子进程
        guard FileManager.default.isExecutableFile(atPath: exeURL.path) else {
            state = .error(loc("Core 可执行文件不存在或不可执行：%@", exeURL.path))
            return
        }

        state = .starting
        let launchPort = effectivePort

        // 预置稳定 token：Core 以生产模式启动（不再自动降级 dev）。
        // 文件固定名 tmp/xijian.token，只在缺失时生成，避免每次启动换 token。
        // 同时迁移旧版 pid-based token 文件（xijian-<pid>.token）到固定文件。
        guard let tokenFile = provisionTokenFile() else {
            state = .error(loc("无法创建 token 文件（%@）。请检查 tmp 目录权限。", runtimeTmpDirectory.path))
            return
        }

        let proc = Process()
        proc.executableURL = exeURL
        proc.arguments = ["--port", "\(launchPort)"]
        proc.currentDirectoryURL = coreDir
        var env = ProcessInfo.processInfo.environment
        // 注意：不设置 XIJIAN_DATA_DIR —— 让 runtime.py 使用默认存储根
        //（即可执行文件同级目录 ~/Library/Application Support/XiJian/Core），
        // 使 onedir 运行时、config.toml、logs/ 与数据（xijian.db 等）保持同一目录。
        //（token/port 等临时文件统一在 tmp/，由 runtime.default_tmp_dir 推导）
        env["XIJIAN_TOKEN_FILE"] = tokenFile.path
        proc.environment = env

        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe

        process = proc
        observeOutput(outPipe.fileHandleForReading)
        observeOutput(errPipe.fileHandleForReading)

        do {
            try proc.run()
            pid = proc.processIdentifier
            // token 文件已在启动前通过 provisionTokenFile() 创建（固定名 xijian.token），
            // 并通过 XIJIAN_TOKEN_FILE 环境变量传递给 Core 进程。
            // 无需再写入 pid-based token 文件。
        } catch {
            process = nil
            pid = nil
            state = .error(loc("启动 Core 进程失败：%@", error.localizedDescription))
            return
        }

        proc.terminationHandler = { [weak self] finished in
            Task { @MainActor in
                self?.handleTermination(finished)
            }
        }

        appendLog(loc("[XiJian] 已启动 Core 进程 pid=%lld port=%lld", Int(proc.processIdentifier), launchPort))

        // 4. 读取端口文件，确认实际生效端口
        //（配置端口被占用时 Core 会报告占用进程并自动换端口，真实端口通过
        //  tmp/xijian-<pid>.port 下发，必须等它出现后再做健康检查，否则会轮询到错误端口）
        let actualPort = await waitForPortFile(pid: proc.processIdentifier, id: myID)
        guard myID == operationID else { return }
        guard let actualPort else {
            if let p = process, p.isRunning { p.terminate() }
            state = .error(loc("等待 Core 端口文件超时（%lld 秒内未生成 tmp/xijian-%lld.port）。请查看日志或尝试重启。", Int(tokenTimeout), Int(proc.processIdentifier)))
            return
        }
        activePort = actualPort
        if actualPort != launchPort {
            appendLog(loc("[XiJian] 端口 %lld 被占用，Core 已自动切换到端口 %lld", launchPort, actualPort))
        }

        // 5. 在真实端口上轮询健康检查
        let ready = await waitForHealth(port: actualPort, id: myID)
        guard myID == operationID else { return }
        guard ready else {
            if let p = process, p.isRunning { p.terminate() }
            state = .error(loc("Core 启动超时（%lld 秒内未就绪）。请查看日志或尝试重启。", Int(healthTimeout)))
            return
        }

        // 6. 读取 token
        let token = await waitForToken(pid: proc.processIdentifier, id: myID)
        guard myID == operationID else { return }
        self.token = token
        appendLog(loc("[XiJian] Core 就绪：http://127.0.0.1:%lld（token 已读取）", actualPort))
        state = .running(port: actualPort)
    }

    // MARK: - 停止 / 重启

    /// 停止 Core（SIGTERM，超时后 SIGINT，再超时 SIGKILL）
    public func stopCore() async {
        operationID += 1
        isStopping = true

        guard let proc = process else {
            process = nil
            pid = nil
            token = nil
            activePort = nil
            state = .stopped
            isStopping = false
            return
        }

        appendLog(loc("[XiJian] 正在停止 Core（SIGTERM）..."))
        proc.terminate()

        let deadline = Date().addingTimeInterval(8)
        while proc.isRunning && Date() < deadline {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        if proc.isRunning {
            appendLog(loc("[XiJian] 未响应 SIGTERM，发送 SIGINT ..."))
            proc.interrupt()
            try? await Task.sleep(nanoseconds: 800_000_000)
        }
        if proc.isRunning {
            appendLog(loc("[XiJian] 仍未退出，强制结束（SIGKILL）..."))
            kill(proc.processIdentifier, SIGKILL)
        }

        process = nil
        pid = nil
        token = nil
        activePort = nil
        isStopping = false
        state = .stopped
        appendLog(loc("[XiJian] Core 已停止"))
    }

    /// 重启 Core
    public func restartCore() async {
        await stopCore()
        await startCore()
    }

    /// 同步停止 Core（用于应用退出等无法等待异步任务的场景）。
    /// 在 MainActor 上执行，通过 RunLoop 等待进程退出，避免死锁。
    public func stopCoreSync() {
        operationID += 1
        isStopping = true
        guard let proc = process else {
            process = nil
            pid = nil
            token = nil
            activePort = nil
            isStopping = false
            state = .stopped
            return
        }
        proc.terminate()
        let deadline = Date().addingTimeInterval(8)
        while proc.isRunning && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
        process = nil
        pid = nil
        token = nil
        activePort = nil
        isStopping = false
        state = .stopped
    }

    // MARK: - 数据管理

    /// 重置 Core 数据：停止进程并删除 Core 数据目录
    /// （仅 ~/Library/Application Support/XiJian/Core，不触碰 XiJian/ 下其它子目录，
    /// 避免误删 DevKit 等旧应用数据）
    func resetCoreData() async {
        await stopCore()
        guard let coreDir = coreDirectory else { return }
        let fm = FileManager.default
        if fm.fileExists(atPath: coreDir.path) {
            try? fm.removeItem(at: coreDir)
        }
        recentLogs.removeAll()
        logEntries.removeAll()
        fileRawLines.removeAll()
        logFileExists = false
        logFileLoadError = nil
        appendLog(loc("[XiJian] 已重置 Core 数据目录：%@", coreDir.path))
    }

    /// 打开 Core 日志目录（Finder）
    func openLogDirectory() {
        guard let coreDir = coreDirectory else { return }
        let logDir = coreDir.appendingPathComponent("logs")
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(logDir)
    }

    // MARK: - API 客户端工厂

    /// 生成 APIClient；Core 未运行或缺少 token 时返回 nil
    func makeClient() -> APIClient? {
        if useCustomServer {
            // 自定义服务器模式：始终可生成客户端，token 使用用户配置的访问令牌（可为空）
            return APIClient(baseURL: baseURL, token: customToken, session: clientSessionOverride ?? .shared)
        }
        guard case .running = state, let token = token, !token.isEmpty else { return nil }
        return APIClient(baseURL: baseURL, token: token, session: clientSessionOverride ?? .shared)
    }

    /// 生成 APIClient；Core 未运行或缺少 token 时出错
    func makeClientOrThrow() throws -> APIClient {
        guard let client = makeClient() else {
            throw APIError.coreNotRunning
        }
        return client
    }

    /// 是否需要合并复制：目标可执行文件缺失，或 bundle 内可执行文件比已安装的更新，
    /// 或 bundle 与已安装的 `_internal` 目录总大小不同。
    /// PyInstaller onedir 的代码主体在 `_internal/`，仅 Python 代码变化可能不影响
    /// 可执行文件本身的 mtime/size，因此额外比较 `_internal` 目录总大小（递归累加）。
    nonisolated static func shouldMergeCore(from source: URL, to dest: URL) -> Bool {
        let fm = FileManager.default
        let installedExe = dest.appendingPathComponent("xijian-api")
        guard fm.isExecutableFile(atPath: installedExe.path) else { return true }
        let bundleExe = source.appendingPathComponent("xijian-api")
        guard let bundleAttr = try? fm.attributesOfItem(atPath: bundleExe.path),
              let installedAttr = try? fm.attributesOfItem(atPath: installedExe.path) else {
            return true
        }
        let bundleDate = (bundleAttr[.modificationDate] as? Date) ?? .distantPast
        let installedDate = (installedAttr[.modificationDate] as? Date) ?? .distantFuture
        let bundleSize = (bundleAttr[.size] as? NSNumber)?.int64Value ?? 0
        let installedSize = (installedAttr[.size] as? NSNumber)?.int64Value ?? -1
        // 大小不同（构建内容变化）或 bundle 更新 → 重新合并
        if bundleSize != installedSize || bundleDate > installedDate {
            return true
        }
        // 代码主体在 _internal/：目录总大小（递归）任一不同即需要重新合并。
        // 仅启动时计算一次，成本可接受。
        let bundleInternal = source.appendingPathComponent("_internal")
        let installedInternal = dest.appendingPathComponent("_internal")
        if fm.fileExists(atPath: bundleInternal.path) || fm.fileExists(atPath: installedInternal.path) {
            if Self.directoryTotalSize(at: bundleInternal) != Self.directoryTotalSize(at: installedInternal) {
                return true
            }
        }
        return false
    }

    /// 递归累加目录内所有文件的大小（字节）；目录不存在时返回 0。
    nonisolated static func directoryTotalSize(at url: URL) -> Int64 {
        guard let enumerator = FileManager.default.enumerator(
            at: url,
            includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey],
            options: [.skipsHiddenFiles]
        ) else { return 0 }
        var total: Int64 = 0
        for case let fileURL as URL in enumerator {
            guard let values = try? fileURL.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey]),
                  values.isRegularFile == true,
                  let size = values.fileSize else { continue }
            total += Int64(size)
        }
        return total
    }

    /// 合并复制 Core：把 onedir 内容逐项复制到目标目录。
    /// 已存在的项保留（用户数据 / 用户编辑过的 config.toml）；
    /// 但 xijian-api 与 _internal 必须来自 bundle（旧版本则先移除再复制）。
    nonisolated static func mergeCore(from source: URL, to dest: URL) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: dest, withIntermediateDirectories: true)
        let items = try fm.contentsOfDirectory(at: source, includingPropertiesForKeys: nil)
        for item in items {
            let name = item.lastPathComponent
            let target = dest.appendingPathComponent(name)
            if fm.fileExists(atPath: target.path) {
                if name == "xijian-api" || name == "_internal" {
                    try? fm.removeItem(at: target)
                    try fm.copyItem(at: item, to: target)
                }
                // 其他已存在项（config.toml / README.txt / 数据目录）保留
                continue
            }
            try fm.copyItem(at: item, to: target)
        }
    }

    // MARK: - 内部实现

    private func handleTermination(_ finished: Process) {
        guard !isStopping else {
            state = .stopped
            return
        }
        let code = finished.terminationReason == .exit
            ? loc("退出码 %lld", Int(finished.terminationStatus))
            : loc("信号 %lld", Int(finished.terminationStatus))
        appendLog(loc("[XiJian] Core 进程意外退出（%@）", code))
        process = nil
        pid = nil
        token = nil
        activePort = nil
        if case .running = state {
            state = .error(loc("Core 进程意外退出（%@）。可在设置中查看日志或重启 Core。", code))
        } else if case .starting = state {
            state = .error(loc("Core 启动失败（%@）。请查看日志。", code))
        }
    }

    /// 轮询 /healthz 直到 200 或超时
    private func waitForHealth(port: Int, id: Int) async -> Bool {
        let url = URL(string: "http://127.0.0.1:\(port)/healthz")!
        let deadline = Date().addingTimeInterval(healthTimeout)
        var request = URLRequest(url: url)
        request.timeoutInterval = 2
        while Date() < deadline {
            if id != operationID { return false }
            if isStopping { return false }
            do {
                let (_, response) = try await URLSession.shared.data(for: request)
                if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                    return true
                }
            } catch {
                // 服务未就绪，继续轮询
            }
            try? await Task.sleep(nanoseconds: UInt64(healthPollInterval * 1_000_000_000))
        }
        return false
    }

    /// 读取/生成固定本机 token（Keychain 持久化；Keychain 不可用时每次重新生成）。
    nonisolated static func provisionedLocalToken() -> String {
        let account = "xijian.core.localToken"
        if let existing = KeychainStore.shared.load(forKey: account), !existing.isEmpty {
            return existing
        }
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        let token: String
        if status == errSecSuccess {
            token = bytes.map { String(format: "%02x", $0) }.joined()
        } else {
            token = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        }
        _ = KeychainStore.shared.save(token, forKey: account)
        return token
    }

    /// 清理本机 tmp 目录下残留的 token/port 发现文件（S3）：
    /// 匹配 xijian-<pid>.token / xijian-<pid>.port 且 pid 已不在运行的文件。
    /// 固定名文件 xijian.token / xijian.port 不清理。
    /// 启动时调用一次，避免多次启动后 tmp 累积旧文件。
    private func cleanupStaleDiscoveryFiles() {
        let dir = runtimeTmpDirectory
        guard let files = try? FileManager.default.contentsOfDirectory(atPath: dir.path) else { return }
        for name in files {
            // 排除固定名文件
            if name == "xijian.token" || name == "xijian.port" { continue }
            guard name.hasPrefix("xijian-"),
                  name.hasSuffix(".token") || name.hasSuffix(".port") else { continue }
            let pidPart = name
                .replacingOccurrences(of: "xijian-", with: "")
                .replacingOccurrences(of: ".token", with: "")
                .replacingOccurrences(of: ".port", with: "")
            guard let oldPid = Int32(pidPart), oldPid > 0 else { continue }
            // kill(pid, 0) 探测进程是否存活；ESRCH = 不存在，可清理
            if kill(oldPid, 0) != 0 && errno == ESRCH {
                try? FileManager.default.removeItem(at: dir.appendingPathComponent(name))
            }
        }
    }

    /// 等待 tmp/xijian-<pid>.port 出现并读取实际生效端口
    ///（端口被占用时 Core 会自动换端口，真实端口通过该文件下发）
    private func waitForPortFile(pid: Int32, id: Int) async -> Int? {
        let portFile = runtimeTmpDirectory.appendingPathComponent("xijian-\(pid).port")
        let deadline = Date().addingTimeInterval(tokenTimeout)
        while Date() < deadline {
            if id != operationID { return nil }
            if let data = try? Data(contentsOf: portFile),
               let port = Self.parsePortFileData(data) {
                return port
            }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        return nil
    }

    /// 解析端口文件内容（去空白、必须是 1...65535 的整数），无效时返回 nil。
    nonisolated static func parsePortFileData(_ data: Data) -> Int? {
        guard let text = String(data: data, encoding: .utf8) else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let port = Int(trimmed), (1...65535).contains(port) else { return nil }
        return port
    }

    /// 等待 tmp/xijian.token（macapp 预置的稳定 token 文件）出现并读取。
    /// 仅读取固定名文件；pid-based token 文件已废弃。
    private func waitForToken(pid: Int32, id: Int) async -> String? {
        let tokenFile = runtimeTmpDirectory.appendingPathComponent("xijian.token")
        let deadline = Date().addingTimeInterval(tokenTimeout)
        while Date() < deadline {
            if id != operationID { return nil }
            if let data = try? Data(contentsOf: tokenFile) {
                let value = String(data: data, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if let value, !value.isEmpty { return value }
            }
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        return nil
    }

    /// 生成并写入稳定 token 文件（tmp/xijian.token，0600）。文件已存在时直接复用。
    /// 迁移逻辑：若固定文件不存在，尝试读取旧版 pid-based token 文件（xijian-<pid>.token）
    /// 并迁移到固定文件，避免用户每次升级都需重新登录。
    private func provisionTokenFile() -> URL? {
        let dir = runtimeTmpDirectory
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        } catch {
            return nil
        }
        let file = dir.appendingPathComponent("xijian.token")
        if FileManager.default.fileExists(atPath: file.path) {
            return file
        }
        // 迁移：查找旧版 pid-based token 文件
        if let migratedToken = migrateLegacyTokenFile(in: dir) {
            do {
                try Data(migratedToken.utf8).write(to: file, options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
                appendLog(loc("[XiJian] 已迁移旧版 token 文件到固定位置"))
                return file
            } catch {
                appendLog(loc("[XiJian] 迁移 token 文件失败：%@", error.localizedDescription))
            }
        }
        // 无旧文件：生成新 token
        let token = (0..<32).map { _ in String(format: "%02x", Int.random(in: 0...255)) }.joined()
        do {
            try Data(token.utf8).write(to: file, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
            return file
        } catch {
            return nil
        }
    }

    /// 尝试从旧版 pid-based token 文件迁移 token。
    /// 返回迁移得到的 token 字符串，若无可用旧文件则返回 nil。
    private func migrateLegacyTokenFile(in dir: URL) -> String? {
        guard let files = try? FileManager.default.contentsOfDirectory(atPath: dir.path) else { return nil }
        for name in files {
            guard name.hasPrefix("xijian-"), name.hasSuffix(".token") else { continue }
            // 排除固定名文件
            if name == "xijian.token" { continue }
            let pidPart = name
                .replacingOccurrences(of: "xijian-", with: "")
                .replacingOccurrences(of: ".token", with: "")
            guard let pid = Int32(pidPart), pid > 0 else { continue }
            let oldFile = dir.appendingPathComponent(name)
            if let data = try? Data(contentsOf: oldFile),
               let token = String(data: data, encoding: .utf8)?
                   .trimmingCharacters(in: .whitespacesAndNewlines),
               !token.isEmpty {
                // 清理旧文件
                try? FileManager.default.removeItem(at: oldFile)
                return token
            }
        }
        return nil
    }

    /// 读取进程输出（stdout/stderr 合并），维护环形日志缓冲
    private func observeOutput(_ handle: FileHandle) {
        let task = Task { [weak self] in
            do {
                for try await line in handle.bytes.lines {
                    guard let self else { return }
                    self.appendLog(line)
                }
            } catch {
                // 管道关闭或读取失败时静默结束
            }
        }
        // 保持引用，防止 Task 提前释放
        outputTasks.append(task)
    }

    private var outputTasks: [Task<Void, Never>] = []

    /// 追加日志（环形缓冲，最多 1000 条），按规则识别级别
    func appendLog(_ line: String) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        recentLogs.append(LogEntry.parseAppLine(trimmed))
        if recentLogs.count > 1000 {
            recentLogs.removeFirst(recentLogs.count - 1000)
        }
    }

    // MARK: - 日志（合并查看）

    /// 合并后的全部日志（Core 日志文件 + App 捕获的进程输出），由 refreshLogs() 更新。
    /// 文件行与进程输出内容重叠时以文件为准去重，避免重复展示。
    private(set) var logEntries: [LogEntry] = []
    /// Core 日志文件是否存在（最近一次 refreshLogs/loadCoreLogFile 的结果）
    private(set) var logFileExists = false
    /// 读取 Core 日志文件失败时的错误描述（nil 表示无错误）
    private(set) var logFileLoadError: String?
    /// 最近一次读取的日志文件原始行（供与进程输出去重）
    private var fileRawLines: [String] = []
    /// 日志文件读取上限（字节）：超过后只读尾部，避免大文件卡 UI
    nonisolated static let maxLogFileReadBytes: Int64 = 8 * 1024 * 1024

    /// Core 日志文件路径：~/Library/Application Support/XiJian/Core/logs/xijian-api.log
    var coreLogFileURL: URL? {
        coreDirectory?.appendingPathComponent("logs").appendingPathComponent("xijian-api.log")
    }

    /// 重新读取 Core 日志文件并合并进程输出缓冲，更新 logEntries。
    /// 供日志页 onAppear、手动刷新与进程输出变化时调用。
    func refreshLogs() {
        let fileEntries = loadCoreLogFile()
        var merged = fileEntries
        let raw = fileRawLines
        if raw.isEmpty {
            // 日志文件不存在或为空：全部保留进程输出
            merged.append(contentsOf: recentLogs)
        } else {
            // 文件与进程输出内容重叠：以文件为准，按原始行去重
            let wsnl = CharacterSet.whitespacesAndNewlines
            let rawSet = Set(raw.map { $0.trimmingCharacters(in: wsnl) })
            merged.append(contentsOf: recentLogs.filter { !rawSet.contains($0.message) })
        }
        logEntries = merged
    }

    /// 解析 Core 日志文件（最多 maxLines 行，默认最近 5000 行），返回日志条目。
    /// 文件不存在时返回空数组，并同步更新 logFileExists / logFileLoadError。
    func loadCoreLogFile(maxLines: Int = 5000) -> [LogEntry] {
        guard let url = coreLogFileURL else {
            logFileExists = false
            logFileLoadError = nil
            fileRawLines = []
            return []
        }
        let fm = FileManager.default
        guard fm.fileExists(atPath: url.path) else {
            logFileExists = false
            logFileLoadError = nil
            fileRawLines = []
            return []
        }
        logFileExists = true
        do {
            let rawLines = try Self.readRecentLines(of: url, maxLines: maxLines)
            fileRawLines = rawLines
            logFileLoadError = nil
            return LogEntry.parseCoreLogLines(rawLines)
        } catch {
            logFileLoadError = loc("读取日志文件失败：%@", error.localizedDescription)
            fileRawLines = []
            return []
        }
    }

    /// 读取文件最近 maxLines 行；文件超过读取上限字节时仅读尾部（从第一个完整行开始），
    /// 避免大文件整读卡住 UI。
    nonisolated static func readRecentLines(of url: URL, maxLines: Int) throws -> [String] {
        let fm = FileManager.default
        let attrs = try fm.attributesOfItem(atPath: url.path)
        let size = (attrs[.size] as? NSNumber)?.int64Value ?? 0
        var text: String
        if size > Self.maxLogFileReadBytes {
            let handle = try FileHandle(forReadingFrom: url)
            defer { try? handle.close() }
            try handle.seek(toOffset: UInt64(size - Self.maxLogFileReadBytes))
            let data = handle.readDataToEndOfFile()
            guard let chunk = String(data: data, encoding: .utf8) else {
                throw CocoaError(.fileReadCorruptFile)
            }
            text = chunk
            // 丢弃第一个可能不完整的行（从第一个换行符之后开始）
            if let newline = text.firstIndex(of: "\n") {
                text = String(text[text.index(after: newline)...])
            }
        } else {
            text = try String(contentsOf: url, encoding: .utf8)
        }
        var lines = text.components(separatedBy: CharacterSet.newlines)
        // 去掉首尾空行（文件常以换行结尾，避免空行占用行数上限）
        while let first = lines.first, first.isEmpty { lines.removeFirst() }
        while let last = lines.last, last.isEmpty { lines.removeLast() }
        if lines.count > maxLines {
            lines = Array(lines.suffix(maxLines))
        }
        return lines
    }

    // MARK: - 测试辅助

    /// 测试用：直接设置运行态（不真正启动进程）
    func setRunningForTesting(port: Int, token: String) {
        self.token = token
        self.activePort = port
        self.state = .running(port: port)
    }

    /// 测试用：重置到停止态
    func resetForTesting() {
        operationID += 1
        process?.terminate()
        process = nil
        pid = nil
        token = nil
        activePort = nil
        clientSessionOverride = nil
        recentLogs.removeAll()
        logEntries.removeAll()
        fileRawLines.removeAll()
        logFileExists = false
        logFileLoadError = nil
        state = .stopped
    }

    // MARK: - Config.toml 读写（供 CoreConfigEditorView 使用）

    /// 当前 config.toml 的关键字段快照（用于编辑器回显）
    struct ConfigSnapshot {
        var host: String = "127.0.0.1"
        var port: Int = 18500
        var devMode: Bool = false
        var keepTokenFile: Bool = false
        var driver: String = "auto"
        var baseDir: String = "~/Library/Application Support/XiJian/Core"
        var modelsSubdir: String = "models"
        var seedDefaultData: Bool = false
        var protectionModule: Bool = true
        var rateLimit: Bool = false
        var overloadMonitor: Bool = true
        var overloadTier: String = "medium"
    }

    /// 读取 config.toml 并返回关键字段（解析失败时返回默认值）
    var currentConfig: ConfigSnapshot {
        var snap = ConfigSnapshot()
        guard let coreDir = coreDirectory,
              let baseURL = URL(string: "file://\(coreDir.path)"),
              let configURL = baseURL.appendingPathComponent("config.toml") as URL?,
              FileManager.default.fileExists(atPath: configURL.path),
              let content = try? String(contentsOf: configURL, encoding: .utf8) else {
            return snap
        }
        // Simple TOML parsing for known keys
        let newlineSet = CharacterSet.newlines
        let whitespaceNewlineSet = CharacterSet.whitespacesAndNewlines
        for line in content.components(separatedBy: newlineSet) {
            let trimmed = line.trimmingCharacters(in: whitespaceNewlineSet)
            if trimmed.hasPrefix("#") || trimmed.isEmpty { continue }
            if let eq = trimmed.firstIndex(of: "=") {
                let key = trimmed[..<eq].trimmingCharacters(in: whitespaceNewlineSet)
                let val = trimmed[trimmed.index(after: eq)...].trimmingCharacters(in: whitespaceNewlineSet)
                    .trimmingCharacters(in: CharacterSet(charactersIn: "\"\'"))
                switch key {
                case "host": snap.host = val
                case "port": snap.port = Int(val) ?? 18500
                case "dev": snap.devMode = ["true","1","yes","on"].contains(val.lowercased())
                case "keep_token_file": snap.keepTokenFile = ["true","1","yes","on"].contains(val.lowercased())
                case "driver": snap.driver = val
                case "base_dir": snap.baseDir = val
                case "models_subdir": snap.modelsSubdir = val
                case "seed_default_data": snap.seedDefaultData = ["true","1","yes","on"].contains(val.lowercased())
                case "protection_module": snap.protectionModule = ["true","1","yes","on"].contains(val.lowercased())
                case "rate_limit": snap.rateLimit = ["true","1","yes","on"].contains(val.lowercased())
                case "monitor": snap.overloadMonitor = ["true","1","yes","on"].contains(val.lowercased())
                case "tier": snap.overloadTier = val
                default: break
                }
            }
        }
        return snap
    }

    /// 将关键字段写回 config.toml（保留其它字段、注释、格式）
    func updateConfig(
        host: String,
        port: Int,
        devMode: Bool,
        keepTokenFile: Bool,
        driver: String,
        baseDir: String,
        modelsSubdir: String,
        seedDefaultData: Bool,
        protectionModule: Bool,
        rateLimit: Bool,
        overloadMonitor: Bool,
        overloadTier: String
    ) throws {
        guard let coreDir = coreDirectory else {
            throw NSError(domain: "CoreManager", code: -1, userInfo: [NSLocalizedDescriptionKey: "Core directory not found"])
        }
        let configURL = coreDir.appendingPathComponent("config.toml")
        var content: String
        if FileManager.default.fileExists(atPath: configURL.path) {
            content = try String(contentsOf: configURL, encoding: .utf8)
        } else {
            content = ""
        }
        let newlineSet = CharacterSet.newlines
        let whitespaceNewlineSet = CharacterSet.whitespacesAndNewlines
        var lines = content.components(separatedBy: newlineSet)
        var updates: [String: String] = [
            "host": host,
            "port": String(port),
            "dev": devMode ? "true" : "false",
            "keep_token_file": keepTokenFile ? "true" : "false",
            "driver": driver,
            "base_dir": baseDir,
            "models_subdir": modelsSubdir,
            "seed_default_data": seedDefaultData ? "true" : "false",
            "protection_module": protectionModule ? "true" : "false",
            "rate_limit": rateLimit ? "true" : "false",
            "monitor": overloadMonitor ? "true" : "false",
            "tier": overloadTier,
        ]
        // Update existing keys
        for i in 0..<lines.count {
            let trimmed = lines[i].trimmingCharacters(in: whitespaceNewlineSet)
            if trimmed.hasPrefix("#") || trimmed.isEmpty { continue }
            if let eq = trimmed.firstIndex(of: "=") {
                let key = trimmed[..<eq].trimmingCharacters(in: whitespaceNewlineSet)
                if let newVal = updates[key] {
                    // Preserve leading whitespace and inline comment
                    let leading = lines[i].prefix { $0 == " " || $0 == "\t" }
                    var comment = ""
                    if let hashIdx = trimmed.firstIndex(of: "#") {
                        comment = " " + String(trimmed[hashIdx...])
                    }
                    lines[i] = leading + key + " = \"" + newVal + "\"" + comment
                    updates.removeValue(forKey: key)
                }
            }
        }
        // Append missing keys under appropriate sections
        if !updates.isEmpty {
            // Simple strategy: append at end
            lines.append("")
            lines.append("# Added by CoreConfigEditorView")
            for (key, val) in updates {
                lines.append("\(key) = \"\(val)\"")
            }
        }
        let newContent = lines.joined(separator: "\n")
        try newContent.write(to: configURL, atomically: true, encoding: .utf8)
    }
}

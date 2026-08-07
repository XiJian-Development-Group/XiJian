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
/// 4. 轮询 `GET /healthz` 直到 200（超时 60 秒）
/// 5. 读取 `run/xijian-<pid>.token` 作为 Bearer token
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
    /// Bearer token（从 run/xijian-<pid>.token 读取）
    private(set) var token: String?
    /// 子进程 PID
    private(set) var pid: Int32?
    /// 最近捕获的进程输出（环形缓冲，用于诊断）
    private(set) var recentLogs: [String] = []

    // MARK: - 连接设置（UserDefaults 持久化）

    /// 端口（默认 18500，与 Core 的 DEFAULT_PORT 一致）
    var port: Int {
        didSet {
            let clamped = min(max(port, 1), 65535)
            if port != clamped { port = clamped; return }
            UserDefaults.standard.set(port, forKey: Self.portKey)
        }
    }

    /// 是否使用自定义服务器（跳过本机 Core 进程管理）
    var useCustomServer: Bool {
        didSet { UserDefaults.standard.set(useCustomServer, forKey: Self.customServerKey) }
    }

    /// 自定义服务器地址（如 http://127.0.0.1:18500）
    var customBaseURL: String {
        didSet { UserDefaults.standard.set(customBaseURL, forKey: Self.customURLKey) }
    }

    /// 自定义服务器访问令牌（可选，UserDefaults 持久化）
    var customToken: String {
        didSet { UserDefaults.standard.set(customToken, forKey: Self.customTokenKey) }
    }

    // MARK: - 内部状态

    private var process: Process?
    private var operationID = 0
    private var isStopping = false

    // MARK: - 测试钩子（默认值即生产行为）

    /// 测试用：覆盖 bundle 内 Core 路径探测
    var bundleCoreOverride: URL?
    /// 测试用：覆盖 Core 运行目录（隔离测试数据）
    var isolatedCoreDirectoryOverride: URL?
    /// 健康检查超时（秒）
    var healthTimeout: TimeInterval = 60
    /// 健康检查轮询间隔（秒）
    var healthPollInterval: TimeInterval = 0.3
    /// token 文件等待超时（秒）
    var tokenTimeout: TimeInterval = 30

    // MARK: - 初始化

    private init() {
        let defaults = UserDefaults.standard
        port = defaults.object(forKey: Self.portKey) as? Int ?? 18500
        useCustomServer = defaults.bool(forKey: Self.customServerKey)
        customBaseURL = defaults.string(forKey: Self.customURLKey) ?? ""
        customToken = defaults.string(forKey: Self.customTokenKey) ?? ""
    }

    // MARK: - 路径

    private static let portKey = "xijian.core.port"
    private static let customServerKey = "xijian.core.useCustomServer"
    private static let customURLKey = "xijian.core.customBaseURL"
    private static let customTokenKey = "xijian.core.customToken"

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
    var appSupportDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support")
        return base.appendingPathComponent("XiJian", isDirectory: true)
    }

    /// Core 运行目录：~/Library/Application Support/XiJian/Core
    public var coreDirectory: URL? {
        if let override = isolatedCoreDirectoryOverride { return override }
        return appSupportDirectory.appendingPathComponent("Core", isDirectory: true)
    }

    /// 数据存储根目录（Core 统一存储，见 runtime.default_storage_dir）
    var dataDirectory: URL {
        appSupportDirectory.appendingPathComponent("Data", isDirectory: true)
    }

    /// 有效端口：自定义服务器时取自定义 URL 中的端口，否则取设置端口
    var effectivePort: Int {
        if useCustomServer, let url = URL(string: customBaseURL), let p = url.port {
            return p
        }
        return port
    }

    /// API base URL（http://127.0.0.1:<port> 或自定义）
    var baseURL: URL {
        if useCustomServer, let url = URL(string: customBaseURL), !customBaseURL.isEmpty {
            return url
        }
        return URL(string: "http://127.0.0.1:\(port)")!
    }

    // MARK: - 启动

    /// 启动 Core（幂等：已在运行或启动中则直接返回）
    public func startCore() async {
        // 使用自定义服务器时不管理本机 Core 进程：直接进入自定义服务器状态，不启动子进程。
        if useCustomServer {
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

        // 1. bundle 内检查
        guard let bundleCore = bundleCoreURL else {
            state = .error("未找到内置 Core 资源（Resources/Core/xijian-api）。请先运行 macapp/build-core.sh 构建 Core 后再启动。")
            return
        }
        guard let coreDir = coreDirectory else {
            state = .error("无法解析 Core 运行目录。")
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
                state = .error("复制 Core 到应用数据目录失败：\(error.localizedDescription)")
                return
            case .success:
                break
            }
        }

        // 3. 启动子进程
        guard FileManager.default.isExecutableFile(atPath: exeURL.path) else {
            state = .error("Core 可执行文件不存在或不可执行：\(exeURL.path)")
            return
        }

        state = .starting
        let launchPort = effectivePort

        let proc = Process()
        proc.executableURL = exeURL
        proc.arguments = ["--port", "\(launchPort)"]
        proc.currentDirectoryURL = coreDir
        var env = ProcessInfo.processInfo.environment
        // 注意：不设置 XIJIAN_DATA_DIR —— 让 runtime.py 使用默认存储根
        //（即可执行文件同级目录 ~/Library/Application Support/XiJian/Core），
        // 使 onedir 运行时、config.toml、logs/、run/ 与数据（xijian.db 等）保持同一目录。
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
        } catch {
            process = nil
            pid = nil
            state = .error("启动 Core 进程失败：\(error.localizedDescription)")
            return
        }

        proc.terminationHandler = { [weak self] finished in
            Task { @MainActor in
                self?.handleTermination(finished)
            }
        }

        appendLog("[XiJian] 已启动 Core 进程 pid=\(proc.processIdentifier) port=\(launchPort)")

        // 4. 轮询健康检查
        let ready = await waitForHealth(port: launchPort, id: myID)
        guard myID == operationID else { return }
        guard ready else {
            if let p = process, p.isRunning { p.terminate() }
            state = .error("Core 启动超时（\(Int(healthTimeout)) 秒内未就绪）。请查看日志或尝试重启。")
            return
        }

        // 5. 读取 token
        let token = await waitForToken(pid: proc.processIdentifier, id: myID)
        guard myID == operationID else { return }
        self.token = token
        appendLog("[XiJian] Core 就绪：http://127.0.0.1:\(launchPort)（token 已读取）")
        state = .running(port: launchPort)
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
            state = .stopped
            isStopping = false
            return
        }

        appendLog("[XiJian] 正在停止 Core（SIGTERM）...")
        proc.terminate()

        let deadline = Date().addingTimeInterval(8)
        while proc.isRunning && Date() < deadline {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        if proc.isRunning {
            appendLog("[XiJian] 未响应 SIGTERM，发送 SIGINT ...")
            proc.interrupt()
            try? await Task.sleep(nanoseconds: 800_000_000)
        }
        if proc.isRunning {
            appendLog("[XiJian] 仍未退出，强制结束（SIGKILL）...")
            kill(proc.processIdentifier, SIGKILL)
        }

        process = nil
        pid = nil
        token = nil
        isStopping = false
        state = .stopped
        appendLog("[XiJian] Core 已停止")
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
        appendLog("[XiJian] 已重置 Core 数据目录：\(coreDir.path)")
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
            return APIClient(baseURL: baseURL, token: customToken)
        }
        guard case .running = state, let token = token, !token.isEmpty else { return nil }
        return APIClient(baseURL: baseURL, token: token)
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
            ? "退出码 \(finished.terminationStatus)"
            : "信号 \(finished.terminationStatus)"
        appendLog("[XiJian] Core 进程意外退出（\(code)）")
        process = nil
        pid = nil
        token = nil
        if case .running = state {
            state = .error("Core 进程意外退出（\(code)）。可在设置中查看日志或重启 Core。")
        } else if case .starting = state {
            state = .error("Core 启动失败（\(code)）。请查看日志。")
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

    /// 等待 run/xijian-<pid>.token 出现并读取
    private func waitForToken(pid: Int32, id: Int) async -> String? {
        guard let coreDir = coreDirectory else { return nil }
        let tokenFile = coreDir.appendingPathComponent("run").appendingPathComponent("xijian-\(pid).token")
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

    /// 追加日志（环形缓冲，最多 1000 行）
    func appendLog(_ line: String) {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        recentLogs.append(trimmed)
        if recentLogs.count > 1000 {
            recentLogs.removeFirst(recentLogs.count - 1000)
        }
    }

    // MARK: - 测试辅助

    /// 测试用：直接设置运行态（不真正启动进程）
    func setRunningForTesting(port: Int, token: String) {
        self.token = token
        self.state = .running(port: port)
    }

    /// 测试用：重置到停止态
    func resetForTesting() {
        operationID += 1
        process?.terminate()
        process = nil
        pid = nil
        token = nil
        recentLogs.removeAll()
        state = .stopped
    }
}

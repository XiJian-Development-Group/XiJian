import Foundation

/// Hermes 子进程管理：start / stop / restart / 健康检查 / 日志 tail。
///
/// 设计原则：
/// - `actor` 隔离，外部访问通过 await
/// - 日志通过 FileHandle 异步 tail 到 stdout
/// - 健康检查委托给 healthCheck 闭包（灵活：端口探测 / 文件存在 / 自定义）
/// - stop 用 SIGTERM 给进程 5s 优雅退出，否则 SIGKILL
public actor HermesProcess {
    public enum State: String, Sendable {
        case stopped = "stopped"
        case starting = "starting"
        case running = "running"
        case stopping = "stopping"
        case crashed = "crashed"
    }

    public struct Config: Sendable {
        public var command: String
        public var arguments: [String]
        public var environment: [String: String]
        public var workingDirectory: URL?
        public var logFile: URL
        public var healthCheck: (@Sendable () async -> Bool)?

        public init(
            command: String,
            arguments: [String] = [],
            environment: [String: String] = [:],
            workingDirectory: URL? = nil,
            logFile: URL,
            healthCheck: (@Sendable () async -> Bool)? = nil
        ) {
            self.command = command
            self.arguments = arguments
            self.environment = environment
            self.workingDirectory = workingDirectory
            self.logFile = logFile
            self.healthCheck = healthCheck
        }
    }

    public let id: UUID
    public private(set) var state: State = .stopped
    private(set) public var process: Process?
    private(set) public var lastExitCode: Int32?
    public let config: Config

    public init(config: Config, id: UUID = UUID()) {
        self.id = id
        self.config = config
    }

    public func start() async throws {
        guard state == .stopped || state == .crashed else {
            throw ProcessError.alreadyRunning
        }
        state = .starting

        // 确保日志目录 + 文件存在（hermes可能不写，文件可能不存在）
        let logDir = config.logFile.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: config.logFile.path) {
            FileManager.default.createFile(atPath: config.logFile.path, contents: Data())
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: config.command)
        proc.arguments = config.arguments
        if !config.environment.isEmpty {
            proc.environment = config.environment
        }
        proc.currentDirectoryURL = config.workingDirectory
        let logHandle = try FileHandle(forWritingTo: config.logFile)
        proc.standardOutput = logHandle
        proc.standardError = logHandle

        // terminationHandler 仅在 stop() 没主动设置 state 时兜底
        proc.terminationHandler = { [weak self] p in
            let code = p.terminationStatus
            // 延时跑，给 stop() 优先写 state 的机会
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 100_000_000)
                await self?.handleTermination(exitCode: code)
            }
        }

        try proc.run()
        self.process = proc
        state = .running
    }

    public func stop(timeout: TimeInterval = 5.0) async throws {
        guard let proc = process, state == .running else { return }
        state = .stopping
        proc.terminate()  // SIGTERM
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline, proc.isRunning {
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
        proc.waitUntilExit()
        // 主动 stop 永远算正常退出（不论 terminationStatus，因为 SIGTERM=15 也合理）
        lastExitCode = proc.terminationStatus
        state = .stopped
        process = nil
    }

    public func restart(timeout: TimeInterval = 5.0) async throws {
        try? await stop(timeout: timeout)
        try await start()
    }

    public func isHealthy() async -> Bool {
        guard state == .running else { return false }
        if let check = config.healthCheck {
            return await check()
        }
        // 默认检查：进程在跑就算健康
        return process?.isRunning ?? false
    }

    public func exitCode() async -> Int32? {
        lastExitCode
    }

    private func handleTermination(exitCode: Int32) {
        // 仅在 stop() 未主动设置 state 时兜底更新
        guard state == .running || state == .starting else { return }
        lastExitCode = exitCode
        if exitCode == 0 {
            state = .stopped
        } else {
            state = .crashed
        }
        process = nil
    }
}

public enum ProcessError: Error, LocalizedError, Sendable {
    case alreadyRunning
    case notRunning
    case launchFailed(reason: String)

    public var errorDescription: String? {
        switch self {
        case .alreadyRunning: return "进程已在运行"
        case .notRunning: return "进程未在运行"
        case .launchFailed(let reason): return "启动失败：\(reason)"
        }
    }
}

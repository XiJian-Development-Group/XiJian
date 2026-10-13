import Foundation

/// Hermes CLI 命令包装：通过 subprocess 调 hermes 二进制并解析输出。
///
/// 设计：
/// - 使用 `Process` + `Pipe` 启动 hermes
/// - hermes 输出端口时写到 `xijian-<pid>.port` 文件（在 hermes 安装目录下），
///   我们读这个文件获取实际端口
/// - gateway 启动通过 `hermes cli --port=9119 gateway start` 或类似命令；
///   实际命令语法以用户 Hermes 配置为准（Phase 1.7 的 MVP 用通用约定）
public actor HermesCommandRunner {
    public static let shared = HermesCommandRunner(
        hermesPath: URL(fileURLWithPath: ("~/.hermes/hermes-agent/venv/bin/hermes" as NSString).expandingTildeInPath)
    )

    public enum Command: String, Sendable {
        case gatewayStart = "gateway start"
        case gatewayStop = "gateway stop"
        case gatewayRestart = "gateway restart"
        case gatewayStatus = "gateway status"
        case listProfiles = "profile list"
        case switchProfile = "profile use"
        case installSkill = "skill install"
        case listSkills = "skill list"
    }

    public struct CommandResult: Sendable {
        public let exitCode: Int32
        public let stdout: String
        public let stderr: String
    }

    private let hermesPath: URL
    private var workingDirectory: URL?

    public init(hermesPath: URL, workingDirectory: URL? = nil) {
        self.hermesPath = hermesPath
        self.workingDirectory = workingDirectory
    }

    /// 执行 hermes CLI 命令并返回结果。
    public func run(_ command: Command, extraArguments: [String] = []) async throws -> CommandResult {
        let proc = Process()
        proc.executableURL = hermesPath
        proc.arguments = command.rawValue.split(separator: " ").map(String.init) + extraArguments
        if let cwd = workingDirectory {
            proc.currentDirectoryURL = cwd
        }
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        proc.standardOutput = stdoutPipe
        proc.standardError = stderrPipe
        try proc.run()
        proc.waitUntilExit()
        let stdoutData = stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        let stderrData = stderrPipe.fileHandleForReading.readDataToEndOfFile()
        return CommandResult(
            exitCode: proc.terminationStatus,
            stdout: String(data: stdoutData, encoding: .utf8) ?? "",
            stderr: String(data: stderrData, encoding: .utf8) ?? ""
        )
    }

    /// 启动 gateway 并返回实际端口。等待 port 文件出现（hermes 写入）。
    public func startGatewayAndAwaitPort(timeout: TimeInterval = 10) async throws -> Int {
        _ = try await run(.gatewayStart)
        // hermes 启动后会在 hermes 安装目录的 tmp/ 下写 xijian-<pid>.port 文件
        // MVP 简化：返回约定默认端口
        // TODO Phase 1.7: 解析 port 文件
        return 9119
    }

    /// 检查 gateway 是否在给定端口运行。
    public func checkDashboardReachable(port: Int) async -> Bool {
        let url = URL(string: "http://127.0.0.1:\(port)/")!
        var request = URLRequest(url: url)
        request.httpMethod = "HEAD"
        request.timeoutInterval = 2
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            return (response as? HTTPURLResponse) != nil
        } catch {
            return false
        }
    }

    /// 计算 dashboard URL。
    public func dashboardURL(port: Int) -> URL? {
        URL(string: "http://127.0.0.1:\(port)/")
    }

    /// 启动指定 profile（spawn 一个新的 hermes 进程）。
    public func startProfile(_ name: String) async throws {
        let result = try await run(.gatewayStart, extraArguments: ["--profile", name])
        if result.exitCode != 0 {
            throw CommandError.nonZeroExit(code: result.exitCode, stderr: result.stderr)
        }
    }

    /// 停止指定 profile。
    public func stopProfile(_ name: String) async throws {
        let result = try await run(.gatewayStop, extraArguments: ["--profile", name])
        if result.exitCode != 0 {
            throw CommandError.nonZeroExit(code: result.exitCode, stderr: result.stderr)
        }
    }

    /// 给指定 profile 发送一条聊天消息（阻塞到收到回复）。
    public func sendChatMessage(profile: String, message: String) async -> Bool {
        do {
            let result = try await run(.gatewayStart, extraArguments: [
                "--profile", profile,
                "--message", message,
                "--once",  // 单条消息后退出
            ])
            return result.exitCode == 0
        } catch {
            return false
        }
    }
}

public enum CommandError: Error, LocalizedError, Sendable {
    case nonZeroExit(code: Int32, stderr: String)
    case launchFailed(reason: String)

    public var errorDescription: String? {
        switch self {
        case .nonZeroExit(let code, let stderr):
            return "命令退出码 \(code)：\(stderr)"
        case .launchFailed(let reason):
            return "启动失败：\(reason)"
        }
    }
}

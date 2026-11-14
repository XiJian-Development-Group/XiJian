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

    /// 要注入给子进程的环境变量。
    ///
    /// **这是「网络加速」真正落地的地方。** `hermes update`、官方安装脚本
    /// 都在子进程里跑，光在界面上显示选了哪个源，它们根本不知道 ——
    /// 必须把 UV_INDEX_URL、npm registry、git insteadOf 这些变量真的塞进
    /// 进程环境里。
    private var environmentOverrides: [String: String]

    public init(
        hermesPath: URL,
        workingDirectory: URL? = nil,
        environmentOverrides: [String: String] = [:]
    ) {
        self.hermesPath = hermesPath
        self.workingDirectory = workingDirectory
        self.environmentOverrides = environmentOverrides
    }

    /// 换上新的环境变量（探测完镜像之后调用）。
    public func setEnvironmentOverrides(_ overrides: [String: String]) {
        environmentOverrides = overrides
    }

    /// 当前会注入的环境变量。
    public func currentEnvironmentOverrides() -> [String: String] { environmentOverrides }

    /// 执行 hermes CLI 命令并返回结果。
    public func run(_ command: Command, extraArguments: [String] = []) async throws -> CommandResult {
        try await run(arguments: command.rawValue.split(separator: " ").map(String.init) + extraArguments)
    }

    /// 直接跑一串参数。
    ///
    /// `--port` 得放在子命令前面（`hermes cli --port 9119 gateway start`），
    /// 所以 gateway 不能靠 extraArguments 拼，得整串自己给。
    public func run(arguments: [String]) async throws -> CommandResult {
        let proc = Process()
        proc.executableURL = hermesPath
        proc.arguments = arguments
        if let cwd = workingDirectory {
            proc.currentDirectoryURL = cwd
        }
        if !environmentOverrides.isEmpty {
            var environment = ProcessInfo.processInfo.environment
            for (key, value) in environmentOverrides {
                environment[key] = value
            }
            proc.environment = environment
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

    /// 启动 gateway 并返回它真正用的端口。
    ///
    /// 端口不能写死：本机 9119 常年被别的进程占着，gateway 起不来，界面
    /// 只能干等。先抢一个空端口，把它交给 hermes，再等它真的通。
    public func startGatewayAndAwaitPort(timeout: TimeInterval = 10) async throws -> Int {
        let port = Self.findFreePort()
        let result = try await run(arguments: Self.gatewayStartArguments(port: port))
        if result.exitCode != 0 {
            throw HermesCommandError.commandFailed(result.stderr.isEmpty ? result.stdout : result.stderr)
        }
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if await checkDashboardReachable(port: port) { return port }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        throw HermesCommandError.timeout(port)
    }

    /// 启动 gateway 的那条命令。端口在前，子命令在后。
    public static func gatewayStartArguments(port: Int) -> [String] {
        ["cli", "--port", String(port), "gateway", "start"]
    }

    /// 找一个没人占着的端口。
    ///
    /// 抢一个空端口、记下号、把 socket 关掉 —— 号就归我们了，hermes 起来
    /// 的那一瞬间没人抢得到。
    public static func findFreePort() -> Int {
        // 抢端口偶尔会失败（正好被别人抢走），重试几次。
        // 找不到就返回 0 —— 返回一个「反正 9119 也没人用」的假端口，
        // 只会让 gateway 带着一个撞车的端口起来。
        for _ in 0..<5 {
            let socket = DarwinSocket.make()
            guard socket >= 0 else { continue }
            defer { DarwinSocket.closeSocket(socket) }
            guard DarwinSocket.bindLoopback(socket) else { continue }
            if let port = DarwinSocket.portOf(socket), port > 0 {
                return port
            }
        }
        return 0
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

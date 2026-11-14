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

    /// Hermes CLI 的真实子命令。
    ///
    /// **一条条对着 `hermes --help` 核过。** 以前这里写的 `profile list` /
    /// `profile use` / `skill install` —— Hermes 根本没有这三条命令，点了
    /// 必然回一句「unrecognized arguments」。profile 是全局参数 `-p <名字>`，
    /// 技能在 `hermes skills` 下面。
    public enum Command: String, Sendable {
        // 消息网关
        case gatewayRun = "gateway run"
        case gatewayStart = "gateway start"
        case gatewayStop = "gateway stop"
        case gatewayRestart = "gateway restart"
        case gatewayStatus = "gateway status"
        case gatewayList = "gateway list"
        // 体检与总状态
        case doctor = "doctor"
        case status = "status"
        // 定时任务
        case cronList = "cron list"
        case cronDoctor = "cron doctor"
        // 会话
        case sessionsList = "sessions list"
        // 记忆
        case memoryStatus = "memory status"
        // 技能
        case skillsList = "skills list"
        case skillsInstall = "skills install"
        // 升级
        case update = "update"
        // 聊天（一条消息就退出）
        case chat = "chat"
        // dashboard（web 界面）
        case dashboard = "dashboard"
    }

    public struct CommandResult: Sendable {
        public let exitCode: Int32
        public let stdout: String
        public let stderr: String
    }

    private let hermesPath: URL
    private var workingDirectory: URL?
    /// XiJian 自己拉起来的 dashboard 进程。停的时候要收掉。
    private var dashboardProcess: Process?

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
    ///
    /// `profile` 是**全局**参数，得放在子命令前面：`hermes -p ops gateway run`。
    /// 放到后面 hermes 不认。
    public func run(_ command: Command, profile: String? = nil,
                    extraArguments: [String] = []) async throws -> CommandResult {
        try await run(arguments: Self.arguments(for: command, profile: profile,
                                                extraArguments: extraArguments))
    }

    /// 拼一条命令的参数。单独拆出来是为了能离线测。
    public static func arguments(for command: Command, profile: String? = nil,
                                 extraArguments: [String] = []) -> [String] {
        var arguments: [String] = []
        if let profile, !profile.isEmpty {
            arguments += ["-p", profile]
        }
        arguments += command.rawValue.split(separator: " ").map(String.init)
        arguments += extraArguments
        return arguments
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

    /// 启动 dashboard，返回它真正用的端口。
    ///
    /// **9119 是 dashboard 的端口，不是消息网关的。** 消息网关（QQ／微信／
    /// 钉钉）根本不占 HTTP 端口，dashboard 才是那个 web 界面。
    ///
    /// 已经在跑就直接用它的端口，不另起一个；没有就自己抢一个空端口。
    public func startDashboardAndAwaitPort(timeout: TimeInterval = 20) async throws -> Int {
        if let existing = await discoverDashboardPort() { return existing }
        let port = Self.findFreePort()
        guard port > 0 else {
            throw HermesCommandError.commandFailed("找不到空闲的端口")
        }
        try spawnDashboard(port: port)
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if await checkDashboardReachable(port: port) { return port }
            try await Task.sleep(nanoseconds: 300_000_000)
        }
        throw HermesCommandError.timeout(port)
    }

    /// 已经在跑的 dashboard 用的是哪个端口。
    public func discoverDashboardPort() async -> Int? {
        guard let result = try? await run(.dashboard, extraArguments: ["--status"]) else {
            return nil
        }
        return Self.parseDashboardPort(result.stdout)
    }

    /// 从 `hermes dashboard --status` 的输出里把端口抠出来。
    ///
    /// 输出长这样：
    ///     PID 1456 [dashboard]: ... 'dashboard', '--port', '9119', ...
    public static func parseDashboardPort(_ output: String) -> Int? {
        for line in output.split(separator: "\n") {
            guard line.contains("dashboard") else { continue }
            let tokens = line.split(separator: " ").map { clean($0) }
            guard let index = tokens.firstIndex(of: "--port"), index + 1 < tokens.count else {
                continue
            }
            if let port = Int(tokens[index + 1]) { return port }
        }
        return nil
    }

    /// 把引号和逗号去掉。
    private static func clean(_ token: Substring) -> String {
        String(token).trimmingCharacters(in: CharacterSet(charactersIn: "'\","))
    }

    /// 启动 dashboard 的那条命令。--no-open 免得自己弹浏览器。
    public static func dashboardStartArguments(port: Int) -> [String] {
        ["dashboard", "--port", String(port), "--host", "127.0.0.1", "--no-open"]
    }

    /// 把 dashboard 拉起来，不等它退出。
    ///
    /// `hermes dashboard` 可能一直待在前台不退出，所以这里只管把它拉起来，
    /// 端口通不通由调用方轮询。
    @discardableResult
    public func spawnDashboard(port: Int) throws -> Process {
        let proc = Process()
        proc.executableURL = hermesPath
        proc.arguments = Self.dashboardStartArguments(port: port)
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
        try proc.run()
        dashboardProcess = proc
        return proc
    }

    /// 把 dashboard 停掉。
    public func stopDashboard() {
        guard let proc = dashboardProcess else { return }
        proc.terminate()
        dashboardProcess = nil
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

    /// 启动指定 profile 的消息网关。
    ///
    /// profile 是全局参数：`hermes -p ops gateway start`。
    public func startProfile(_ name: String) async throws {
        let result = try await run(.gatewayStart, profile: name)
        if result.exitCode != 0 {
            throw CommandError.nonZeroExit(code: result.exitCode, stderr: result.stderr)
        }
    }

    /// 停止指定 profile。
    public func stopProfile(_ name: String) async throws {
        let result = try await run(.gatewayStop, profile: name)
        if result.exitCode != 0 {
            throw CommandError.nonZeroExit(code: result.exitCode, stderr: result.stderr)
        }
    }

    /// 给指定 profile 发送一条聊天消息（阻塞到收到回复）。
    ///
    /// 真实命令是 `hermes -p <profile> chat -q "..." --oneshot`。
    public func sendChatMessage(profile: String, message: String) async -> Bool {
        do {
            let result = try await run(.chat, profile: profile,
                                       extraArguments: ["-q", message, "--oneshot"])
            return result.exitCode == 0
        } catch {
            return false
        }
    }

    // -- 其它子系统 -----------------------------------------------------------

    /// 体检：hermes doctor。
    public func runDoctor() async throws -> CommandResult { try await run(.doctor) }

    /// 定时任务列表。
    public func cronJobs() async throws -> CommandResult { try await run(.cronList) }

    /// 定时任务体检。
    public func cronDoctor() async throws -> CommandResult { try await run(.cronDoctor) }

    /// 会话列表。
    public func sessions() async throws -> CommandResult { try await run(.sessionsList) }

    /// 记忆提供方状态。
    public func memoryStatus() async throws -> CommandResult { try await run(.memoryStatus) }

    /// 技能列表。
    public func skills() async throws -> CommandResult { try await run(.skillsList) }

    /// 升级 Hermes（要联网，慢）。
    public func updateHermes() async throws -> CommandResult { try await run(.update) }
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

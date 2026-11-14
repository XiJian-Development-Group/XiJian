import Foundation
import SwiftUI
import Observation

/// 应用生命周期管理器：管理 Hermes 安装 / 启动 / 停止 / 重启 / Dashboard 探测。
@Observable
@MainActor
public final class AppLifecycleManager {

    public static let shared = AppLifecycleManager()

    /// Hermes gateway 当前状态
    public enum GatewayState: String, Sendable {
        case stopped = "未启动"
        case starting = "启动中"
        case running = "运行中"
        case stopping = "停止中"
        case crashed = "崩溃"
        case notInstalled = "未安装"

        public var label: String {
            switch self {
            case .stopped: return loc("未启动")
            case .starting: return loc("启动中")
            case .running: return loc("运行中")
            case .stopping: return loc("停止中")
            case .crashed: return loc("崩溃")
            case .notInstalled: return loc("未安装")
            }
        }

        public var color: Color {
            switch self {
            case .stopped, .notInstalled: return .secondary
            case .starting, .stopping: return .orange
            case .running: return .green
            case .crashed: return .red
            }
        }
    }

    public private(set) var gatewayState: GatewayState = .stopped
    public private(set) var dashboardPort: Int?
    public private(set) var lastError: String?

    /// Hermes 日志目录（~/Library/Logs/XiJianManager/）
    public let logsDirectory: URL

    private let installer = HermesInstaller()
    private var hermesProcess: HermesProcess?
    public private(set) var commandRunner: HermesCommandRunner?
    private var logTailer: LogTailer?
    public private(set) var installedHermesPath: URL?

    public init() {
        let logsBase = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first!
        self.logsDirectory = logsBase
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("XiJianManager", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsDirectory, withIntermediateDirectories: true)
    }

    /// 启动时检查 Hermes 是否已安装，未装则标记 notInstalled；已装则启动 gateway。
    public func ensureHermesInstalledAndRunning() async {
        let status = await installer.detect()
        if !status.installed || status.path == nil {
            gatewayState = .notInstalled
            return
        }
        installedHermesPath = status.path
        commandRunner = HermesCommandRunner(hermesPath: status.path!)
        do {
            try await startGateway()
        } catch {
            lastError = "启动失败：\(error)"
            gatewayState = .crashed
        }
    }

    public func startGateway() async throws {
        guard let path = installedHermesPath else {
            gatewayState = .notInstalled
            throw InstallError.notInstalled
        }
        gatewayState = .starting

        let logFile = logsDirectory.appendingPathComponent("hermes.log")
        // 消息网关跟 dashboard 是两回事：
        // - gateway 是消息平台的前台进程（`hermes gateway run`），它**不占** HTTP 端口；
        // - dashboard 才是那个 web 界面（`hermes dashboard --port N`），界面里嵌的是它。
        let config = HermesProcess.Config(
            command: path.path,
            arguments: ["gateway", "run"],
            logFile: logFile,
            // 默认的健康检查就是「进程还活着」——网关的健康本来就是这个意思。
            healthCheck: nil
        )
        let proc = HermesProcess(config: config)
        try await proc.start()
        hermesProcess = proc

        let tailer = LogTailer()
        logTailer = tailer
        try? await tailer.start(file: logFile) { [weak self] _ in
            _ = self
        }

        gatewayState = .running

        // 前台跑不起来的时候（系统服务已经托管这个 profile，hermes 会拒绝
        // 起第二个 dispatcher），退回去用服务命令。
        try await Task.sleep(nanoseconds: 1_500_000_000)
        if await proc.isHealthy() == false, let runner = commandRunner {
            let service = try? await runner.run(.gatewayStart)
            if service?.exitCode == 0 {
                try? await proc.stop(timeout: 1.0)
                hermesProcess = nil
                gatewayState = .running
                await startDashboard()
                return
            }
            lastError = "消息网关起不来：\(service?.stderr ?? "")"
        }

        await startDashboard()
    }

    /// 把 dashboard 拉起来。已经在跑就用它的端口，没有就自己抢一个。
    public func startDashboard() async {
        guard let runner = commandRunner else { return }
        do {
            dashboardPort = try await runner.startDashboardAndAwaitPort()
        } catch {
            lastError = "dashboard 启动失败：\(error)"
        }
    }

    public func stopGateway() async throws {
        guard let proc = hermesProcess else {
            gatewayState = .stopped
            return
        }
        gatewayState = .stopping
        await logTailer?.stop()
        logTailer = nil
        try await proc.stop(timeout: 3.0)
        hermesProcess = nil
        await commandRunner?.stopDashboard()
        dashboardPort = nil
        gatewayState = .stopped
    }

    public func restartGateway() async throws {
        try await stopGateway()
        try await startGateway()
    }

    /// 同步停止（应用退出时调用）。
    public func stopGatewaySync() {
        // 直接 SIGTERM（不能 await）
        let proc = hermesProcess
        Task { [weak proc] in
            try? await proc?.stop(timeout: 1.0)
        }
    }

    /// 安装 hermes。
    public func installHermes() async throws {
        let options = HermesInstallOptions()
        try await installer.install(options: options)
        // 重新 detect
        let status = await installer.detect()
        if let p = status.path {
            installedHermesPath = p
            commandRunner = HermesCommandRunner(hermesPath: p)
        }
    }
}

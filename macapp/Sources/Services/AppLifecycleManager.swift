import Foundation
import SwiftUI
import Observation

/// 应用生命周期管理器：管理 Hermes 安装 / 启动 / 停止 / 重启 / Dashboard 探测。
///
/// Phase 1 任务 1.5 (HermesProcess) + 1.8 (HermesCommandRunner + 集成) 实现完整能力。
/// 当前为骨架实现：仅保持可编译，所有操作均为 no-op（让 build 通过）。
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

        public var label: String {
            switch self {
            case .stopped: return loc("未启动")
            case .starting: return loc("启动中")
            case .running: return loc("运行中")
            case .stopping: return loc("停止中")
            case .crashed: return loc("崩溃")
            }
        }

        public var color: Color {
            switch self {
            case .stopped: return .secondary
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

    public init() {
        let logsBase = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first!
        self.logsDirectory = logsBase
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("XiJianManager", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsDirectory, withIntermediateDirectories: true)
    }

    /// 启动时检查 Hermes 是否已安装，未装则提示去设置安装
    public func ensureHermesInstalledAndRunning() async {
        // Phase 1 任务 1.5: detect + start
        // 当前 stub：不执行任何动作
    }

    public func startGateway() async throws {
        gatewayState = .running
        dashboardPort = 9119  // 占位
    }

    public func stopGateway() async throws {
        gatewayState = .stopped
        dashboardPort = nil
    }

    public func restartGateway() async throws {
        try await stopGateway()
        try await startGateway()
    }

    /// 同步停止（应用退出时调用，不能 await）
    public func stopGatewaySync() {
        // Phase 1 任务 1.5: SIGTERM
    }
}

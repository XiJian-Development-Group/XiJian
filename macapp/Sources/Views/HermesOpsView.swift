import SwiftUI
import XiJianManagerKit

/// Hermes 运维视图：体检、定时任务、会话、记忆、技能、升级。
///
/// 这些命令以前只存在于 runner 里，界面上一个入口都没有 —— 有能耐没入口。
/// 现在每个都给一个按钮，跑完把输出摊开看（可以选中复制）。
struct HermesOpsView: View {
    @State private var isRunning = false
    @State private var runningAction: ActionID?
    @State private var output = ""
    @State private var error: String?

    /// 能跑的命令。
    enum ActionID: String, CaseIterable, Identifiable {
        case doctor
        case status
        case cron
        case cronDoctor
        case sessions
        case memory
        case skills
        case update

        var id: String { rawValue }

        var title: String {
            switch self {
            case .doctor: return loc("体检")
            case .status: return loc("总状态")
            case .cron: return loc("定时任务")
            case .cronDoctor: return loc("定时任务体检")
            case .sessions: return loc("会话")
            case .memory: return loc("记忆状态")
            case .skills: return loc("技能")
            case .update: return loc("升级 Hermes")
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(xj: "Hermes 运维")
                    .font(.title)
                Spacer()
                if isRunning {
                    ProgressView().controlSize(.small)
                }
            }
            .padding()

            Divider()

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(ActionID.allCases) { action in
                        Button {
                            Task { await run(action) }
                        } label: {
                            Text(action.title)
                        }
                        .buttonStyle(.bordered)
                        .disabled(isRunning)
                    }
                }
                .padding(.horizontal)
                .padding(.vertical, 8)
            }

            Divider()

            if let err = error {
                Text(err)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
                    .padding(.top, 8)
            }

            if output.isEmpty {
                ContentUnavailableView(
                    "选一条命令",
                    systemImage: "wrench.and.screwdriver",
                    description: Text("跑完这里显示输出")
                )
            } else {
                ScrollView {
                    Text(output)
                        .font(.system(.body, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func run(_ action: ActionID) async {
        guard let runner = AppLifecycleManager.shared.commandRunner else {
            error = "还没找到 Hermes"
            return
        }
        isRunning = true
        runningAction = action
        defer {
            isRunning = false
            runningAction = nil
        }
        do {
            let result = try await execute(action, runner)
            let parts = [result.stdout, result.stderr].filter { !$0.isEmpty }
            output = parts.joined(separator: "\n")
            if result.exitCode == 0 {
                error = nil
            } else {
                error = String(format: loc("退出码 %lld"), result.exitCode)
            }
        } catch {
            self.error = "\(action.title) 失败：\(error)"
            output = ""
        }
    }

    private func execute(_ action: ActionID,
                         _ runner: HermesCommandRunner) async throws -> HermesCommandRunner.CommandResult {
        switch action {
        case .doctor: return try await runner.runDoctor()
        case .status: return try await runner.run(.status)
        case .cron: return try await runner.cronJobs()
        case .cronDoctor: return try await runner.cronDoctor()
        case .sessions: return try await runner.sessions()
        case .memory: return try await runner.memoryStatus()
        case .skills: return try await runner.skills()
        case .update: return try await runner.updateHermes()
        }
    }
}

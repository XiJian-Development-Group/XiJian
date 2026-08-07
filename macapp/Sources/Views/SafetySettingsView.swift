import SwiftUI
import XiJianKit

/// 安全模块设置：保护闸门状态、启用/禁用（两步挑战）、输入/输出扫描测试
struct SafetySettingsView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme

    @State private var status: GateStatus?
    @State private var isLoading = false
    @State private var showError = false
    @State private var errorMessage = ""

    // 禁用流程
    @State private var disablePhase: DisablePhase = .idle
    @State private var confirmationText = ""
    @State private var challenge: DisableChallenge?
    @State private var phrase = ""

    // 扫描测试
    @State private var scanText = ""
    @State private var scanResult: SafetyScanResult?
    @State private var lastScanKind = ""

    enum DisablePhase {
        case idle, awaitingChallenge, awaitingConfirmation
    }

    var body: some View {
        Form {
            Section("保护闸门") {
                if let status {
                    LabeledContent("状态", value: status.enabled ? "已启用" : "已禁用")
                    LabeledContent("防护等级", value: status.guard_level ?? "standard")
                    LabeledContent("审计日志条数", value: "\(status.audit_log_size ?? 0)")
                    LabeledContent("版本", value: status.version ?? "—")
                } else if isLoading {
                    ProgressView("加载中...")
                } else {
                    Text("无法获取闸门状态（Core 可能未运行）。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 10) {
                    Button("刷新状态") {
                        Task { await load() }
                    }
                    .buttonStyle(.bordered)

                    if status?.enabled == true {
                        Button("关闭保护", role: .destructive) {
                            startDisableFlow()
                        }
                        .buttonStyle(.bordered)
                    } else {
                        Button("启用保护") {
                            Task { await enable() }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                }
            }

            if disablePhase != .idle {
                Section("关闭保护（两步确认）") {
                    switch disablePhase {
                    case .awaitingChallenge:
                        Text("关闭保护前需要双重确认。请输入确认短语：")
                            .font(.caption)
                        TextField("I understand the risks", text: $confirmationText)
                            .textFieldStyle(.roundedBorder)
                        HStack {
                            Button("发起挑战") {
                                Task { await startChallenge() }
                            }
                            .buttonStyle(.borderedProminent)
                            Button("取消") { disablePhase = .idle }
                        }
                    case .awaitingConfirmation:
                        if let challenge {
                            Text("请在 60 秒内输入以下短语完成确认：")
                                .font(.caption)
                            Text("「\(challenge.challenge_phrase ?? "—")」")
                                .font(.headline)
                                .foregroundStyle(theme.accentColor)
                            TextField("输入挑战短语", text: $phrase)
                                .textFieldStyle(.roundedBorder)
                            HStack {
                                Button("确认关闭") {
                                    Task { await confirmChallenge() }
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(phrase.trimmingCharacters(in: .whitespaces).isEmpty)
                                Button("取消") { disablePhase = .idle }
                            }
                        }
                    case .idle:
                        EmptyView()
                    }
                }
            }

            Section("扫描测试") {
                TextField("输入要扫描的文本", text: $scanText, axis: .vertical)
                    .lineLimit(3...6)
                    .textFieldStyle(.roundedBorder)

                HStack(spacing: 10) {
                    Button("扫描输入") {
                        Task { await scan(kind: "input") }
                    }
                    .buttonStyle(.bordered)

                    Button("扫描输出") {
                        Task { await scan(kind: "output") }
                    }
                    .buttonStyle(.bordered)
                }
                .disabled(scanText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                if let result = scanResult {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 6) {
                            Image(systemName: result.isPass ? "checkmark.circle.fill" : (result.isBlock ? "xmark.octagon.fill" : "exclamationmark.triangle.fill"))
                                .foregroundStyle(result.isPass ? .green : (result.isBlock ? .red : .orange))
                            Text("判定：\(result.verdict ?? "未知")")
                                .font(.subheadline.bold())
                            Text("（\(lastScanKind)）")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if let blocked = result.blocked, !blocked.isEmpty {
                            Text("拦截原因：\(blocked)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if let matches = result.matches, !matches.isEmpty {
                            Text("命中规则：\(matches.map(\.displayText).joined(separator: "、"))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if let auditID = result.audit_id {
                            Text("审计 ID：\(auditID)")
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                    }
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color(.controlBackgroundColor))
                    )
                }
            }

            Section("说明") {
                Text("安全模块默认开启。所有安全端点都受安全模块自身监控，任何尝试绕过安全系统的请求都会写入审计日志。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("安全模块")
        .alert("出错了", isPresented: $showError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .task {
            await load()
        }
    }

    // MARK: 加载

    private func load() async {
        guard let client = core.makeClient() else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            status = try await client.gateStatus()
        } catch {
            presentError(error)
        }
    }

    // MARK: 启用 / 禁用

    private func startDisableFlow() {
        confirmationText = ""
        phrase = ""
        challenge = nil
        disablePhase = .awaitingChallenge
    }

    private func startChallenge() async {
        guard let client = core.makeClient() else { return }
        do {
            let result = try await client.startDisableGate(confirmation: confirmationText)
            if let error = result.error, result.challenge_id == nil {
                presentErrorMessage("发起挑战失败：\(error)")
                disablePhase = .idle
                return
            }
            challenge = result
            disablePhase = .awaitingConfirmation
        } catch {
            presentError(error)
        }
    }

    private func confirmChallenge() async {
        guard let client = core.makeClient(), let challenge else { return }
        do {
            let result = try await client.confirmDisableGate(challengeID: challenge.challenge_id ?? "", phrase: phrase)
            if result.enabled == false {
                status = GateStatus(enabled: false, guard_level: nil, audit_log_size: nil, version: nil)
                disablePhase = .idle
            } else if let error = result.error {
                presentErrorMessage("确认失败：\(error == "phrase_mismatch" ? "短语不匹配" : (error == "challenge_expired" ? "挑战已过期" : error))")
                disablePhase = .idle
            }
        } catch {
            presentError(error)
        }
    }

    private func enable() async {
        guard let client = core.makeClient() else { return }
        do {
            status = try await client.enableGate()
        } catch {
            presentError(error)
        }
    }

    // MARK: 扫描

    private func scan(kind: String) async {
        guard let client = core.makeClient() else { return }
        let text = scanText.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            if kind == "input" {
                scanResult = try await client.scanInput(text: text, characterID: nil, worldID: nil)
            } else {
                scanResult = try await client.scanOutput(text: text, characterID: nil, worldID: nil)
            }
            lastScanKind = kind == "input" ? "输入" : "输出"
        } catch {
            presentError(error)
        }
    }

    // MARK: 错误

    private func presentError(_ error: Error) {
        if let apiError = error as? APIError {
            presentErrorMessage(apiError.message)
        } else {
            presentErrorMessage(error.localizedDescription)
        }
    }

    private func presentErrorMessage(_ message: String) {
        errorMessage = message
        showError = true
    }
}

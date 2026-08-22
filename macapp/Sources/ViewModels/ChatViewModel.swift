import Foundation
import Observation

/// 聊天 ViewModel：模型选择、会话管理、SSE 流式收发、中止生成
@Observable
@MainActor
final class ChatViewModel {

    // MARK: 状态

    /// 当前会话消息
    private(set) var messages: [ChatMessage] = []
    /// 当前会话 ID
    private(set) var sessionID: String?
    /// 可选模型
    var models: [ModelInfo] = []
    /// 当前选中模型
    var selectedModelID: String?
    /// 是否正在流式生成
    private(set) var isStreaming = false
    /// 当前流式请求 ID（用于中止）
    private(set) var streamRequestID: String?
    /// 错误提示
    var errorMessage: String?
    var showError = false
    /// 正在加载模型
    private(set) var isLoadingModels = false

    /// 会话标题（用于创建会话）
    var sessionTitle: String = loc("新会话")

    private var streamingTask: Task<Void, Never>?
    private let core = CoreManager.shared
    private let app = AppViewModel.shared

    // MARK: 模型

    func loadModels() async {
        guard let client = core.makeClient() else { return }
        isLoadingModels = true
        defer { isLoadingModels = false }
        do {
            let list = try await client.listModels()
            models = list
            if selectedModelID == nil || !list.contains(where: { $0.id == selectedModelID }) {
                selectedModelID = list.first?.id
            }
        } catch {
            presentError(error)
        }
    }

    var selectedModel: String? {
        selectedModelID ?? models.first?.id
    }

    // MARK: 会话

    /// 新开会话（服务端创建 + 清空本地）
    func newSession() async {
        stopStreaming()
        messages = []
        sessionID = nil
        guard let client = core.makeClient() else { return }
        do {
            let session = try await client.createSession(title: sessionTitle)
            sessionID = session.id
        } catch {
            presentError(error)
        }
    }

    /// 加载会话历史
    func loadMessages() async {
        guard let sessionID, let client = core.makeClient() else { return }
        do {
            let list = try await client.listSessionMessages(sessionID)
            messages = list
        } catch {
            presentError(error)
        }
    }

    /// 删除当前会话
    func deleteCurrentSession() async {
        stopStreaming()
        if let sessionID, let client = core.makeClient() {
            try? await client.deleteSession(sessionID)
        }
        messages = []
        self.sessionID = nil
    }

    /// 清空当前会话（本地 + 服务端重建）
    func clearChat() async {
        await deleteCurrentSession()
        await newSession()
    }

    // MARK: 发送

    /// 发送用户消息并流式接收回复
    func send(text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        // 僵尸态自愈：上一次任务实际已结束但 isStreaming 未复位的极端情况。
        if isStreaming {
            if streamingTask == nil { isStreaming = false }
        }
        guard !isStreaming else {
            presentError(loc("上一条消息仍在生成中，请稍候或点击停止。"))
            return
        }
        guard let client = core.makeClient() else {
            presentError(loc("Core 未运行，无法发送消息。请先在设置中启动 Core。"))
            return
        }
        if models.isEmpty { await loadModels() }
        guard let model = selectedModel else {
            presentError(loc("没有可用模型，无法发送消息。"))
            return
        }

        // 确保会话存在
        if sessionID == nil {
            do {
                let session = try await client.createSession(title: sessionTitle)
                sessionID = session.id
            } catch {
                presentError(error)
                return
            }
        }

        let userMessage = ChatMessage(role: "user", content: trimmed, sessionID: sessionID)
        messages.append(userMessage)
        // 尽力持久化到会话
        if let sessionID { try? await client.appendSessionMessage(sessionID, role: "user", content: trimmed) }

        // 记录最后对话时间（用于首页按最近对话排序）
        if let characterID = app.selectedCharacterID {
            recordLastChatTime(for: characterID)
        }

        // 构造流式请求（发送全部历史）
        isStreaming = true
        let profile = UserProfileSettings.shared
        var userProfile: [String: JSONValue] = [:]
        if !profile.identityDescription.isEmpty {
            userProfile["identity"] = .string(profile.identityDescription)
        }
        if !profile.aliases.isEmpty {
            userProfile["aliases"] = .array(profile.aliases.map { .string($0) })
        }
        let request = APIClient.ChatRequest(
            model: model,
            messages: messages,
            temperature: app.temperature,
            maxTokens: app.maxTokens,
            characterID: app.selectedCharacterID,
            worldID: app.selectedWorldID,
            recallEnabled: app.recallEnabled,
            requestID: UUID().uuidString,
            userName: profile.userName.isEmpty ? nil : profile.userName,
            userProfile: userProfile.isEmpty ? nil : userProfile
        )
        streamRequestID = request.requestID

        var assistantMessage = ChatMessage(role: "assistant", content: "", sessionID: sessionID)
        messages.append(assistantMessage)

        streamingTask = Task {
            do {
                var finishedNormally = false
                var accumulated = ""
                for try await event in client.streamChat(request: request) {
                    if Task.isCancelled { break }
                    switch event {
                    case .chunk(let chunk):
                        accumulated += chunk.deltaContent
                        assistantMessage = ChatMessage(role: "assistant", content: accumulated, sessionID: sessionID)
                        // 就地更新最后一条消息
                        if let idx = messages.indices.last, messages[idx].isAssistant {
                            messages[idx] = assistantMessage
                        }
                        if chunk.finishReason == "stop" || chunk.finishReason == "abort" {
                            finishedNormally = true
                        }
                    case .done:
                        finishedNormally = true
                    case .aborted:
                        finishedNormally = true
                    }
                }
                if !Task.isCancelled && finishedNormally {
                    if let sessionID, !accumulated.isEmpty {
                        try? await client.appendSessionMessage(sessionID, role: "assistant", content: accumulated)
                    }
                }
                // 静默失败兜底：流结束但既无内容也无正常收尾 → 明确报错并清掉空气泡，
                // 绝不让 UI 停留在"看起来发出去了但什么都没发生"的状态。
                if !Task.isCancelled {
                    if !finishedNormally {
                        presentError(loc("生成中断：服务器未正常完成回复。请重试或查看 Core 日志。"))
                    } else if accumulated.isEmpty {
                        presentError(loc("服务器返回了空回复。请检查模型配置或稍后重试。"))
                        trimEmptyAssistantMessage()
                    }
                } else if accumulated.isEmpty {
                    trimEmptyAssistantMessage()
                }
            } catch {
                if !Task.isCancelled {
                    presentError(error)
                }
            }
            isStreaming = false
            streamRequestID = nil
            streamingTask = nil
        }
    }

    /// 停止生成（取消流 + 通知服务端中止）
    func stopStreaming() {
        guard isStreaming else { return }
        let requestID = streamRequestID
        streamingTask?.cancel()
        streamingTask = nil
        isStreaming = false
        streamRequestID = nil
        if let requestID {
            Task {
                _ = try? await core.makeClient()?.chatAbort(requestID: requestID)
            }
        }
    }

    /// 移除最后一条空回复
    func trimEmptyAssistantMessage() {
        if let last = messages.last, last.isAssistant, last.content.isEmpty {
            messages.removeLast()
        }
    }

    /// 错误提示
    private func presentError(_ message: String) {
        errorMessage = message
        showError = true
    }

    private func presentError(_ error: Error) {
        if let apiError = error as? APIError {
            presentError(apiError.message)
        } else {
            presentError(error.localizedDescription)
        }
    }

    /// 记录角色最后对话时间（存入 UserDefaults，用于首页排序）
    private func recordLastChatTime(for characterID: String) {
        var times = UserDefaults.standard.dictionary(forKey: XJDefaultsKey.characterLastChatTime) as? [String: TimeInterval] ?? [:]
        times[characterID] = Date().timeIntervalSince1970
        UserDefaults.standard.set(times, forKey: XJDefaultsKey.characterLastChatTime)
    }
}

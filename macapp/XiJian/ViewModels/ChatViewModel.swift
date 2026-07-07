import Foundation

@Observable
final class ChatViewModel {
    var messages: [Message] = []
    var inputText: String = ""
    var isStreaming: Bool = false
    var selectedModel: String = "default"
    var availableModels: [String] = ["default"]
    var errorMessage: String?

    private let apiClient: APIClient

    init(apiClient: APIClient) {
        self.apiClient = apiClient
    }

    func loadMessages(_ messages: [Message]) {
        self.messages = messages
    }

    func clearMessages() {
        messages.removeAll()
        errorMessage = nil
    }

    func send(baseURL: String) async {
        let userMessage = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !userMessage.isEmpty else { return }

        let userMsg = Message(role: .user, content: userMessage)
        messages.append(userMsg)
        inputText = ""

        let assistantMsg = Message(role: .assistant, content: "", isStreaming: true)
        messages.append(assistantMsg)
        isStreaming = true
        errorMessage = nil

        let apiMessages = messages
            .filter { !$0.isStreaming }
            .map { ["role": $0.role.rawValue, "content": $0.content] }

        var accumulatedContent = ""

        for await chunk in apiClient.streamChat(
            baseURL: baseURL,
            model: selectedModel,
            messages: apiMessages,
            onChunk: { _ in }
        ) {
            accumulatedContent += chunk
            if let idx = messages.firstIndex(where: { $0.id == assistantMsg.id }) {
                messages[idx].content = accumulatedContent
            }
        }

        if let idx = messages.firstIndex(where: { $0.id == assistantMsg.id }) {
            messages[idx].isStreaming = false
        }
        isStreaming = false

        if accumulatedContent.isEmpty {
            errorMessage = "未收到回复，请检查服务器状态"
            messages.removeAll { $0.id == assistantMsg.id }
        }
    }

    func fetchModels(baseURL: String) async {
        guard let models = try? await apiClient.listModels(baseURL: baseURL) else { return }
        availableModels = models.compactMap { $0["id"] as? String }
        if !availableModels.contains(selectedModel) {
            selectedModel = availableModels.first ?? "default"
        }
    }
}

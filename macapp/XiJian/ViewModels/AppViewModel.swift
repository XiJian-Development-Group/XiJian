import Foundation

@Observable
final class AppViewModel {
    var selectedSidebarItem: SidebarItem = .chat
    var conversations: [Conversation] = []
    var selectedConversationID: String?
    var serverConfig: ServerConfig { didSet { serverConfig.save() } }

    let coreManager: CoreManager
    let apiClient: APIClient

    private var conversationCounter = 0

    var selectedConversation: Conversation? {
        get { conversations.first { $0.id == selectedConversationID } }
        set {
            if let newValue {
                if let idx = conversations.firstIndex(where: { $0.id == newValue.id }) {
                    conversations[idx] = newValue
                }
            }
        }
    }

    init() {
        self.coreManager = CoreManager()
        self.apiClient = APIClient()
        self.serverConfig = ServerConfig.load()
    }

    // MARK: - Conversations

    func createNewConversation() {
        conversationCounter += 1
        let conv = Conversation(title: "新对话 \(conversationCounter)", model: serverConfig.defaultModel)
        conversations.insert(conv, at: 0)
        selectedConversationID = conv.id
    }

    func deleteConversation(id: String) {
        conversations.removeAll { $0.id == id }
        if selectedConversationID == id {
            selectedConversationID = conversations.first?.id
        }
    }

    // MARK: - Server

    var baseURL: String {
        if serverConfig.useCustomServer && !serverConfig.customServerURL.isEmpty {
            return serverConfig.customServerURL
        }
        return "http://127.0.0.1:\(coreManager.port)"
    }

    func startServer() async {
        await coreManager.start()
    }

    func stopServer() {
        coreManager.stop()
    }

    func checkServerHealth() async -> Bool {
        await apiClient.checkHealth(baseURL: baseURL)
    }
}

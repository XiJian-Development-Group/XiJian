import SwiftUI
import MarkdownUI

struct ChatView: View {
    @Environment(AppViewModel.self) private var appVM
    @State private var chatVM: ChatViewModel?
    @State private var scrollProxy: ScrollViewProxy?
    @State private var isLoadingModels = false

    var body: some View {
        VStack(spacing: 0) {
            // Model selector and controls header
            HStack {
                Picker("模型", selection: Binding(
                    get: { chatVM?.selectedModel ?? "default" },
                    set: { chatVM?.selectedModel = $0 }
                )) {
                    ForEach(chatVM?.availableModels ?? ["default"], id: \.self) { model in
                        Text(model).tag(model)
                    }
                }
                .frame(width: 200)

                Spacer()

                if let error = chatVM?.errorMessage {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.xiJianRed)
                }

                Button("清空") {
                    chatVM?.clearMessages()
                }
                .disabled(chatVM?.messages.isEmpty ?? true)
            }
            .padding(.horizontal)
            .padding(.vertical, 8)

            Divider()

            // Messages area
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 12) {
                        if let msgs = chatVM?.messages, msgs.isEmpty {
                            VStack(spacing: 16) {
                                Image(systemName: "message.and.waveform")
                                    .font(.system(size: 48))
                                    .foregroundStyle(.secondary)
                                Text("开始一段新的对话")
                                    .font(.title3)
                                    .foregroundStyle(.secondary)
                                Text("在下方的输入框中输入消息，与 AI 角色展开对话")
                                    .font(.callout)
                                    .foregroundStyle(.tertiary)
                            }
                            .padding(.top, 80)
                        }

                        ForEach(chatVM?.messages ?? []) { message in
                            MessageBubbleView(message: message)
                                .id(message.id)
                        }
                    }
                    .padding()
                }
                .onChange(of: chatVM?.messages.count ?? 0) { _, _ in
                    if let last = chatVM?.messages.last {
                        withAnimation {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }

            Divider()

            // Input area
            HStack(spacing: 12) {
                TextField("输入消息...", text: Binding(
                    get: { chatVM?.inputText ?? "" },
                    set: { chatVM?.inputText = $0 }
                ), axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(1...6)
                    .padding(10)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .onSubmit {
                        sendMessage()
                    }
                    .disabled(chatVM?.isStreaming ?? false)

                Button(action: sendMessage) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 28))
                        .symbolRenderingMode(.hierarchical)
                        .foregroundStyle(.xiJianPurple)
                }
                .buttonStyle(.plain)
                .disabled((chatVM?.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true) || (chatVM?.isStreaming ?? false))
            }
            .padding()
        }
        .onAppear {
            if chatVM == nil {
                chatVM = ChatViewModel(apiClient: appVM.apiClient)
            }
            chatVM?.loadMessages(appVM.selectedConversation?.messages ?? [])
            loadModels()
        }
        .onChange(of: appVM.selectedConversationID) { _, _ in
            chatVM?.loadMessages(appVM.selectedConversation?.messages ?? [])
        }
    }

    private func sendMessage() {
        guard let chatVM else { return }
        Task {
            await chatVM.send(baseURL: appVM.baseURL)
            if let conv = appVM.selectedConversation {
                var updated = conv
                updated.messages = chatVM.messages
                updated.updatedAt = Date()
                appVM.selectedConversation = updated
            }
        }
    }

    private func loadModels() {
        guard let chatVM else { return }
        Task {
            await chatVM.fetchModels(baseURL: appVM.baseURL)
        }
    }
}

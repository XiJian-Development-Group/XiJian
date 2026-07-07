import SwiftUI

struct SidebarView: View {
    @Environment(AppViewModel.self) private var appVM

    var body: some View {
        List(selection: Binding(
            get: { appVM.selectedSidebarItem },
            set: { appVM.selectedSidebarItem = $0 }
        )) {
            Section("导航") {
                ForEach(SidebarItem.allCases) { item in
                    Label(item.label, systemImage: item.icon)
                        .tag(item)
                }
            }

            if appVM.selectedSidebarItem == .chat {
                Section("对话记录") {
                    ForEach(appVM.conversations) { conv in
                        HStack {
                            Image(systemName: "message")
                                .foregroundStyle(.secondary)
                            Text(conv.title)
                                .lineLimit(1)
                                .font(.body)
                        }
                        .tag(conv.id)
                        .contextMenu {
                            Button("删除", role: .destructive) {
                                appVM.deleteConversation(id: conv.id)
                            }
                        }
                        .onTapGesture {
                            appVM.selectedConversationID = conv.id
                        }
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Button(action: {
                    if appVM.coreManager.state == .stopped {
                        Task { await appVM.startServer() }
                    } else {
                        appVM.stopServer()
                    }
                }) {
                    StatusIndicatorView(state: appVM.coreManager.state)
                }
                .buttonStyle(.plain)
                .help(serverStatusText)
            }

            ToolbarItem(placement: .automatic) {
                if appVM.selectedSidebarItem == .chat {
                    Button(action: appVM.createNewConversation) {
                        Image(systemName: "square.and.pencil")
                    }
                    .help("新建对话")
                }
            }
        }
    }

    private var serverStatusText: String {
        switch appVM.coreManager.state {
        case .stopped: return "服务器已停止 - 点击启动"
        case .starting, .extracting: return "服务器启动中..."
        case .running(let port): return "服务器运行中 (端口 \(port))"
        case .error(let msg): return "错误: \(msg)"
        }
    }
}

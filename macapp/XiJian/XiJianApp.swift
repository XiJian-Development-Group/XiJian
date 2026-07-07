import SwiftUI

@main
struct XiJianApp: App {
    @State private var appViewModel = AppViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(appViewModel)
                .frame(minWidth: 900, minHeight: 600)
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("新建对话") {
                    appViewModel.createNewConversation()
                }
                .keyboardShortcut("n", modifiers: .command)

                Button("清空对话") {
                    if let conv = appViewModel.selectedConversation {
                        var updated = conv
                        updated.messages.removeAll()
                        appViewModel.selectedConversation = updated
                    }
                }
                .keyboardShortcut("k", modifiers: [.command, .shift])
            }
        }
    }
}

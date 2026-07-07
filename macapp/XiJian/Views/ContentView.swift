import SwiftUI

struct ContentView: View {
    @Environment(AppViewModel.self) private var appVM

    var body: some View {
        NavigationSplitView {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 300)
        } detail: {
            switch appVM.selectedSidebarItem {
            case .chat:
                ChatView()
            case .characters:
                CharacterListView()
            case .settings:
                ServerSettingsView()
            }
        }
        .onAppear {
            if appVM.conversations.isEmpty {
                appVM.createNewConversation()
            }
        }
    }
}

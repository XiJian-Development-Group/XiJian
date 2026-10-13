import SwiftUI
import XiJianManagerKit

/// websearch provider 切换 + Dashboard 子页管理 二合一视图
struct SettingsAdvancedView: View {
    var body: some View {
        TabView {
            WebSearchView()
                .tabItem { Label("Web 搜索", systemImage: "magnifyingglass") }
            DashboardTabsView()
                .tabItem { Label("子页管理", systemImage: "square.grid.2x2") }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct WebSearchView: View {
    @State private var currentID: String = "default"
    @State private var isLoading = false
    @State private var error: String?

    var body: some View {
        Form {
            Section("默认 Web Search Provider") {
                ForEach(WebSearchConfig.shared.available) { p in
                    HStack {
                        Image(systemName: currentID == p.id ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(currentID == p.id ? Color.accentColor : Color.secondary)
                        Text(p.name)
                        Spacer()
                    }
                    .contentShape(Rectangle())
                    .onTapGesture {
                        Task { await select(p.id) }
                    }
                }
            }
            Section {
                Text("切换后会在 ~/.hermes/websearch.json 写入，Hermes Agent 下次启动读取。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
        }
        .padding()
        .task { await load() }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            currentID = try await WebSearchConfig.shared.load()
            error = nil
        } catch {
            self.error = "加载失败：\(error)"
        }
    }

    private func select(_ id: String) async {
        do {
            try await WebSearchConfig.shared.setCurrent(id)
            await load()
        } catch {
            self.error = "切换失败：\(error)"
        }
    }
}

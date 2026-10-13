import SwiftUI
import XiJianManagerKit

/// Token 监控视图：provider 列表 + 添加 + 刷新 + 显示用量
struct TokenMonitorView: View {
    @State private var providers: [TokenMonitor.Provider] = []
    @State private var isLoading = false
    @State private var error: String?
    @State private var showAddSheet = false
    @State private var newName: String = ""
    @State private var newEndpoint: String = ""
    @State private var newAPIKey: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(xj: "Token 监控")
                    .font(.title)
                Spacer()
                Button(loc("全部刷新")) {
                    Task { await refreshAll() }
                }
                Button(loc("+ 添加")) { showAddSheet = true }
            }
            .padding()

            Divider()

            if isLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if providers.isEmpty {
                ContentUnavailableView(
                    "未配置 provider",
                    systemImage: "chart.bar",
                    description: Text("添加 OpenAI / Anthropic 等 API endpoint")
                )
            } else {
                List {
                    ForEach(providers) { p in
                        providerRow(p)
                    }
                }
            }

            if let err = error {
                Text(err).font(.callout).foregroundStyle(.red).padding(8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(isPresented: $showAddSheet) {
            addSheet
        }
        .task { await load() }
    }

    @ViewBuilder
    private func providerRow(_ p: TokenMonitor.Provider) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(p.name).font(.headline)
                if let lastFetched = p.lastFetched {
                    Text(lastFetched, format: .relative(presentation: .named))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(loc("刷新")) {
                    Task { await refresh(p.id) }
                }
                .buttonStyle(.borderless)
                Button(loc("删除"), role: .destructive) {
                    Task { await delete(p.id) }
                }
                .buttonStyle(.borderless)
            }
            if let usage = p.usage {
                HStack {
                    ProgressView(value: usage.percentage ?? 0)
                        .frame(maxWidth: 200)
                    Text("\(usage.usedTokens) tokens")
                        .font(.callout.monospacedDigit())
                }
                HStack(spacing: 12) {
                    if let tier = usage.tier {
                        LabeledContent("等级") { Text(tier).font(.callout) }
                    }
                    if let resetAt = usage.resetAt {
                        LabeledContent("重置") {
                            Text(resetAt, format: .dateTime.month().day().hour().minute())
                                .font(.callout)
                        }
                    }
                }
            } else if let err = p.error {
                Text("⚠ \(err)")
                    .font(.caption)
                    .foregroundStyle(.red)
            } else {
                Text("尚未拉取")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }

    private var addSheet: some View {
        VStack(spacing: 16) {
            Text(xj: "添加 Provider").font(.headline)
            TextField("名称（如 OpenAI）", text: $newName)
                .textFieldStyle(.roundedBorder)
                .frame(width: 400)
            TextField("Usage endpoint URL", text: $newEndpoint)
                .textFieldStyle(.roundedBorder)
                .frame(width: 400)
            SecureField("API Key（可选）", text: $newAPIKey)
                .textFieldStyle(.roundedBorder)
                .frame(width: 400)
            HStack {
                Button(loc("取消")) {
                    newName = ""
                    newEndpoint = ""
                    newAPIKey = ""
                    showAddSheet = false
                }
                .keyboardShortcut(.cancelAction)
                Spacer()
                Button(loc("添加")) {
                    Task { await addProvider() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(newName.isEmpty || newEndpoint.isEmpty)
            }
        }
        .padding(20)
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            providers = try await TokenMonitor.shared.loadProviders()
            error = nil
        } catch {
            self.error = "加载失败：\(error)"
        }
    }

    private func refresh(_ id: String) async {
        await TokenMonitor.shared.refresh(providerID: id)
        await load()
    }

    private func refreshAll() async {
        await TokenMonitor.shared.refreshAll()
        await load()
    }

    private func delete(_ id: String) async {
        do {
            try await TokenMonitor.shared.removeProvider(id: id)
            await load()
        } catch {
            self.error = "删除失败：\(error)"
        }
    }

    private func addProvider() async {
        let provider = TokenMonitor.Provider(
            id: UUID().uuidString,
            name: newName,
            endpoint: URL(string: newEndpoint) ?? URL(string: "https://invalid")!,
            apiKey: newAPIKey.isEmpty ? nil : newAPIKey
        )
        do {
            try await TokenMonitor.shared.addProvider(provider)
            newName = ""
            newEndpoint = ""
            newAPIKey = ""
            showAddSheet = false
            await load()
        } catch {
            self.error = "添加失败：\(error)"
        }
    }
}

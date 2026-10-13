import SwiftUI
import XiJianManagerKit

/// Dashboard 子页管理视图：勾选启用 / 上移下移
struct DashboardTabsView: View {
    @State private var tabs: [DashboardTabs.Tab] = []
    @State private var isLoading = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(xj: "Dashboard 子页")
                .font(.title)
                .padding()

            Text("勾选启用，拖动或点击上下移动调整顺序")
                .font(.callout)
                .foregroundStyle(.secondary)
                .padding(.horizontal)

            Divider().padding(.vertical, 8)

            if isLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(tabs) { tab in
                        HStack {
                            Toggle("", isOn: Binding(
                                get: { tab.enabled },
                                set: { newVal in
                                    Task { await setEnabled(tab.id, newVal) }
                                }
                            ))
                            .labelsHidden()
                            Text(tab.label).font(.body)
                            Spacer()
                            Button {
                                Task { await move(tab.id, by: -1) }
                            } label: {
                                Image(systemName: "arrow.up")
                            }
                            .buttonStyle(.borderless)
                            .disabled(isFirst(tab.id))

                            Button {
                                Task { await move(tab.id, by: 1) }
                            } label: {
                                Image(systemName: "arrow.down")
                            }
                            .buttonStyle(.borderless)
                            .disabled(isLast(tab.id))
                        }
                        .padding(.vertical, 2)
                    }
                }
            }

            if let err = error {
                Text(err).font(.callout).foregroundStyle(.red).padding(8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task { await load() }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            tabs = try await DashboardTabs.shared.load()
            error = nil
        } catch {
            self.error = "加载失败：\(error)"
        }
    }

    private func setEnabled(_ id: String, _ enabled: Bool) async {
        do {
            try await DashboardTabs.shared.setEnabled(id: id, enabled: enabled)
            await load()
        } catch {
            self.error = "保存失败：\(error)"
        }
    }

    private func move(_ id: String, by delta: Int) async {
        guard let idx = tabs.firstIndex(where: { $0.id == id }) else { return }
        let newIdx = idx + delta
        guard newIdx >= 0, newIdx < tabs.count else { return }
        var reordered = tabs
        let item = reordered.remove(at: idx)
        reordered.insert(item, at: newIdx)
        do {
            try await DashboardTabs.shared.reorder(ids: reordered.map { $0.id })
            await load()
        } catch {
            self.error = "排序失败：\(error)"
        }
    }

    private func isFirst(_ id: String) -> Bool { tabs.first?.id == id }
    private func isLast(_ id: String) -> Bool { tabs.last?.id == id }
}

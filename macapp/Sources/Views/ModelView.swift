import SwiftUI
import XiJianManagerKit

/// 模型管理视图：列出 config.yaml 中所有 model，添加/删除/切换活跃
struct ModelView: View {
    @State private var models: [ModelManager.Model] = []
    @State private var activeModel: ModelManager.Model?
    @State private var isLoading = false
    @State private var error: String?
    @State private var showAddSheet = false
    @State private var newName: String = ""
    @State private var newProvider: String = ""
    @State private var newNotes: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(xj: "模型管理")
                    .font(.title)
                Spacer()
                Button(loc("刷新")) { Task { await load() } }
                Button(loc("+ 添加")) { showAddSheet = true }
            }
            .padding()

            Divider()

            if isLoading {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if models.isEmpty {
                ContentUnavailableView(
                    "未配置模型",
                    systemImage: "cpu",
                    description: Text("点 + 添加从 git URL 拉取")
                )
            } else {
                List {
                    ForEach(models) { model in
                        modelRow(model)
                    }
                }
            }

            if let err = error {
                Text(err).font(.callout).foregroundStyle(.red).padding(8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(isPresented: $showAddSheet) { addSheet }
        .task { await load() }
    }

    @ViewBuilder
    private func modelRow(_ m: ModelManager.Model) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    Text(m.name).font(.headline)
                    if m.isActive {
                        Text("活跃")
                            .font(.caption)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.green.opacity(0.2))
                            .foregroundStyle(.green)
                            .clipShape(Capsule())
                    }
                }
                Text(m.provider)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let notes = m.notes, !notes.isEmpty {
                    Text(notes)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            Spacer()
            if !m.isActive {
                Button(loc("激活")) { Task { await setActive(m.id) } }
                    .buttonStyle(.bordered)
            }
            Button(loc("删除"), role: .destructive) {
                Task { await remove(m.id) }
            }
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 4)
    }

    private var addSheet: some View {
        VStack(spacing: 16) {
            Text(xj: "添加模型").font(.headline)
            TextField("名称（如 gpt-4o）", text: $newName)
                .textFieldStyle(.roundedBorder)
                .frame(width: 400)
            TextField("Provider（如 openai）", text: $newProvider)
                .textFieldStyle(.roundedBorder)
                .frame(width: 400)
            TextField("备注（可选）", text: $newNotes, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .frame(width: 400)
                .lineLimit(2...4)
            HStack {
                Button(loc("取消")) {
                    newName = ""
                    newProvider = ""
                    newNotes = ""
                    showAddSheet = false
                }
                .keyboardShortcut(.cancelAction)
                Spacer()
                Button(loc("添加")) {
                    Task { await add() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(newName.isEmpty || newProvider.isEmpty)
            }
        }
        .padding(20)
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            models = try await ModelManager.shared.load()
            activeModel = models.first { $0.isActive }
            error = nil
        } catch {
            self.error = "加载失败：\(error)"
        }
    }

    private func add() async {
        let model = ModelManager.Model(
            id: "\(newProvider):\(newName)",
            provider: newProvider,
            name: newName,
            isActive: false,
            notes: newNotes.isEmpty ? nil : newNotes
        )
        do {
            try await ModelManager.shared.add(model)
            newName = ""
            newProvider = ""
            newNotes = ""
            showAddSheet = false
            await load()
        } catch {
            self.error = "添加失败：\(error)"
        }
    }

    private func setActive(_ id: String) async {
        do {
            try await ModelManager.shared.setActive(id: id)
            await load()
        } catch {
            self.error = "激活失败：\(error)"
        }
    }

    private func remove(_ id: String) async {
        do {
            try await ModelManager.shared.remove(id: id)
            await load()
        } catch {
            self.error = "删除失败：\(error)"
        }
    }
}

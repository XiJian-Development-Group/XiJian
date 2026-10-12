import SwiftUI
import XiJianManagerKit

/// SOUL/MEMORY/USER.md 编辑器：两个 tab 并排显示，独立的保存按钮。
struct MemoryEditorView: View {
    @State private var memoryDoc = MemoryDocument(kind: .memory)
    @State private var userDoc = MemoryDocument(kind: .user)
    @State private var originalMemory: MemoryDocument?
    @State private var originalUser: MemoryDocument?
    @State private var profilePath: URL?
    @State private var error: String?
    @State private var isLoading = false

    private var isMemoryDirty: Bool { memoryDoc.body != (originalMemory?.body ?? "") }
    private var isUserDirty: Bool { userDoc.body != (originalUser?.body ?? "") }
    private var isAnyDirty: Bool { isMemoryDirty || isUserDirty }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(xj: "记忆管理")
                    .font(.title)
                Spacer()
                if isAnyDirty {
                    Text("● 未保存")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
                Button(loc("重新载入")) {
                    Task { await load() }
                }
                Button(loc("保存")) {
                    Task { await save() }
                }
                .disabled(!isAnyDirty)
            }
            .padding()

            Divider()

            if isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                HSplitView {
                    memoryPanel
                        .frame(minWidth: 350)
                    userPanel
                        .frame(minWidth: 350)
                }
            }

            if let err = error {
                Text(err)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding(8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task { await load() }
    }

    private var memoryPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(MemoryDocument.Kind.memory.displayName)
                    .font(.headline)
                Text("~/.hermes/.../MEMORY.md")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if isMemoryDirty {
                    Text("● 未保存")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
            TextEditor(text: $memoryDoc.body)
                .font(.system(size: 12, design: .monospaced))
                .frame(maxHeight: .infinity)
                .border(Color.gray.opacity(0.2))
        }
        .padding()
    }

    private var userPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(MemoryDocument.Kind.user.displayName)
                    .font(.headline)
                Text("~/.hermes/.../USER.md")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if isUserDirty {
                    Text("● 未保存")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
            TextEditor(text: $userDoc.body)
                .font(.system(size: 12, design: .monospaced))
                .frame(maxHeight: .infinity)
                .border(Color.gray.opacity(0.2))
        }
        .padding()
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let url = try currentProfilePath()
            profilePath = url
            memoryDoc = try MemoryDocument.load(kind: .memory, from: url)
            userDoc = try MemoryDocument.load(kind: .user, from: url)
            originalMemory = memoryDoc
            originalUser = userDoc
            error = nil
        } catch {
            self.error = "加载失败：\(error)"
        }
    }

    private func save() async {
        guard let url = profilePath else {
            error = "未选择 profile"
            return
        }
        do {
            try memoryDoc.save(to: url)
            try userDoc.save(to: url)
            originalMemory = memoryDoc
            originalUser = userDoc
            error = nil
        } catch {
            self.error = "保存失败：\(error)"
        }
    }

    private func currentProfilePath() throws -> URL {
        let home = URL(fileURLWithPath: ("~/.hermes" as NSString).expandingTildeInPath)
        let activeFile = home.appendingPathComponent("active_profile")
        let active = (try? String(contentsOf: activeFile, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)) ?? ""
        return active.isEmpty || active == "default"
            ? home
            : home.appendingPathComponent("profiles").appendingPathComponent(active)
    }
}

import SwiftUI
import XiJianManagerKit

/// SOUL.md 编辑器：左侧章节列表 + 右侧 Markdown 编辑器 + 顶部保存。
struct CharacterEditorView: View {
    @State private var soul: SoulDocument = SoulDocument()
    @State private var originalSoul: SoulDocument = SoulDocument()
    @State private var profilePath: URL?
    @State private var error: String?
    @State private var isLoading = false
    @State private var selectedSectionId: UUID?

    private var isDirty: Bool { soul != originalSoul }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(xj: "角色编辑")
                    .font(.title)
                Spacer()
                if isDirty {
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
                .disabled(!isDirty)
            }
            .padding()

            Divider()

            if isLoading {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                HSplitView {
                    sectionList
                        .frame(minWidth: 180, idealWidth: 220, maxWidth: 280)
                    editor
                        .frame(minWidth: 400)
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

    private var sectionList: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("章节").font(.headline)
                Spacer()
                Button(loc("+")) {
                    let newSection = SoulDocument.Section(title: "新章节", content: "")
                    soul.sections.append(newSection)
                    selectedSectionId = newSection.id
                }
                .buttonStyle(.borderless)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)

            Divider()

            List(selection: $selectedSectionId) {
                ForEach(soul.sections) { section in
                    Text(section.title.isEmpty ? "(无标题)" : section.title)
                        .tag(section.id)
                }
                .onDelete { offsets in
                    soul.sections.remove(atOffsets: offsets)
                }
            }
            .frame(minHeight: 200)
        }
    }

    @ViewBuilder
    private var editor: some View {
        if let idx = soul.sections.firstIndex(where: { $0.id == selectedSectionId }) {
            VStack(alignment: .leading, spacing: 8) {
                TextField("章节标题", text: Binding(
                    get: { soul.sections[idx].title },
                    set: { soul.sections[idx].title = $0 }
                ))
                .font(.title2)
                .textFieldStyle(.roundedBorder)

                TextEditor(text: Binding(
                    get: { soul.sections[idx].content },
                    set: { soul.sections[idx].content = $0 }
                ))
                .font(.system(size: 13, design: .monospaced))
                .frame(maxHeight: .infinity)
            }
            .padding()
        } else {
            ContentUnavailableView(
                "无选中章节",
                systemImage: "doc.text",
                description: Text("左侧选一个章节开始编辑")
            )
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let url = try currentSoulURL()
            profilePath = url
            let doc = try SoulDocument.load(from: url)
            soul = doc
            originalSoul = doc
            selectedSectionId = doc.sections.first?.id
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
            try soul.save(to: url)
            originalSoul = soul
            error = nil
        } catch {
            self.error = "保存失败：\(error)"
        }
    }

    private func currentSoulURL() throws -> URL {
        let home = URL(fileURLWithPath: ("~/.hermes" as NSString).expandingTildeInPath)
        let active = (try? String(contentsOf: home.appendingPathComponent("active_profile"), encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)) ?? ""
        let profileDir: URL = active.isEmpty || active == "default"
            ? home
            : home.appendingPathComponent("profiles").appendingPathComponent(active)
        return profileDir.appendingPathComponent("SOUL.md")
    }
}

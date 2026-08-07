import SwiftUI
import XiJianKit

/// 角色详情：人设、状态、加载/卸载、互动
struct CharacterDetailView: View {
    @Bindable var viewModel: CharacterViewModel
    let characterID: String

    @Environment(\.dismiss) private var dismiss
    @Environment(ThemeSettings.self) private var theme
    @State private var showEdit = false
    @State private var showInteract = false
    @State private var interactResult: String?
    @State private var showStateEditor = false
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var nsfwAllowed = false
    @State private var contextText = ""
    @State private var selectedInteractionID: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            if let detail = viewModel.detail {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        personaSection(detail)
                        stateSection
                        Divider()
                        actionsSection(detail)
                    }
                    .padding(16)
                }
            } else {
                ProgressView("加载中...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(width: 560, height: 640)
        .sheet(isPresented: $showEdit) {
            if let detail = viewModel.detail {
                CharacterEditSheet(viewModel: viewModel, mode: .edit(detail))
            }
        }
        .sheet(isPresented: $showInteract) {
            interactSheet
        }
        .sheet(isPresented: $showStateEditor) {
            stateEditorSheet
        }
        .alert("出错了", isPresented: $showError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .alert("互动结果", isPresented: Binding(
            get: { interactResult != nil },
            set: { if !$0 { interactResult = nil } }
        )) {
            Button("好", role: .cancel) {}
        } message: {
            Text(interactResult ?? "")
        }
        .task {
            await viewModel.loadDetail(characterID)
            await viewModel.loadInteractions()
        }
        .onChange(of: viewModel.showError) { _, newValue in
            if newValue {
                errorMessage = viewModel.errorMessage ?? "未知错误"
                showError = true
                viewModel.showError = false
            }
        }
    }

    // MARK: 头部

    private var header: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(theme.accentColor.opacity(0.18))
                    .frame(width: 48, height: 48)
                Text(String((viewModel.detail?.displayName ?? "?").prefix(1)))
                    .font(.title2)
                    .foregroundStyle(theme.accentColor)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(viewModel.detail?.displayName ?? "角色")
                    .font(.title2)
                    .bold()
                if let emotion = viewModel.detail?.default_emotion {
                    Text("情绪基线：\(emotion)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button {
                Task { await viewModel.toggleLoaded(characterID) }
            } label: {
                Label(
                    viewModel.detail?.isLoaded == true ? "卸载" : "加载",
                    systemImage: viewModel.detail?.isLoaded == true ? "eject.fill" : "play.fill"
                )
            }
            .buttonStyle(.bordered)
            Button("编辑") { showEdit = true }
                .buttonStyle(.bordered)
            Button("关闭") { dismiss() }
                .buttonStyle(.bordered)
        }
        .padding(14)
    }

    // MARK: 人设

    private func personaSection(_ detail: CharacterInfo) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("人设文档", systemImage: "doc.text")
                .font(.headline)
            Text(detail.persona_doc?.isEmpty == false ? detail.persona_doc! : "（未填写人设）")
                .font(.body)
                .foregroundStyle(detail.persona_doc?.isEmpty == false ? .primary : .tertiary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)

            if !detail.tagList.isEmpty {
                HStack(spacing: 6) {
                    ForEach(detail.tagList, id: \.self) { tag in
                        Text(tag)
                            .font(.caption2)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(Color.secondary.opacity(0.15)))
                    }
                }
            }
        }
    }

    // MARK: 状态

    @ViewBuilder
    private var stateSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("角色状态", systemImage: "gauge")
                    .font(.headline)
                Spacer()
                Button("刷新") {
                    Task { await viewModel.refreshState() }
                }
                .controlSize(.small)
                Button("编辑状态") { showStateEditor = true }
                    .controlSize(.small)
            }
            if let state = viewModel.state {
                if state.values.isEmpty {
                    Text("（暂无状态数据）")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                } else {
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                        ForEach(state.sortedEntries, id: \.key) { entry in
                            GridRow {
                                Text(entry.key)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(entry.value.displayText)
                                    .font(.body)
                                    .textSelection(.enabled)
                            }
                        }
                    }
                }
            } else {
                Text("（状态加载中或不可用）")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    // MARK: 动作区

    private func actionsSection(_ detail: CharacterInfo) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("互动", systemImage: "sparkles")
                .font(.headline)

            if viewModel.interactions.isEmpty {
                Text("（暂无可用互动类型）")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            } else {
                Menu {
                    ForEach(viewModel.interactions) { interaction in
                        Button(interaction.displayName) {
                            selectedInteractionID = interaction.id
                            showInteract = true
                        }
                    }
                } label: {
                    Label("选择互动...", systemImage: "hand.tap")
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color(.controlBackgroundColor))
                        )
                }
                .menuStyle(.borderlessButton)
            }

            Text("提示：互动需要先加载角色。")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    // MARK: 互动面板

    private var interactSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("触发互动")
                .font(.title3)
                .bold()

            if let id = selectedInteractionID,
               let interaction = viewModel.interactions.first(where: { $0.id == id }) {
                Text("互动：\(interaction.displayName)")
                    .foregroundStyle(.secondary)
            }

            TextField("上下文（如：location=home, time_of_day=evening）", text: $contextText)
                .textFieldStyle(.roundedBorder)

            Toggle("允许 NSFW 回应", isOn: $nsfwAllowed)

            HStack {
                Spacer()
                Button("取消") { showInteract = false }
                Button("触发") {
                    Task {
                        let context = parseContext(contextText)
                        let result = await viewModel.trigger(
                            selectedInteractionID ?? "",
                            characterID: characterID,
                            context: context.isEmpty ? nil : context,
                            nsfwAllowed: nsfwAllowed
                        )
                        interactResult = result ?? "互动触发失败"
                        showInteract = false
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(selectedInteractionID == nil)
            }
        }
        .padding(20)
        .frame(width: 420)
    }

    /// 解析 "k=v, k2=v2" 形式的上下文
    private func parseContext(_ text: String) -> [String: JSONValue] {
        var result: [String: JSONValue] = [:]
        for part in text.split(separator: ",") {
            let kv = part.split(separator: "=", maxSplits: 1)
            guard kv.count == 2 else { continue }
            let key = kv[0].trimmingCharacters(in: .whitespaces)
            let value = kv[1].trimmingCharacters(in: .whitespaces)
            if let number = Double(value) {
                result[key] = .number(number)
            } else if value == "true" {
                result[key] = .bool(true)
            } else if value == "false" {
                result[key] = .bool(false)
            } else {
                result[key] = .string(value)
            }
        }
        return result
    }

    // MARK: 状态编辑器

    private var stateEditorSheet: some View {
        StateEditorSheet(viewModel: viewModel, characterID: characterID)
    }
}

/// 角色状态编辑器：按 key 输入数值
struct StateEditorSheet: View {
    @Bindable var viewModel: CharacterViewModel
    let characterID: String

    @Environment(\.dismiss) private var dismiss
    @State private var fields: [String: String] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("编辑角色状态")
                .font(.title3)
                .bold()
            Text("填写要更新的状态字段（如 intimacy、mood、energy），留空则不改动。")
                .font(.caption)
                .foregroundStyle(.secondary)

            Form {
                ForEach(Array(fields.keys.sorted()), id: \.self) { key in
                    TextField(key, text: Binding(
                        get: { fields[key] ?? "" },
                        set: { fields[key] = $0 }
                    ))
                }

                HStack {
                    TextField("新字段名", text: $newKey)
                        .textFieldStyle(.roundedBorder)
                    Button("添加字段") {
                        let trimmed = newKey.trimmingCharacters(in: .whitespaces)
                        if !trimmed.isEmpty && fields[trimmed] == nil {
                            fields[trimmed] = ""
                        }
                        newKey = ""
                    }
                }
            }
            .formStyle(.grouped)

            HStack {
                Spacer()
                Button("取消") { dismiss() }
                Button("保存") {
                    Task { await save() }
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 440)
        .onAppear {
            if let state = viewModel.state {
                for entry in state.sortedEntries {
                    if let value = entry.value.stringValue {
                        fields[entry.key] = value
                    }
                }
            }
        }
    }

    @State private var newKey = ""

    private func save() async {
        var patch: [String: JSONValue] = [:]
        for (key, value) in fields {
            let trimmed = value.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty else { continue }
            if let number = Double(trimmed) {
                patch[key] = .number(number)
            } else if trimmed == "true" {
                patch[key] = .bool(true)
            } else if trimmed == "false" {
                patch[key] = .bool(false)
            } else {
                patch[key] = .string(trimmed)
            }
        }
        guard !patch.isEmpty else {
            dismiss()
            return
        }
        await viewModel.updateState(characterID, patch: patch)
        dismiss()
    }
}

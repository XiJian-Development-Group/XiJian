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
    @State private var showStatSlider = false
    @State private var statSliderDimension: CharacterStatusDimension = .hunger

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            if let detail = viewModel.detail {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        personaSection(detail)
                        characterStatusSection
                        rawStateSection
                        Divider()
                        actionsSection(detail)
                    }
                    .padding(16)
                }
            } else {
                ProgressView(loc("加载中..."))
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
        .sheet(isPresented: $showStatSlider) {
            CharacterStatSliderSheet(
                viewModel: viewModel,
                characterID: characterID,
                dimension: statSliderDimension
            )
        }
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .alert(loc("互动结果"), isPresented: Binding(
            get: { interactResult != nil },
            set: { if !$0 { interactResult = nil } }
        )) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(interactResult ?? "")
        }
        .task {
            await viewModel.loadDetail(characterID)
            await viewModel.loadInteractions()
        }
        .onChange(of: viewModel.showError) { _, newValue in
            if newValue {
                errorMessage = viewModel.errorMessage ?? loc("未知错误")
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
                Text(viewModel.detail?.displayName ?? loc("角色"))
                    .font(.title2)
                    .bold()
                if let emotion = viewModel.detail?.default_emotion {
                    Text(loc("情绪基线：%@", emotion))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button {
                Task { await viewModel.toggleLoaded(characterID) }
            } label: {
                Label(
                    viewModel.detail?.isLoaded == true ? loc("卸载") : loc("加载"),
                    systemImage: viewModel.detail?.isLoaded == true ? "eject.fill" : "play.fill"
                )
            }
            .buttonStyle(.bordered)
            Button(loc("编辑")) { showEdit = true }
                .buttonStyle(.bordered)
            Button(loc("关闭")) { dismiss() }
                .buttonStyle(.bordered)
        }
        .padding(14)
    }

    // MARK: 人设

    private func personaSection(_ detail: CharacterInfo) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(loc("人设文档"), systemImage: "doc.text")
                .font(.headline)
            Text(detail.persona_doc?.isEmpty == false ? detail.persona_doc! : loc("（未填写人设）"))
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

    // MARK: 角色状态面板（A3.2 四维环形 + 变更日志）

    @ViewBuilder
    private var characterStatusSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(loc("角色状态"), systemImage: "gauge")
                    .font(.headline)
                Spacer()
                if let summary = viewModel.state?.summary {
                    statusChip(summary)
                }
                Button(loc("刷新")) {
                    Task { await viewModel.refreshState() }
                }
                .controlSize(.small)
                Button(loc("编辑状态")) { showStateEditor = true }
                    .controlSize(.small)
            }

            if viewModel.isRefreshingState && viewModel.state == nil {
                // 加载态
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                    Text(loc("状态加载中..."))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else if let summary = viewModel.state?.summary {
                // 正常态：四维环形 + 变更日志
                statRings(summary)
                stateEventsSection
            } else if viewModel.stateLoadFailed {
                // 加载失败：显式提示 + 重试
                HStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                    Text(loc("状态加载失败"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button(loc("重试")) {
                        Task { await viewModel.refreshState() }
                    }
                    .controlSize(.small)
                }
            } else {
                // 空态：角色刚创建 / 从未被状态系统触碰
                Text(loc("角色尚未初始化状态（无状态记录）"))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    /// 状态徽标（Critical 红色告警，其余普通胶囊）
    @ViewBuilder
    private func statusChip(_ summary: CharacterStateSummary) -> some View {
        if summary.isCritical {
            Label(summary.statusDisplayName, systemImage: "exclamationmark.triangle.fill")
                .font(.caption2)
                .padding(.horizontal, 8)
                .padding(.vertical, 2)
                .background(Capsule().fill(Color.red.opacity(0.2)))
                .foregroundStyle(.red)
                .help(loc("健康 ≤ 0：角色已不可对话，仅能通过恢复操作解除"))
        } else {
            Text(summary.statusDisplayName)
                .font(.caption2)
                .padding(.horizontal, 8)
                .padding(.vertical, 2)
                .background(Capsule().fill(Color.secondary.opacity(0.15)))
                .foregroundStyle(.secondary)
        }
    }

    /// 四维环形进度条（饱食 / 饮水 / 健康 / 心情）
    private func statRings(_ summary: CharacterStateSummary) -> some View {
        HStack(spacing: 12) {
            ForEach(CharacterStatusDimension.allCases) { dimension in
                CharacterStatRing(
                    title: dimension.displayName,
                    icon: dimension.iconName,
                    value: summary.value(for: dimension),
                    max: summary.max(for: dimension),
                    color: statColor(dimension)
                ) {
                    statSliderDimension = dimension
                    showStatSlider = true
                }
                .frame(maxWidth: .infinity)
            }
        }
    }

    /// 状态变更日志（最近 10 条：时间 / 维度 / 来源 / 数值变化）
    @ViewBuilder
    private var stateEventsSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(loc("状态变更记录（最近 %lld 条）", min(viewModel.stateEvents.count, 10)))
                .font(.caption)
                .foregroundStyle(.secondary)

            if viewModel.stateEvents.isEmpty {
                Text(loc("暂无状态变更记录"))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            } else {
                VStack(spacing: 3) {
                    ForEach(viewModel.stateEvents.prefix(10), id: \.self) { entry in
                        stateEventRow(entry)
                    }
                }
            }
        }
    }

    private func stateEventRow(_ entry: CharacterStateLogEntry) -> some View {
        HStack(spacing: 8) {
            Text(entry.created_at.map { $0.xijianDate.xijianTimeText } ?? "--")
                .font(.caption2)
                .monospacedDigit()
                .foregroundStyle(.secondary)
                .frame(width: 88, alignment: .leading)
            Text(entry.fieldDisplayName)
                .font(.caption2)
                .frame(width: 40, alignment: .leading)
            Text(entry.reasonDisplayName)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Spacer()
            Text(entry.deltaText.isEmpty ? "—" : entry.deltaText)
                .font(.caption2)
                .monospacedDigit()
                .foregroundStyle(
                    entry.deltaSign > 0 ? .green : (entry.deltaSign < 0 ? .red : .secondary)
                )
        }
        .padding(.vertical, 2)
        .background(Color(.textBackgroundColor).opacity(0.4), in: RoundedRectangle(cornerRadius: 4))
    }

    /// 维度主题色
    private func statColor(_ dimension: CharacterStatusDimension) -> Color {
        switch dimension {
        case .hunger: return .orange
        case .thirst: return .blue
        case .health: return .red
        case .mood: return .purple
        }
    }

    // MARK: 原始字段（v1 文本字段 + A3.2 原始值，折叠展示）

    @ViewBuilder
    private var rawStateSection: some View {
        if let state = viewModel.state {
            DisclosureGroup {
                if state.values.isEmpty {
                    Text(loc("（暂无状态数据）"))
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
            } label: {
                Text(loc("原始字段"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .tint(.secondary)
        }
    }

    // MARK: 动作区

    private func actionsSection(_ detail: CharacterInfo) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(loc("互动"), systemImage: "sparkles")
                .font(.headline)

            if viewModel.interactions.isEmpty {
                Text(loc("（暂无可用互动类型）"))
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
                    Label(loc("选择互动..."), systemImage: "hand.tap")
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(
                            RoundedRectangle(cornerRadius: 8)
                                .fill(Color(.controlBackgroundColor))
                        )
                }
                .menuStyle(.borderlessButton)
            }

            Text(loc("提示：互动需要先加载角色。"))
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    // MARK: 互动面板

    private var interactSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(loc("触发互动"))
                .font(.title3)
                .bold()

            if let id = selectedInteractionID,
               let interaction = viewModel.interactions.first(where: { $0.id == id }) {
                Text(loc("互动：%@", interaction.displayName))
                    .foregroundStyle(.secondary)
            }

            TextField(loc("上下文（如：location=home, time_of_day=evening）"), text: $contextText)
                .textFieldStyle(.roundedBorder)

            Toggle(loc("允许 NSFW 回应"), isOn: $nsfwAllowed)

            HStack {
                Spacer()
                Button(loc("取消")) { showInteract = false }
                Button(loc("触发")) {
                    Task {
                        let context = parseContext(contextText)
                        let result = await viewModel.trigger(
                            selectedInteractionID ?? "",
                            characterID: characterID,
                            context: context.isEmpty ? nil : context,
                            nsfwAllowed: nsfwAllowed
                        )
                        interactResult = result ?? loc("互动触发失败")
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
            Text(loc("编辑角色状态"))
                .font(.title3)
                .bold()
            Text(loc("填写要更新的状态字段（如 intimacy、mood、energy），留空则不改动。"))
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
                    TextField(loc("新字段名"), text: $newKey)
                        .textFieldStyle(.roundedBorder)
                    Button(loc("添加字段")) {
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
                Button(loc("取消")) { dismiss() }
                Button(loc("保存")) {
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

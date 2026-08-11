import SwiftUI
import XiJianKit

/// 世界详情：状态视图、环境、地点转换、事件注入、状态编辑
struct WorldDetailView: View {
    @Bindable var viewModel: WorldViewModel
    let worldID: String

    @Environment(\.dismiss) private var dismiss
    @Environment(ThemeSettings.self) private var theme
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var selectedSection = 0

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if let state = viewModel.state {
                Picker("", selection: $selectedSection) {
                    Text(loc("状态与环境")).tag(0)
                    Text(loc("地点转换")).tag(1)
                    Text(loc("事件注入")).tag(2)
                    Text(loc("状态编辑")).tag(3)
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)

                Divider()

                ScrollView {
                    VStack(alignment: .leading, spacing: XJSpacing.md) {
                        switch selectedSection {
                        case 0: stateOverview(state)
                        case 1: transitionSection
                        case 2: eventSection
                        default: stateEditSection
                        }
                    }
                    .padding(XJSpacing.md)
                }
            } else {
                ProgressView(loc("加载世界状态中..."))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(width: 580, height: 660)
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .task {
            await viewModel.loadState(worldID)
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
                RoundedRectangle(cornerRadius: 8)
                    .fill(theme.accentColor.opacity(0.15))
                    .frame(width: 44, height: 44)
                Image(systemName: "globe.asia.australia")
                    .font(.title3)
                    .foregroundStyle(theme.accentColor)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(viewModel.state?.name ?? loc("世界"))
                    .font(.title2)
                    .bold()
                Text(worldID)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let state = viewModel.state, state.is_active == false {
                Button(loc("设为当前")) {
                    Task { await viewModel.switchActive(worldID) }
                }
                .buttonStyle(.bordered)
            }
            Button(loc("刷新")) {
                Task { await viewModel.loadState(worldID) }
            }
            .buttonStyle(.bordered)
            Button(loc("关闭")) { dismiss() }
                .buttonStyle(.bordered)
        }
        .padding(14)
    }

    // MARK: 状态与环境

    private func stateOverview(_ state: WorldStateInfo) -> some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            // 环境
            if let env = state.environment {
                VStack(alignment: .leading, spacing: XJSpacing.sm) {
                    Label(loc("环境"), systemImage: "cloud.sun")
                        .font(.headline)
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                        GridRow {
                            Text(loc("天气")).font(.caption).foregroundStyle(.secondary)
                            Text(env.weather ?? loc("未知")).textSelection(.enabled)
                        }
                        GridRow {
                            Text(loc("时段")).font(.caption).foregroundStyle(.secondary)
                            Text(env.time_of_day ?? loc("未知")).textSelection(.enabled)
                        }
                        GridRow {
                            Text(loc("亮度")).font(.caption).foregroundStyle(.secondary)
                            Text(env.light_level.map { String(format: "%.1f", $0) } ?? loc("未知"))
                        }
                        GridRow {
                            Text(loc("环境音")).font(.caption).foregroundStyle(.secondary)
                            Text(env.ambient_audio ?? loc("无")).textSelection(.enabled)
                        }
                    }
                }
                .xjCard(padding: XJSpacing.md)
            }

            // 计算配置
            if let config = state.compute_config {
                VStack(alignment: .leading, spacing: XJSpacing.sm) {
                    Label(loc("计算配置"), systemImage: "cpu")
                        .font(.headline)
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                        GridRow {
                            Text(loc("活跃层级")).font(.caption).foregroundStyle(.secondary)
                            Text(config.active_tier ?? loc("未知"))
                        }
                        GridRow {
                            Text(loc("NPC 上限")).font(.caption).foregroundStyle(.secondary)
                            Text("\(config.max_npcs ?? 0)")
                        }
                        GridRow {
                            Text(loc("活跃 NPC")).font(.caption).foregroundStyle(.secondary)
                            Text("\(config.max_active_npcs ?? 0)")
                        }
                        GridRow {
                            Text(loc("Token 预算")).font(.caption).foregroundStyle(.secondary)
                            Text("\(config.total_token_budget ?? 0)")
                        }
                    }
                }
                .xjCard(padding: XJSpacing.md)
            }

            // 自由状态字段（economy/health/diet/stamina/mentality）
            if !state.extra.isEmpty {
                VStack(alignment: .leading, spacing: XJSpacing.sm) {
                    Label(loc("状态维度"), systemImage: "gauge")
                        .font(.headline)
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                        ForEach(state.extra.sorted(by: { $0.key < $1.key }), id: \.key) { entry in
                            GridRow {
                                Text(entry.key).font(.caption).foregroundStyle(.secondary)
                                Text(entry.value.displayText).textSelection(.enabled)
                            }
                        }
                    }
                }
                .xjCard(padding: XJSpacing.md)
            }

            VStack(alignment: .leading, spacing: XJSpacing.sm) {
                Label(loc("路径"), systemImage: "folder")
                    .font(.headline)
                Text(state.world_doc_path ?? loc("未设置"))
                Text(state.config_path ?? loc("未设置"))
                Text(state.state_doc_path ?? loc("未设置"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .xjCard(padding: XJSpacing.md)
        }
    }

    // MARK: 地点转换

    @State private var fromLocation = ""
    @State private var toLocation = ""
    @State private var transport = "walking"
    @State private var etaSeconds = 900
    @State private var transitionMessage: String?

    private let transports = ["walking", "bicycle", "train", "taxi", "bus", "teleport"]

    private var transitionSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(loc("地点转换"), systemImage: "arrow.triangle.swap")
                .font(.headline)

            TextField(loc("出发地点"), text: $fromLocation)
                .textFieldStyle(.roundedBorder)
            TextField(loc("到达地点"), text: $toLocation)
                .textFieldStyle(.roundedBorder)

            Picker(loc("交通方式"), selection: $transport) {
                ForEach(transports, id: \.self) { Text($0) }
            }
            .pickerStyle(.segmented)

            HStack {
                Text(loc("预计耗时（秒）"))
                Stepper("\(etaSeconds)", value: $etaSeconds, in: 0...86400, step: 60)
            }

            Button(loc("执行转换")) {
                Task {
                    await viewModel.transition(
                        worldID,
                        fromLocation: fromLocation.isEmpty ? nil : fromLocation,
                        toLocation: toLocation.isEmpty ? nil : toLocation,
                        transport: transport,
                        etaSeconds: etaSeconds
                    )
                    transitionMessage = loc("转换已记录。")
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(toLocation.isEmpty)

            if let message = transitionMessage {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.green)
            }
        }
        .xjCard(padding: XJSpacing.md)
    }

    // MARK: 事件注入

    @State private var eventName = ""
    @State private var eventDescription = ""
    @State private var eventPriority = 0
    @State private var eventMessage: String?

    private var eventSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(loc("注入世界事件"), systemImage: "bolt.badge.clock")
                .font(.headline)

            TextField(loc("事件名称"), text: $eventName)
                .textFieldStyle(.roundedBorder)
            TextField(loc("事件描述"), text: $eventDescription)
                .textFieldStyle(.roundedBorder)
            Stepper(loc("优先级：%lld", eventPriority), value: $eventPriority, in: -10...10)

            Button(loc("注入事件")) {
                let name = eventName.trimmingCharacters(in: .whitespacesAndNewlines)
                let desc = eventDescription.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !name.isEmpty || !desc.isEmpty else { return }
                Task {
                    await viewModel.injectEvent(
                        worldID,
                        name: name.isEmpty ? "custom_event" : name,
                        description: desc,
                        sceneRefID: nil,
                        priority: eventPriority,
                        isEnabled: true
                    )
                    eventMessage = loc("事件已注入并触发。")
                    eventName = ""
                    eventDescription = ""
                }
            }
            .buttonStyle(.borderedProminent)

            if let message = eventMessage {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.green)
            }
        }
        .xjCard(padding: XJSpacing.md)
    }

    // MARK: 状态编辑

    @State private var stateFields: [String: JSONValue] = [:]
    @State private var selectedStateField = ""

    private var stateEditSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(loc("编辑状态维度"), systemImage: "slider.horizontal.3")
                .font(.headline)
            Text(loc("选择要更新的字段；数值用滑块，开关用开关，其余用文本框。"))
                .font(.caption)
                .foregroundStyle(.secondary)

            // 添加字段：候选来自预定义世界字段 + 当前已有字段（U2，不再手输字段名）
            HStack(spacing: 8) {
                Picker(loc("选择字段"), selection: $selectedStateField) {
                    ForEach(stateCandidates, id: \.self) { key in
                        Text(key).tag(key)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: .infinity)

                Button(loc("添加")) {
                    guard !selectedStateField.isEmpty, stateFields[selectedStateField] == nil else { return }
                    let numericKeys = Set(StateFieldCandidates.world)
                    stateFields[selectedStateField] = numericKeys.contains(selectedStateField) ? .number(0) : .string("")
                }
                .disabled(selectedStateField.isEmpty || stateFields[selectedStateField] != nil)
            }

            Form {
                ForEach(Array(stateFields.keys.sorted()), id: \.self) { key in
                    HStack(alignment: .top, spacing: 8) {
                        StateFieldRow(key: key, value: stateFields[key] ?? .string(""), maxValue: 100, edited: Binding(
                            get: { stateFields[key] ?? .string("") },
                            set: { stateFields[key] = $0 }
                        ))
                        Button {
                            stateFields.removeValue(forKey: key)
                        } label: {
                            Image(systemName: "trash")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .formStyle(.grouped)

            Button(loc("保存状态")) {
                var patch: [String: JSONValue] = [:]
                for (key, value) in stateFields {
                    if case .string(let s) = value, s.trimmingCharacters(in: .whitespaces).isEmpty {
                        continue
                    }
                    patch[key] = value
                }
                guard !patch.isEmpty else { return }
                Task {
                    await viewModel.updateState(worldID, patch: patch)
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .onAppear {
            if stateFields.isEmpty, let state = viewModel.state {
                for key in state.extra.keys.sorted() {
                    stateFields[key] = state.extra[key]
                }
            }
        }
    }

    /// 候选字段：预定义世界字段 + 当前已有字段
    private var stateCandidates: [String] {
        let existing = viewModel.state?.extra.keys.map { $0 } ?? []
        return StateFieldCandidates.merged(existing: existing, predefined: StateFieldCandidates.world)
    }
}

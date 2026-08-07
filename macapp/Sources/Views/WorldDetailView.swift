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
                    Text("状态与环境").tag(0)
                    Text("地点转换").tag(1)
                    Text("事件注入").tag(2)
                    Text("状态编辑").tag(3)
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)

                Divider()

                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        switch selectedSection {
                        case 0: stateOverview(state)
                        case 1: transitionSection
                        case 2: eventSection
                        default: stateEditSection
                        }
                    }
                    .padding(16)
                }
            } else {
                ProgressView("加载世界状态中...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(width: 580, height: 660)
        .alert("出错了", isPresented: $showError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .task {
            await viewModel.loadState(worldID)
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
                RoundedRectangle(cornerRadius: 8)
                    .fill(theme.accentColor.opacity(0.15))
                    .frame(width: 44, height: 44)
                Image(systemName: "globe.asia.australia")
                    .font(.title3)
                    .foregroundStyle(theme.accentColor)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(viewModel.state?.name ?? "世界")
                    .font(.title2)
                    .bold()
                Text(worldID)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let state = viewModel.state, state.is_active == false {
                Button("设为当前") {
                    Task { await viewModel.switchActive(worldID) }
                }
                .buttonStyle(.bordered)
            }
            Button("刷新") {
                Task { await viewModel.loadState(worldID) }
            }
            .buttonStyle(.bordered)
            Button("关闭") { dismiss() }
                .buttonStyle(.bordered)
        }
        .padding(14)
    }

    // MARK: 状态与环境

    private func stateOverview(_ state: WorldStateInfo) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            // 环境
            if let env = state.environment {
                VStack(alignment: .leading, spacing: 6) {
                    Label("环境", systemImage: "cloud.sun")
                        .font(.headline)
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                        GridRow {
                            Text("天气").font(.caption).foregroundStyle(.secondary)
                            Text(env.weather ?? "未知").textSelection(.enabled)
                        }
                        GridRow {
                            Text("时段").font(.caption).foregroundStyle(.secondary)
                            Text(env.time_of_day ?? "未知").textSelection(.enabled)
                        }
                        GridRow {
                            Text("亮度").font(.caption).foregroundStyle(.secondary)
                            Text(env.light_level.map { String(format: "%.1f", $0) } ?? "未知")
                        }
                        GridRow {
                            Text("环境音").font(.caption).foregroundStyle(.secondary)
                            Text(env.ambient_audio ?? "无").textSelection(.enabled)
                        }
                    }
                }
            }

            // 计算配置
            if let config = state.compute_config {
                VStack(alignment: .leading, spacing: 6) {
                    Label("计算配置", systemImage: "cpu")
                        .font(.headline)
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 4) {
                        GridRow {
                            Text("活跃层级").font(.caption).foregroundStyle(.secondary)
                            Text(config.active_tier ?? "未知")
                        }
                        GridRow {
                            Text("NPC 上限").font(.caption).foregroundStyle(.secondary)
                            Text("\(config.max_npcs ?? 0)")
                        }
                        GridRow {
                            Text("活跃 NPC").font(.caption).foregroundStyle(.secondary)
                            Text("\(config.max_active_npcs ?? 0)")
                        }
                        GridRow {
                            Text("Token 预算").font(.caption).foregroundStyle(.secondary)
                            Text("\(config.total_token_budget ?? 0)")
                        }
                    }
                }
            }

            // 自由状态字段（economy/health/diet/stamina/mentality）
            if !state.extra.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Label("状态维度", systemImage: "gauge")
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
            }

            VStack(alignment: .leading, spacing: 4) {
                Label("路径", systemImage: "folder")
                    .font(.headline)
                Text(state.world_doc_path ?? "未设置")
                Text(state.config_path ?? "未设置")
                Text(state.state_doc_path ?? "未设置")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
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
            Label("地点转换", systemImage: "arrow.triangle.swap")
                .font(.headline)

            TextField("出发地点", text: $fromLocation)
                .textFieldStyle(.roundedBorder)
            TextField("到达地点", text: $toLocation)
                .textFieldStyle(.roundedBorder)

            Picker("交通方式", selection: $transport) {
                ForEach(transports, id: \.self) { Text($0) }
            }
            .pickerStyle(.segmented)

            HStack {
                Text("预计耗时（秒）")
                Stepper("\(etaSeconds)", value: $etaSeconds, in: 0...86400, step: 60)
            }

            Button("执行转换") {
                Task {
                    await viewModel.transition(
                        worldID,
                        fromLocation: fromLocation.isEmpty ? nil : fromLocation,
                        toLocation: toLocation.isEmpty ? nil : toLocation,
                        transport: transport,
                        etaSeconds: etaSeconds
                    )
                    transitionMessage = "转换已记录。"
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
    }

    // MARK: 事件注入

    @State private var eventName = ""
    @State private var eventDescription = ""
    @State private var eventPriority = 0
    @State private var eventMessage: String?

    private var eventSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("注入世界事件", systemImage: "bolt.badge.clock")
                .font(.headline)

            TextField("事件名称", text: $eventName)
                .textFieldStyle(.roundedBorder)
            TextField("事件描述", text: $eventDescription)
                .textFieldStyle(.roundedBorder)
            Stepper("优先级：\(eventPriority)", value: $eventPriority, in: -10...10)

            Button("注入事件") {
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
                    eventMessage = "事件已注入并触发。"
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
    }

    // MARK: 状态编辑

    @State private var stateFields: [String: String] = [:]

    private var stateEditSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("编辑状态维度", systemImage: "slider.horizontal.3")
                .font(.headline)
            Text("支持 economy、health、diet、stamina、mentality 等字段；数值或文本均可。")
                .font(.caption)
                .foregroundStyle(.secondary)

            Form {
                ForEach(Array(stateFields.keys.sorted()), id: \.self) { key in
                    TextField(key, text: Binding(
                        get: { stateFields[key] ?? "" },
                        set: { stateFields[key] = $0 }
                    ))
                }
                HStack {
                    TextField("新字段名", text: $newStateKey)
                    Button("添加") {
                        let trimmed = newStateKey.trimmingCharacters(in: .whitespaces)
                        if !trimmed.isEmpty && stateFields[trimmed] == nil {
                            stateFields[trimmed] = ""
                        }
                        newStateKey = ""
                    }
                }
            }
            .formStyle(.grouped)

            Button("保存状态") {
                var patch: [String: JSONValue] = [:]
                for (key, value) in stateFields {
                    let trimmed = value.trimmingCharacters(in: .whitespaces)
                    guard !trimmed.isEmpty else { continue }
                    if let number = Double(trimmed) {
                        patch[key] = .number(number)
                    } else {
                        patch[key] = .string(trimmed)
                    }
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
                for entry in state.extra {
                    if let value = entry.value.stringValue {
                        stateFields[entry.key] = value
                    }
                }
            }
        }
    }

    @State private var newStateKey = ""
}

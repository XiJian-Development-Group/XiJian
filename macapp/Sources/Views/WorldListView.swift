import SwiftUI
import XiJianKit

/// 世界列表：查看、创建、删除、进入状态详情
struct WorldListView: View {
    @Bindable var viewModel: WorldViewModel
    @Environment(CoreManager.self) private var core
    @State private var showCreateSheet = false
    @State private var newWorldName = ""
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var selectedID: String?
    @State private var showDetail = false
    @State private var pendingDelete: WorldInfo?

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading && viewModel.worlds.isEmpty {
                    ProgressView("加载世界中...")
                } else if viewModel.worlds.isEmpty {
                    emptyState
                } else {
                    List {
                        ForEach(viewModel.worlds) { world in
                            Button {
                                selectedID = world.worldID
                                showDetail = true
                            } label: {
                                WorldRow(world: world) {
                                    pendingDelete = world
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .navigationTitle("世界")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showCreateSheet = true
                    } label: {
                        Label("新建世界", systemImage: "plus")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await viewModel.refresh() }
                    } label: {
                        Label("刷新", systemImage: "arrow.clockwise")
                    }
                }
            }
        }
        .sheet(isPresented: $showCreateSheet) {
            createSheet
        }
        .sheet(isPresented: $showDetail) {
            if let id = selectedID {
                WorldDetailView(viewModel: viewModel, worldID: id)
            }
        }
        .alert("删除世界", isPresented: Binding(
            get: { pendingDelete != nil },
            set: { if !$0 { pendingDelete = nil } }
        )) {
            Button("删除", role: .destructive) {
                if let world = pendingDelete {
                    Task { await viewModel.delete(world.worldID) }
                }
                pendingDelete = nil
            }
            Button("取消", role: .cancel) { pendingDelete = nil }
        } message: {
            Text("确定要删除世界「\(pendingDelete?.name ?? "")」吗？此操作不可撤销。")
        }
        .alert("出错了", isPresented: $showError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .task {
            await viewModel.refresh()
        }
        .onChange(of: viewModel.showError) { _, newValue in
            if newValue {
                errorMessage = viewModel.errorMessage ?? "未知错误"
                showError = true
                viewModel.showError = false
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "globe.asia.australia")
                .font(.system(size: 44))
                .foregroundStyle(.tertiary)
            Text("还没有世界")
                .font(.title3)
                .foregroundStyle(.secondary)
            Text("点击右上角 + 新建世界，或确认 Core 已启动")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var createSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("新建世界")
                .font(.title3)
                .bold()
            TextField("世界名称", text: $newWorldName)
                .textFieldStyle(.roundedBorder)
            HStack {
                Spacer()
                Button("取消") { showCreateSheet = false }
                Button("创建") {
                    let name = newWorldName.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !name.isEmpty else { return }
                    Task {
                        await viewModel.create(name: name)
                        showCreateSheet = false
                        newWorldName = ""
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(newWorldName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 360)
    }
}

/// 世界行
struct WorldRow: View {
    let world: WorldInfo
    var onDelete: () -> Void

    @Environment(ThemeSettings.self) private var theme

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(theme.accentColor.opacity(0.15))
                    .frame(width: 38, height: 38)
                Image(systemName: "globe.asia.australia")
                    .foregroundStyle(theme.accentColor)
            }

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(world.name ?? world.worldID)
                        .font(.headline)
                    if world.isActive {
                        Text("当前")
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(Color.blue.opacity(0.2)))
                            .foregroundStyle(.blue)
                    }
                }
                Text(world.worldID)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Button(action: onDelete) {
                Image(systemName: "trash")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help("删除世界")
        }
        .padding(.vertical, 4)
    }
}

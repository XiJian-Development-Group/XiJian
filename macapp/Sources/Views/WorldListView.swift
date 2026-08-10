import SwiftUI
import XiJianKit

/// 世界列表：查看、创建、删除、进入状态详情
struct WorldListView: View {
    @Bindable var viewModel: WorldViewModel
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @State private var showCreateSheet = false
    @State private var newWorldName = ""
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var selectedID: String?
    @State private var showDetail = false
    @State private var pendingDelete: WorldInfo?
    @State private var showImportSheet = false

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading && viewModel.worlds.isEmpty {
                    ProgressView(loc("加载世界中..."))
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
            .navigationTitle(loc("世界"))
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showImportSheet = true
                    } label: {
                        Label(loc("导入资源包"), systemImage: "square.and.arrow.down")
                    }
                    .disabled(!coreIsRunning)
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showCreateSheet = true
                    } label: {
                        Label(loc("新建世界"), systemImage: "plus")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await viewModel.refresh() }
                    } label: {
                        Label(loc("刷新"), systemImage: "arrow.clockwise")
                    }
                }
            }
        }
        .sheet(isPresented: $showImportSheet) {
            ImportPackSheet() {
                await viewModel.refresh()
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
        .alert(loc("删除世界"), isPresented: Binding(
            get: { pendingDelete != nil },
            set: { if !$0 { pendingDelete = nil } }
        )) {
            Button(loc("删除"), role: .destructive) {
                if let world = pendingDelete {
                    Task { await viewModel.delete(world.worldID) }
                }
                pendingDelete = nil
            }
            Button(loc("取消"), role: .cancel) { pendingDelete = nil }
        } message: {
            Text(loc("确定要删除世界「%@」吗？此操作不可撤销。", pendingDelete?.name ?? ""))
        }
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .task {
            await viewModel.refresh()
        }
        .onChange(of: viewModel.showError) { _, newValue in
            if newValue {
                errorMessage = viewModel.errorMessage ?? loc("未知错误")
                showError = true
                viewModel.showError = false
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: XJSpacing.md) {
            // Apple 风格：毛玻璃圆形容器 + 主题色图标（与 ChatView 空态一致）
            ZStack {
                Circle()
                    .fill(theme.accentColor.opacity(0.12))
                    .frame(width: 88, height: 88)
                Image(systemName: "globe.asia.australia")
                    .font(.system(size: 38))
                    .foregroundStyle(theme.accentColor)
            }
            .shadow(color: theme.accentColor.opacity(0.15), radius: 18, y: 8)
            Text(loc("还没有世界"))
                .font(.title2.bold())
                .foregroundStyle(.primary)
            Text(loc("点击右上角新建世界或导入资源包，或确认 Core 已启动"))
                .font(.body)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .xjFadeUp()
    }

    private var coreIsRunning: Bool {
        if case .running = core.state { return true }
        return false
    }

    private var createSheet: some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            Text(loc("新建世界"))
                .font(.title2.bold())
            TextField(loc("世界名称"), text: $newWorldName)
                .textFieldStyle(.roundedBorder)
            HStack {
                Spacer()
                Button(loc("取消")) { showCreateSheet = false }
                Button(loc("创建")) {
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
    @State private var isHovering = false

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
                        Text(loc("当前"))
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(Color.blue.opacity(0.2)))
                            .foregroundStyle(.blue)
                    }
                    if world.isFromPack {
                        Text(loc("资源包"))
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(Color.purple.opacity(0.2)))
                            .foregroundStyle(.purple)
                            .help(loc("来自资源包：%@", world.packID ?? ""))
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
            .help(loc("删除世界"))
        }
        .padding(.vertical, 4)
        .background(
            RoundedRectangle(cornerRadius: XJRadius.small, style: .continuous)
                .fill(isHovering ? Color.primary.opacity(0.05) : Color.clear)
        )
        .onHover { hovering in
            withAnimation(.spring(response: 0.4, dampingFraction: 1.0)) {
                isHovering = hovering
            }
        }
    }
}

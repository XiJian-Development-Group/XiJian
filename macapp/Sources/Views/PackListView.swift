import SwiftUI
import XiJianKit

/// 资源包管理：列表、导入、卸载、重新扫描
struct PackListView: View {
    @Bindable var viewModel: PackViewModel
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme
    @State private var showImportSheet = false
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var pendingUninstall: PackInfo?

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading && viewModel.packs.isEmpty {
                    ProgressView(loc("加载资源包中..."))
                } else if viewModel.packs.isEmpty {
                    emptyState
                } else {
                    List {
                        ForEach(viewModel.packs) { pack in
                            PackRow(pack: pack) {
                                pendingUninstall = pack
                            }
                        }
                    }
                }
            }
            .navigationTitle(loc("资源包"))
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
                        Task { await viewModel.refresh() }
                    } label: {
                        Label(loc("刷新"), systemImage: "arrow.clockwise")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await viewModel.rescan() }
                    } label: {
                        Label(loc("重新扫描"), systemImage: "arrow.triangle.2.circlepath")
                    }
                    .disabled(!coreIsRunning)
                }
            }
        }
        .sheet(isPresented: $showImportSheet) {
            ImportPackSheet() {
                await viewModel.refresh()
            }
        }
        .alert(loc("卸载资源包"), isPresented: Binding(
            get: { pendingUninstall != nil },
            set: { if !$0 { pendingUninstall = nil } }
        )) {
            Button(loc("卸载"), role: .destructive) {
                if let pack = pendingUninstall {
                    Task { await viewModel.uninstall(pack.package_id) }
                }
                pendingUninstall = nil
            }
            Button(loc("取消"), role: .cancel) { pendingUninstall = nil }
        } message: {
            Text(loc("卸载将删除包目录并移除其加载的角色/世界观/记忆记录，不可恢复。确定卸载「%@」吗？", pendingUninstall?.name ?? ""))
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
                Image(systemName: "shippingbox")
                    .font(.system(size: 38))
                    .foregroundStyle(theme.accentColor)
            }
            .shadow(color: theme.accentColor.opacity(0.15), radius: 18, y: 8)
            Text(loc("还没有资源包"))
                .font(.title2.bold())
                .foregroundStyle(.primary)
            Text(loc("可用 DevKit 导出资源包，或把 .7z/.zip 放入 Core 的 packs 目录后点击重新扫描"))
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
}

/// 资源包行
struct PackRow: View {
    let pack: PackInfo
    var onUninstall: () -> Void

    @State private var isHovering = false

    private var kindColor: Color {
        switch pack.kind {
        case "character": return .purple
        case "world": return .blue
        default: return .orange
        }
    }

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 8)
                    .fill(kindColor.opacity(0.15))
                    .frame(width: 38, height: 38)
                Image(systemName: "shippingbox")
                    .foregroundStyle(kindColor)
            }

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(pack.name)
                        .font(.headline)
                    Text(pack.displayKind)
                        .font(.caption2)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(kindColor.opacity(0.2)))
                        .foregroundStyle(kindColor)
                    if !pack.version.isEmpty {
                        Text("v\(pack.version)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                Text(pack.package_id)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let description = pack.manifest.description, !description.isEmpty {
                    Text(description)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }

            Spacer()

            Button(action: onUninstall) {
                Image(systemName: "trash")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help(loc("卸载资源包"))
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

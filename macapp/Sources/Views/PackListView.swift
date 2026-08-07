import SwiftUI
import XiJianKit

/// 资源包管理：列表、导入、卸载、重新扫描
struct PackListView: View {
    @Bindable var viewModel: PackViewModel
    @Environment(CoreManager.self) private var core
    @State private var showImportSheet = false
    @State private var showError = false
    @State private var errorMessage = ""
    @State private var pendingUninstall: PackInfo?

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.isLoading && viewModel.packs.isEmpty {
                    ProgressView("加载资源包中...")
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
            .navigationTitle("资源包")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showImportSheet = true
                    } label: {
                        Label("导入资源包", systemImage: "square.and.arrow.down")
                    }
                    .disabled(!coreIsRunning)
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await viewModel.refresh() }
                    } label: {
                        Label("刷新", systemImage: "arrow.clockwise")
                    }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        Task { await viewModel.rescan() }
                    } label: {
                        Label("重新扫描", systemImage: "arrow.triangle.2.circlepath")
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
        .alert("卸载资源包", isPresented: Binding(
            get: { pendingUninstall != nil },
            set: { if !$0 { pendingUninstall = nil } }
        )) {
            Button("卸载", role: .destructive) {
                if let pack = pendingUninstall {
                    Task { await viewModel.uninstall(pack.package_id) }
                }
                pendingUninstall = nil
            }
            Button("取消", role: .cancel) { pendingUninstall = nil }
        } message: {
            Text("卸载将删除包目录并移除其加载的角色/世界观/记忆记录，不可恢复。确定卸载「\(pendingUninstall?.name ?? "")」吗？")
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
            Image(systemName: "shippingbox")
                .font(.system(size: 44))
                .foregroundStyle(.tertiary)
            Text("还没有资源包")
                .font(.title3)
                .foregroundStyle(.secondary)
            Text("可用 DevKit 导出资源包，或把 .7z/.zip 放入 Core 的 packs 目录后点击重新扫描")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
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
            .help("卸载资源包")
        }
        .padding(.vertical, 4)
    }
}

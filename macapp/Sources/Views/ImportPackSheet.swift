import SwiftUI
import UniformTypeIdentifiers

/// 资源包导入阶段
enum ImportPhase {
    /// 选择文件
    case selecting
    /// 已选文件，待确认
    case ready(URL)
    /// 正在导入
    case importing
    /// 导入完成（含结果摘要）
    case done(ImportJobInfo)
    /// 导入失败（含错误描述）
    case failed(String)
}

/// 通用资源包导入 sheet（角色页与世界页共用）。
/// 导入成功后由调用方决定刷新哪些列表。
struct ImportPackSheet: View {
    let onImported: () async -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(CoreManager.self) private var core
    @State private var phase: ImportPhase = .selecting
    /// 当前选中的文件（importing 阶段展示文件名用）
    @State private var selectedURL: URL?

    var body: some View {
        Group {
            switch phase {
            case .selecting:
                selectingView
            case .ready(let url):
                readyView(url)
            case .importing:
                importingView
            case .done(let job):
                doneView(job)
            case .failed(let message):
                failedView(message)
            }
        }
        .padding(20)
        .frame(width: 480)
    }

    // MARK: 各阶段视图

    private var selectingView: some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            Text(loc("导入资源包"))
                .font(.title2.bold())
            Text(loc("支持 .7z/.zip 资源包，可包含角色与世界观。导入完成后可在对应页面查看。"))
                .font(.body)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button(loc("选择资源包…")) { chooseFile() }
                    .xjPrimaryButton(prominent: true)
            }
        }
        .xjFadeUp()
    }

    private func readyView(_ url: URL) -> some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            Text(loc("导入资源包"))
                .font(.title2.bold())
            VStack(alignment: .leading, spacing: 6) {
                Text(url.lastPathComponent)
                    .font(.headline)
                Text("\(fileSizeText(url)) · \(url.path)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .xjCard(padding: 12)
            HStack {
                Spacer()
                Button(loc("取消")) { dismiss() }
                Button(loc("重新选择")) { phase = .selecting }
                Button(loc("开始导入")) {
                    Task { await startImport() }
                }
                .xjPrimaryButton(prominent: true)
            }
        }
        .xjFadeUp()
    }

    private var importingView: some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            Text(loc("导入资源包"))
                .font(.title2.bold())
            HStack(spacing: 10) {
                ProgressView()
                Text(loc("正在导入 %@…", selectedURL?.lastPathComponent ?? loc("资源包")))
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 8)
            HStack {
                Spacer()
                Button(loc("取消")) { dismiss() }
            }
            Text(loc("关闭后导入仍在后台继续，完成后可在对应列表查看"))
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .xjFadeUp()
    }

    private func doneView(_ job: ImportJobInfo) -> some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            HStack(spacing: XJSpacing.sm) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text(loc("导入完成"))
                    .font(.title2.bold())
            }
            Text(loc("已导入角色 %lld 个、世界观 %lld 个、记忆 %lld 条", job.result?.loaded_characters ?? 0, job.result?.loaded_worlds ?? 0, job.result?.loaded_memories ?? 0))
                .foregroundStyle(.secondary)
            if let packageID = job.package_id, !packageID.isEmpty {
                Text(loc("包 ID：%@", packageID))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            HStack {
                Spacer()
                Button(loc("完成")) {
                    dismiss()
                    Task { await onImported() }
                }
                .xjPrimaryButton(prominent: true)
            }
        }
        .xjFadeUp()
    }

    private func failedView(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            HStack(spacing: XJSpacing.sm) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                Text(loc("导入失败"))
                    .font(.title2.bold())
            }
            Text(message)
                .font(.body)
                .foregroundStyle(.red)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button(loc("关闭")) { dismiss() }
            }
        }
        .xjFadeUp()
    }

    // MARK: 逻辑

    private func chooseFile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.zip, UTType(filenameExtension: "7z") ?? .data]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.message = loc("选择要导入的资源包（.7z/.zip）")
        if panel.runModal() == .OK, let url = panel.url {
            selectedURL = url
            phase = .ready(url)
        }
    }

    private func startImport() async {
        guard let url = selectedURL else { return }
        guard let client = core.makeClient() else {
            phase = .failed(loc("Core 未运行，无法导入资源包。"))
            return
        }
        phase = .importing
        do {
            let job = try await client.importResource(name: url.lastPathComponent, kind: "mixed", path: url.path)
            let final = try await client.pollImportJob(job.id)
            if final.isCompleted {
                phase = .done(final)
            } else if final.isFailed {
                phase = .failed(final.error ?? loc("导入失败"))
            } else {
                phase = .failed(loc("导入超时，请稍后在资源包页查看状态。"))
            }
        } catch {
            let message = (error as? APIError)?.message ?? error.localizedDescription
            phase = .failed(message)
        }
    }

    /// 文件大小文本（KB/MB）
    private func fileSizeText(_ url: URL) -> String {
        guard let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize else {
            return loc("大小未知")
        }
        return ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file)
    }
}

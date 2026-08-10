import SwiftUI
import XiJianKit

/// 备份与受保护模块：手动备份、列表、恢复、删除、自动备份开关
struct BackupSettingsView: View {
    @Environment(CoreManager.self) private var core
    @Environment(ThemeSettings.self) private var theme

    @State private var characters: [CharacterInfo] = []
    @State private var selectedCharacterID: String?

    @State private var modules: [ProtectedModule] = []
    @State private var backups: [BackupRecord] = []

    @State private var isLoading = false
    @State private var showError = false
    @State private var errorMessage = ""

    @State private var scope = "all"
    @State private var restoreTarget: BackupRecord?
    @State private var restoreScope = "all"
    @State private var restoreMessage: String?

    private let scopes = ["all", "memory_only", "state_only", "doc_only"]

    var body: some View {
        Form {
            Section(loc("角色")) {
                if characters.isEmpty {
                    Text(loc("暂无角色。"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Picker(loc("目标角色"), selection: $selectedCharacterID) {
                        ForEach(characters) { character in
                            Text(character.displayName).tag(String?.some(character.id))
                        }
                    }
                    .onChange(of: selectedCharacterID) {
                        Task { await load() }
                    }
                }
            }

            Section(loc("手动备份")) {
                HStack(spacing: XJSpacing.sm) {
                    Picker(loc("范围"), selection: $scope) {
                        ForEach(scopes, id: \.self) { Text(scopeDisplay($0)) }
                    }
                    .frame(width: 160)

                    Button(loc("创建备份")) {
                        Task { await createBackup() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(selectedCharacterID == nil)
                }

                if backups.isEmpty {
                    if isLoading {
                        ProgressView(loc("加载备份中..."))
                    } else {
                        Text(loc("暂无备份记录。"))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } else {
                    ForEach(backups) { backup in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Image(systemName: "externaldrive.fill")
                                    .foregroundStyle(theme.accentColor)
                                Text("\(backup.character_id ?? "?") · \(scopeDisplay(backup.scope ?? "all"))")
                                    .font(.subheadline)
                                Spacer()
                                if let created = backup.created_at {
                                    Text(created.xijianDate.xijianTimeText)
                                        .font(.caption)
                                        .foregroundStyle(.tertiary)
                                }
                            }
                            if let size = backup.size_bytes {
                                Text(loc("大小：%@", ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file)))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            HStack(spacing: XJSpacing.sm) {
                                Button(loc("恢复")) {
                                    restoreTarget = backup
                                    restoreScope = "all"
                                    restoreMessage = nil
                                }
                                .controlSize(.small)
                                Button(loc("删除"), role: .destructive) {
                                    Task { await deleteBackup(backup) }
                                }
                                .controlSize(.small)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }

            Section(loc("受保护模块")) {
                if modules.isEmpty {
                    Text(loc("加载中或暂无数据..."))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(modules) { module in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(module.module_name ?? "?")
                                    .font(.subheadline)
                                if let description = module.description {
                                    Text(description)
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            Toggle(loc("自动备份"), isOn: Binding(
                                get: { module.auto_backup ?? true },
                                set: { newValue in
                                    Task {
                                        await setAutoBackup(module: module, enabled: newValue)
                                    }
                                }
                            ))
                            .labelsHidden()
                            .toggleStyle(.switch)
                            .controlSize(.small)
                        }
                    }
                }
            }

            Section(loc("说明")) {
                Text(loc("备份文件为 zstd 压缩的 {character_id}_{ISO8601}_v{n}.bak，单角色最多保留 10 个版本。"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .navigationTitle(loc("备份与受保护模块"))
        .alert(loc("出错了"), isPresented: $showError) {
            Button(loc("好"), role: .cancel) {}
        } message: {
            Text(errorMessage)
        }
        .sheet(item: $restoreTarget) { backup in
            restoreSheet(backup)
        }
        .task {
            if let client = core.makeClient() {
                characters = (try? await client.listCharacters()) ?? []
                if selectedCharacterID == nil {
                    selectedCharacterID = characters.first?.id
                }
            }
            await load()
        }
    }

    // MARK: 子视图

    private func restoreSheet(_ backup: BackupRecord) -> some View {
        VStack(alignment: .leading, spacing: XJSpacing.md) {
            Text(loc("恢复备份"))
                .font(.title2.bold())
            Text(loc("备份 ID：%@", backup.backup_id))
                .font(.caption)
                .foregroundStyle(.secondary)

            Picker(loc("恢复范围"), selection: $restoreScope) {
                ForEach(scopes, id: \.self) { Text(scopeDisplay($0)) }
            }

            if let message = restoreMessage {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(message.contains(loc("恢复成功")) ? .green : .red)
            }

            HStack {
                Spacer()
                Button(loc("取消")) { restoreTarget = nil }
                Button(loc("执行恢复")) {
                    Task {
                        await restore(backup)
                    }
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 400)
    }

    // MARK: 动作

    private func load() async {
        guard let client = core.makeClient() else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            modules = try await client.listProtectedModules(characterID: selectedCharacterID)
            backups = try await client.listBackups(characterID: selectedCharacterID, limit: 50)
        } catch {
            presentError(error)
        }
    }

    private func createBackup() async {
        guard let client = core.makeClient(), let characterID = selectedCharacterID else { return }
        do {
            _ = try await client.createBackup(characterID: characterID, scope: scope, createdBy: "user")
            await load()
        } catch {
            presentError(error)
        }
    }

    private func deleteBackup(_ backup: BackupRecord) async {
        guard let client = core.makeClient() else { return }
        do {
            try await client.deleteBackup(backup.backup_id)
            backups.removeAll { $0.backup_id == backup.backup_id }
        } catch {
            presentError(error)
        }
    }

    private func restore(_ backup: BackupRecord) async {
        guard let client = core.makeClient() else { return }
        do {
            let result = try await client.restoreBackup(backup.backup_id, scope: restoreScope, targetCharacterID: selectedCharacterID)
            let summary = result["summary"]?.displayText ?? result["restored"]?.displayText ?? loc("恢复完成")
            restoreMessage = loc("恢复成功：%@", summary)
        } catch {
            if let apiError = error as? APIError {
                restoreMessage = loc("恢复失败：%@", apiError.message)
            } else {
                restoreMessage = loc("恢复失败：%@", error.localizedDescription)
            }
        }
    }

    private func setAutoBackup(module: ProtectedModule, enabled: Bool) async {
        guard let client = core.makeClient(), let characterID = selectedCharacterID,
              let moduleName = module.module_name else { return }
        do {
            _ = try await client.setAutoBackup(characterID: characterID, moduleName: moduleName, enabled: enabled)
            await load()
        } catch {
            presentError(error)
        }
    }

    // MARK: 辅助

    private func scopeDisplay(_ scope: String) -> String {
        switch scope {
        case "all": return loc("全部")
        case "memory_only": return loc("仅记忆")
        case "state_only": return loc("仅状态")
        case "doc_only": return loc("仅文档")
        default: return scope
        }
    }

    private func presentError(_ error: Error) {
        if let apiError = error as? APIError {
            errorMessage = apiError.message
        } else {
            errorMessage = error.localizedDescription
        }
        showError = true
    }
}

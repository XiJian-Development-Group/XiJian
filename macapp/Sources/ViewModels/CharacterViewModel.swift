import Foundation
import Observation

/// 角色 ViewModel：角色 CRUD、加载/卸载、互动、状态查看
@Observable
@MainActor
final class CharacterViewModel {

    // MARK: 状态

    private(set) var characters: [CharacterInfo] = []
    private(set) var isLoading = false
    private(set) var isMutating = false
    var errorMessage: String?
    var showError = false

    /// 选中角色详情
    private(set) var detail: CharacterInfo?
    /// 角色状态
    private(set) var state: CharacterStateInfo?
    /// 最近状态变更日志（最新在前，最多 10 条）
    private(set) var stateEvents: [CharacterStateLogEntry] = []
    /// 状态是否加载中（首屏 / 手动刷新）
    private(set) var isRefreshingState = false
    /// 最近一次状态加载/调整是否失败（供视图展示重试）
    private(set) var stateLoadFailed = false
    /// 互动列表
    private(set) var interactions: [InteractionInfo] = []

    private let core = CoreManager.shared

    // MARK: 列表

    func refresh() async {
        guard let client = core.makeClient() else {
            characters = []
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            characters = try await client.listCharacters()
        } catch {
            presentError(error)
        }
    }

    // MARK: 详情与状态

    func loadDetail(_ id: String) async {
        guard let client = core.makeClient() else { return }
        isRefreshingState = true
        defer { isRefreshingState = false }
        do {
            detail = try await client.getCharacter(id)
            if let detail {
                state = try? await client.getCharacterState(detail.id)
                stateEvents = (try? await client.getCharacterStateLog(detail.id, limit: 10)) ?? []
                stateLoadFailed = state == nil
            }
        } catch {
            presentError(error)
        }
    }

    /// 刷新角色状态与变更日志（幂等；失败置 stateLoadFailed 供视图重试）
    func refreshState() async {
        guard let id = detail?.id, let client = core.makeClient() else { return }
        isRefreshingState = true
        defer { isRefreshingState = false }
        do {
            state = try await client.getCharacterState(id)
            stateEvents = try await client.getCharacterStateLog(id, limit: 10)
            stateLoadFailed = false
        } catch {
            stateLoadFailed = true
            presentError(error)
        }
    }

    /// 调节单个状态维度并提交（Core 端点用 POST；失败时已触发错误提示）。
    /// 返回是否成功，供调用方决定是否关闭弹窗。
    @discardableResult
    func adjustState(_ dimension: CharacterStatusDimension, to value: Double) async -> Bool {
        guard let id = detail?.id, let client = core.makeClient() else { return false }
        do {
            state = try await client.updateCharacterState(id, patch: [dimension.rawValue: .number(value)])
            stateEvents = (try? await client.getCharacterStateLog(id, limit: 10)) ?? stateEvents
            stateLoadFailed = false
            return true
        } catch {
            stateLoadFailed = true
            presentError(error)
            return false
        }
    }

    // MARK: CRUD

    func create(name: String, displayName: String?, personaDoc: String, voiceProfile: String?, defaultEmotion: String?, tags: [String]) async {
        guard let client = core.makeClient() else {
            presentError(loc("Core 未运行，无法创建角色。"))
            return
        }
        isMutating = true
        defer { isMutating = false }
        do {
            let record = try await client.createCharacter(
                name: name, displayName: displayName, personaDoc: personaDoc,
                voiceProfile: voiceProfile, defaultEmotion: defaultEmotion, tags: tags
            )
            characters.append(record)
            detail = record
        } catch {
            presentError(error)
        }
    }

    func update(_ id: String, patch: [String: JSONValue]) async {
        guard let client = core.makeClient() else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            let updated = try await client.updateCharacter(id, patchBody: patch)
            if let idx = characters.firstIndex(where: { $0.id == id }) {
                characters[idx] = updated
            }
            detail = updated
        } catch {
            presentError(error)
        }
    }

    func delete(_ id: String) async {
        guard let client = core.makeClient() else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            try await client.deleteCharacter(id)
            characters.removeAll { $0.id == id }
            if detail?.id == id { detail = nil }
        } catch {
            presentError(error)
        }
    }

    // MARK: 加载 / 卸载

    func toggleLoaded(_ id: String) async {
        guard let client = core.makeClient() else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            let target = characters.first { $0.id == id }
            let updated: CharacterInfo
            if target?.isLoaded == true {
                updated = try await client.unloadCharacter(id)
            } else {
                updated = try await client.loadCharacter(id)
            }
            if let idx = characters.firstIndex(where: { $0.id == id }) {
                characters[idx] = updated
            }
            if detail?.id == id { detail = updated }
        } catch {
            presentError(error)
        }
    }

    // MARK: 互动

    func loadInteractions() async {
        guard let client = core.makeClient() else { return }
        do {
            interactions = try await client.listInteractions()
        } catch {
            presentError(error)
        }
    }

    /// 触发互动，返回响应文本
    func trigger(_ interactionID: String, characterID: String?, context: [String: JSONValue]?, nsfwAllowed: Bool) async -> String? {
        guard let client = core.makeClient() else { return nil }
        do {
            let result = try await client.triggerInteraction(
                interactionID: interactionID,
                characterID: characterID,
                context: context,
                nsfwAllowed: nsfwAllowed
            )
            // 响应包含 response / action 等字段
            var parts: [String] = []
            if let response = result["response"]?.stringValue {
                parts.append(loc("回应：%@", response))
            }
            if let reason = result["reason"]?.stringValue {
                parts.append(loc("原因：%@", reason))
            }
            if let action = result["action"] {
                parts.append(loc("动作：%@", action.displayText))
            }
            if let accepted = result["accepted"]?.boolValue {
                parts.append(accepted ? loc("互动已接受") : loc("互动被拒绝"))
            }
            return parts.isEmpty ? (result["message"]?.stringValue ?? loc("互动完成")) : parts.joined(separator: "\n")
        } catch {
            presentError(error)
            return nil
        }
    }

    // MARK: 状态更新

    func updateState(_ id: String, patch: [String: JSONValue]) async {
        guard let client = core.makeClient() else { return }
        do {
            state = try await client.updateCharacterState(id, patch: patch)
        } catch {
            presentError(error)
        }
    }

    // MARK: 错误

    private func presentError(_ message: String) {
        errorMessage = message
        showError = true
    }

    private func presentError(_ error: Error) {
        if let apiError = error as? APIError {
            presentError(apiError.message)
        } else {
            presentError(error.localizedDescription)
        }
    }
}

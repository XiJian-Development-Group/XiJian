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
        do {
            detail = try await client.getCharacter(id)
            if let detail {
                state = try? await client.getCharacterState(detail.id)
            }
        } catch {
            presentError(error)
        }
    }

    func refreshState() async {
        guard let id = detail?.id, let client = core.makeClient() else { return }
        do {
            state = try await client.getCharacterState(id)
        } catch {
            presentError(error)
        }
    }

    // MARK: CRUD

    func create(name: String, displayName: String?, personaDoc: String, voiceProfile: String?, defaultEmotion: String?, tags: [String]) async {
        guard let client = core.makeClient() else {
            presentError("Core 未运行，无法创建角色。")
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
                parts.append("回应：\(response)")
            }
            if let reason = result["reason"]?.stringValue {
                parts.append("原因：\(reason)")
            }
            if let action = result["action"] {
                parts.append("动作：\(action.displayText)")
            }
            if let accepted = result["accepted"]?.boolValue {
                parts.append(accepted ? "互动已接受" : "互动被拒绝")
            }
            return parts.isEmpty ? (result["message"]?.stringValue ?? "互动完成") : parts.joined(separator: "\n")
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

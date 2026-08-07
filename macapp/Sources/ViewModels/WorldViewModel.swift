import Foundation
import Observation

/// 世界 ViewModel：世界列表、状态、转换、事件
@Observable
@MainActor
final class WorldViewModel {

    // MARK: 状态

    private(set) var worlds: [WorldInfo] = []
    private(set) var isLoading = false
    var errorMessage: String?
    var showError = false

    /// 世界状态详情
    private(set) var state: WorldStateInfo?

    private let core = CoreManager.shared

    // MARK: 列表

    func refresh() async {
        guard let client = core.makeClient() else {
            worlds = []
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            worlds = try await client.listWorlds()
        } catch {
            presentError(error)
        }
    }

    // MARK: CRUD

    func create(name: String) async {
        guard let client = core.makeClient() else {
            presentError("Core 未运行，无法创建世界。")
            return
        }
        do {
            let world = try await client.createWorld(name: name)
            worlds.append(world)
        } catch {
            presentError(error)
        }
    }

    func delete(_ id: String) async {
        guard let client = core.makeClient() else { return }
        do {
            _ = try await client.deleteWorld(id)
            worlds.removeAll { $0.id == id }
        } catch {
            presentError(error)
        }
    }

    func switchActive(_ id: String) async {
        guard let client = core.makeClient() else { return }
        do {
            let updated = try await client.switchWorld(id)
            if let idx = worlds.firstIndex(where: { $0.id == id }) {
                worlds[idx] = updated
            }
        } catch {
            presentError(error)
        }
    }

    // MARK: 状态

    func loadState(_ id: String) async {
        guard let client = core.makeClient() else { return }
        do {
            state = try await client.getWorldState(id)
        } catch {
            presentError(error)
        }
    }

    func updateState(_ id: String, patch: [String: JSONValue]) async {
        guard let client = core.makeClient() else { return }
        do {
            _ = try await client.updateWorldState(id, patchBody: patch)
            await loadState(id)
        } catch {
            presentError(error)
        }
    }

    // MARK: 转换 / 事件

    func transition(_ id: String, fromLocation: String?, toLocation: String?, transport: String?, etaSeconds: Int?) async {
        guard let client = core.makeClient() else { return }
        do {
            _ = try await client.transitionWorld(id, fromLocation: fromLocation, toLocation: toLocation, transport: transport, etaSeconds: etaSeconds)
        } catch {
            presentError(error)
        }
    }

    func injectEvent(_ id: String, name: String, description: String, sceneRefID: String?, priority: Int, isEnabled: Bool) async {
        guard let client = core.makeClient() else { return }
        do {
            _ = try await client.injectWorldEvent(id, name: name, description: description, sceneRefID: sceneRefID, priority: priority, isEnabled: isEnabled)
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

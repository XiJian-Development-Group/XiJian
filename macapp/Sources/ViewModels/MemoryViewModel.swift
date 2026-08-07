import Foundation
import Observation

/// 记忆 ViewModel：条目 CRUD、搜索、整理与遗忘
@Observable
@MainActor
final class MemoryViewModel {

    // MARK: 状态

    private(set) var entries: [MemoryEntry] = []
    private(set) var searchResults: [MemoryEntry]?
    private(set) var isLoading = false
    private(set) var isSearching = false
    var errorMessage: String?
    var showError = false

    /// 搜索关键词
    var searchText: String = ""

    /// 清除搜索结果
    func clearSearch() {
        searchText = ""
        searchResults = nil
    }

    private let core = CoreManager.shared

    // MARK: 列表

    func refresh(characterID: String? = nil) async {
        guard let client = core.makeClient() else {
            entries = []
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            entries = try await client.listMemoryEntries(characterID: characterID, limit: 200)
        } catch {
            presentError(error)
        }
    }

    // MARK: CRUD

    func create(characterID: String?, content: String, importance: Double?, decay: String?, category: String?, tags: [String]) async {
        guard let client = core.makeClient() else {
            presentError("Core 未运行，无法创建记忆。")
            return
        }
        do {
            let entry = try await client.createMemoryEntry(
                characterID: characterID, content: content,
                importance: importance, decay: decay, category: category, tags: tags
            )
            entries.insert(entry, at: 0)
        } catch {
            presentError(error)
        }
    }

    func update(_ id: String, patch: [String: JSONValue]) async {
        guard let client = core.makeClient() else { return }
        do {
            let updated = try await client.updateMemoryEntry(id, patchBody: patch)
            if let idx = entries.firstIndex(where: { $0.id == id }) {
                entries[idx] = updated
            }
            if let idx = searchResults?.firstIndex(where: { $0.id == id }) {
                searchResults?[idx] = updated
            }
        } catch {
            presentError(error)
        }
    }

    func delete(_ id: String) async {
        guard let client = core.makeClient() else { return }
        do {
            try await client.deleteMemoryEntry(id)
            entries.removeAll { $0.id == id }
            searchResults?.removeAll { $0.id == id }
        } catch {
            presentError(error)
        }
    }

    // MARK: 搜索

    func search(characterID: String?, topK: Int = 10) async {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, let client = core.makeClient() else {
            searchResults = nil
            return
        }
        isSearching = true
        defer { isSearching = false }
        do {
            searchResults = try await client.searchMemory(query: query, characterID: characterID, topK: topK, minScore: nil)
        } catch {
            presentError(error)
        }
    }

    // MARK: 整理 / 遗忘

    func consolidate(characterID: String?) async {
        guard let client = core.makeClient() else { return }
        do {
            _ = try await client.consolidateMemory(characterID: characterID)
        } catch {
            presentError(error)
        }
    }

    func forget(entryIDs: [String], decay: String?) async {
        guard let client = core.makeClient() else { return }
        do {
            let result = try await client.forgetMemory(entryIDs: entryIDs.isEmpty ? nil : entryIDs, decay: decay)
            let removed = result["removed"]?.doubleValue.map { Int($0) } ?? 0
            if removed > 0 {
                for id in entryIDs {
                    entries.removeAll { $0.id == id }
                    searchResults?.removeAll { $0.id == id }
                }
            } else {
                await refresh()
            }
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

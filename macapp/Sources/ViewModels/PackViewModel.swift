import Foundation
import Observation

/// 资源包 ViewModel：资源包列表、卸载、重新扫描
@Observable
@MainActor
final class PackViewModel {

    // MARK: 状态

    private(set) var packs: [PackInfo] = []
    private(set) var isLoading = false
    private(set) var isMutating = false
    var errorMessage: String?
    var showError = false

    private let core = CoreManager.shared

    // MARK: 列表

    func refresh() async {
        guard let client = core.makeClient() else {
            packs = []
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            packs = try await client.listPacks()
        } catch {
            presentError(error)
        }
    }

    // MARK: 卸载

    func uninstall(_ packageID: String) async {
        guard let client = core.makeClient() else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            _ = try await client.uninstallPack(packageID)
            await refresh()
        } catch {
            presentError(error)
        }
    }

    // MARK: 重新扫描

    func rescan() async {
        guard let client = core.makeClient() else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            _ = try await client.rescanPacks()
            await refresh()
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

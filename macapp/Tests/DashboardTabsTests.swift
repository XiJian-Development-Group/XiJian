import XCTest
@testable import XiJianManagerKit

final class DashboardTabsTests: XCTestCase {

    private var tempDir: URL!
    private var manager: DashboardTabs!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("dash-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        manager = DashboardTabs(storePath: tempDir.appendingPathComponent("tabs.json"))
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        manager = nil
        try super.tearDownWithError()
    }

    func test_load_nonexistent_returnsDefaults() async throws {
        let tabs = try await manager.load()
        XCTAssertFalse(tabs.isEmpty)
        XCTAssertTrue(tabs.contains { $0.id == "状态" })
    }

    func test_setEnabled_persists() async throws {
        _ = try await manager.load()
        try await manager.setEnabled(id: "状态", enabled: false)
        let tabs = try await manager.load()
        XCTAssertEqual(tabs.first { $0.id == "状态" }?.enabled, false)
    }

    func test_reorder_updatesOrder() async throws {
        _ = try await manager.load()
        let original = try await manager.load()
        let ids = original.reversed().map { $0.id }
        try await manager.reorder(ids: ids)
        let reordered = try await manager.load()
        XCTAssertEqual(reordered.first?.id, ids.first)
    }

    func test_reorder_missingIds_keepsUnknown() async throws {
        _ = try await manager.load()
        try await manager.reorder(ids: ["不存在", "状态"])
        // 不存在的 id 被忽略；存在的被排到第 0 位
        let tabs = try await manager.load()
        XCTAssertEqual(tabs.first?.id, "状态")
    }
}

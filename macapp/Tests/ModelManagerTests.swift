import XCTest
@testable import XiJianManagerKit

final class ModelManagerTests: XCTestCase {

    private var tempDir: URL!
    private var manager: ModelManager!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("model-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        manager = ModelManager(configPath: tempDir.appendingPathComponent("config.yaml"))
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        manager = nil
        try super.tearDownWithError()
    }

    func test_load_nonexistent_returnsEmpty() async throws {
        let models = try await manager.load()
        XCTAssertTrue(models.isEmpty)
    }

    func test_add_andLoad() async throws {
        let model = ModelManager.Model(
            id: "openai:gpt-4o",
            provider: "openai",
            name: "gpt-4o",
            isActive: true
        )
        try await manager.add(model)
        let models = try await manager.load()
        XCTAssertEqual(models.count, 1)
        XCTAssertEqual(models.first?.name, "gpt-4o")
    }

    func test_setActive_marksCorrectOne() async throws {
        try await manager.add(ModelManager.Model(id: "a:1", provider: "a", name: "1", isActive: false))
        try await manager.add(ModelManager.Model(id: "a:2", provider: "a", name: "2", isActive: true))
        let models = try await manager.load()
        XCTAssertEqual(models.filter { $0.isActive }.count, 1)
        XCTAssertEqual(models.first { $0.isActive }?.name, "2")
    }

    func test_setActive_clearsOthers() async throws {
        try await manager.add(ModelManager.Model(id: "a:1", provider: "a", name: "1", isActive: true))
        try await manager.add(ModelManager.Model(id: "a:2", provider: "a", name: "2", isActive: true))
        try await manager.setActive(id: "a:1")
        let models = try await manager.load()
        let active = models.filter { $0.isActive }
        XCTAssertEqual(active.count, 1)
        XCTAssertEqual(active.first?.name, "1")
    }

    func test_remove_deletesModel() async throws {
        try await manager.add(ModelManager.Model(id: "a:1", provider: "a", name: "1"))
        try await manager.remove(id: "a:1")
        let models = try await manager.load()
        XCTAssertTrue(models.isEmpty)
    }
}

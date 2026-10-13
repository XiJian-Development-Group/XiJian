import XCTest
@testable import XiJianManagerKit

final class AgentGroupTests: XCTestCase {

    private var tempProfiles: URL!
    private var manager: AgentGroup!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempProfiles = FileManager.default.temporaryDirectory
            .appendingPathComponent("agent-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempProfiles, withIntermediateDirectories: true)
        manager = AgentGroup()
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tempProfiles)
        tempProfiles = nil
        manager = nil
        try super.tearDownWithError()
    }

    func test_addAgent() async {
        await manager.add(name: "test", profileName: "default")
        let agents = await manager.agents
        XCTAssertEqual(agents.count, 1)
        XCTAssertEqual(agents.first?.name, "test")
    }

    func test_removeAgent() async {
        await manager.add(name: "to-remove", profileName: "default")
        let agents = await manager.agents
        await manager.remove(id: agents.first!.id)
        let after = await manager.agents
        XCTAssertTrue(after.isEmpty)
    }

    func test_status_transitions() async {
        await manager.add(name: "test", profileName: "nonexistent-profile")
        let agents = await manager.agents
        let id = agents.first!.id
        // 启动一个不存在的 profile 应该 crashed（hermes 不在 path 上时）
        await manager.start(id: id)
        let after = await manager.agents
        // 状态是 crashed 或 stopped（取决于 hermes 路径是否存在）
        XCTAssertTrue(["crashed", "stopped"].contains(after.first?.status.rawValue))
    }
}

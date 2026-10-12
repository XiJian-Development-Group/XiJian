import XCTest
@testable import XiJianManagerKit

final class MCPManagerTests: XCTestCase {

    private var tempDir: URL!
    private var manager: MCPManager!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("mcp-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        manager = MCPManager(configPath: tempDir.appendingPathComponent("mcp.json"))
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        manager = nil
        try super.tearDownWithError()
    }

    func test_load_nonexistent_returnsEmpty() async throws {
        let config = try await manager.load()
        XCTAssertTrue(config.servers.isEmpty)
    }

    func test_addServer_savesToFile() async throws {
        let server = MCPManager.MCPServer(
            id: "github",
            command: "npx",
            args: ["-y", "@modelcontextprotocol/server-github"],
            env: ["GITHUB_TOKEN": "xxx"],
            enabled: true
        )
        try await manager.addServer(server)
        let loaded = try await manager.load()
        XCTAssertEqual(loaded.servers.count, 1)
        XCTAssertEqual(loaded.servers["github"]?.command, "npx")
    }

    func test_listServers_returnsSorted() async throws {
        try await manager.addServer(MCPManager.MCPServer(id: "zebra", command: "a"))
        try await manager.addServer(MCPManager.MCPServer(id: "alpha", command: "b"))
        try await manager.addServer(MCPManager.MCPServer(id: "middle", command: "c"))
        let servers = try await manager.listServers()
        XCTAssertEqual(servers.map { $0.id }, ["alpha", "middle", "zebra"])
    }

    func test_removeServer_deletes() async throws {
        try await manager.addServer(MCPManager.MCPServer(id: "removable", command: "x"))
        try await manager.removeServer(name: "removable")
        let loaded = try await manager.load()
        XCTAssertTrue(loaded.servers.isEmpty)
    }

    func test_toggleServer_disables() async throws {
        try await manager.addServer(MCPManager.MCPServer(id: "toggle", command: "x", enabled: true))
        try await manager.toggleServer(name: "toggle", enabled: false)
        let loaded = try await manager.load()
        XCTAssertEqual(loaded.servers["toggle"]?.enabled, false)
    }

    func test_toggleServer_notFound_throws() async throws {
        do {
            try await manager.toggleServer(name: "nonexistent", enabled: true)
            XCTFail("应抛错")
        } catch MCPError.notFound {
            // expected
        }
    }
}

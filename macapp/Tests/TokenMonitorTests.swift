import XCTest
@testable import XiJianManagerKit

final class TokenMonitorTests: XCTestCase {

    private var tempDir: URL!
    private var monitor: TokenMonitor!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("token-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        monitor = TokenMonitor(storePath: tempDir.appendingPathComponent("providers.json"))
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        monitor = nil
        try super.tearDownWithError()
    }

    func test_loadProviders_empty_returnsEmpty() async throws {
        let providers = try await monitor.loadProviders()
        XCTAssertTrue(providers.isEmpty)
    }

    func test_addProvider_persistsToFile() async throws {
        let provider = TokenMonitor.Provider(
            id: "openai",
            name: "OpenAI",
            endpoint: URL(string: "https://api.openai.com/v1/dashboard/billing/usage")!,
            apiKey: "sk-test"
        )
        try await monitor.addProvider(provider)
        let loaded = try await monitor.loadProviders()
        XCTAssertEqual(loaded.count, 1)
        XCTAssertEqual(loaded.first?.id, "openai")
    }

    func test_removeProvider_deletes() async throws {
        let provider = TokenMonitor.Provider(
            id: "to-remove",
            name: "Remove",
            endpoint: URL(string: "https://example.com")!
        )
        try await monitor.addProvider(provider)
        try await monitor.removeProvider(id: "to-remove")
        let loaded = try await monitor.loadProviders()
        XCTAssertTrue(loaded.isEmpty)
    }

    func test_refresh_invalidEndpoint_setsError() async throws {
        let provider = TokenMonitor.Provider(
            id: "invalid",
            name: "Invalid",
            endpoint: URL(string: "http://127.0.0.1:1/x")!,  // 永远拒绝连接
            apiKey: "test"
        )
        try await monitor.addProvider(provider)
        await monitor.refresh(providerID: "invalid")
        let providers = await monitor.providers
        XCTAssertNotNil(providers.first?.error, "expected error from unreachable endpoint")
        XCTAssertNotNil(providers.first?.lastFetched)
    }

    func test_usage_percentage() {
        var usage = TokenMonitor.Usage(usedTokens: 50, totalTokens: 100)
        XCTAssertEqual(usage.percentage, 0.5)
        usage.totalTokens = nil
        XCTAssertNil(usage.percentage)
    }
}

import XCTest
@testable import XiJianManagerKit

final class MemoryDocumentTests: XCTestCase {

    private var tempDir: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("memory-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        try super.tearDownWithError()
    }

    func test_load_nonexistent_returnsEmpty() throws {
        let doc = try MemoryDocument.load(kind: .memory, from: tempDir)
        XCTAssertEqual(doc.kind, .memory)
        XCTAssertEqual(doc.body, "")
    }

    func test_saveAndLoad_roundtrip() throws {
        let original = MemoryDocument(kind: .memory, body: "Alice 喜欢咖啡。")
        try original.save(to: tempDir)

        let loaded = try MemoryDocument.load(kind: .memory, from: tempDir)
        XCTAssertEqual(loaded.body, "Alice 喜欢咖啡。")
        XCTAssertEqual(loaded.kind, .memory)
    }

    func test_userKind_separateFromMemory() throws {
        let memory = MemoryDocument(kind: .memory, body: "memory content")
        let user = MemoryDocument(kind: .user, body: "user content")
        try memory.save(to: tempDir)
        try user.save(to: tempDir)

        let loadedMemory = try MemoryDocument.load(kind: .memory, from: tempDir)
        let loadedUser = try MemoryDocument.load(kind: .user, from: tempDir)
        XCTAssertEqual(loadedMemory.body, "memory content")
        XCTAssertEqual(loadedUser.body, "user content")
        XCTAssertEqual(loadedMemory.kind, .memory)
        XCTAssertEqual(loadedUser.kind, .user)
    }
}

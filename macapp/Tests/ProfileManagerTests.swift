import XCTest
@testable import XiJianManagerKit

final class ProfileManagerTests: XCTestCase {

    private var tempDir: URL!
    private var manager: ProfileManager!

    override func setUpWithError() throws {
        try super.setUpWithError()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("profile-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        manager = ProfileManager(hermesHome: tempDir)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        manager = nil
        try super.tearDownWithError()
    }

    func test_listProfiles_emptyHome_returnsDefault() async throws {
        let profiles = try await manager.listProfiles()
        // tempDir 是空的，只有 home 本身算 default
        XCTAssertEqual(profiles.count, 1)
        XCTAssertEqual(profiles.first?.id, "default")
        XCTAssertTrue(profiles.first?.isDefault ?? false)
    }

    func test_activeProfile_default_returnsDefault() async throws {
        let active = try await manager.activeProfile()
        XCTAssertEqual(active, "default")
    }

    func test_createProfile_createsDirectory() async throws {
        let profile = try await manager.createProfile(name: "alice")
        XCTAssertEqual(profile.id, "alice")
        XCTAssertFalse(profile.isDefault)
        let fm = FileManager.default
        XCTAssertTrue(fm.fileExists(atPath: profile.path.path))
        // 子目录已创建
        XCTAssertTrue(fm.fileExists(atPath: profile.path.appendingPathComponent("memories").path))
        XCTAssertTrue(fm.fileExists(atPath: profile.path.appendingPathComponent("skills").path))
    }

    func test_createProfile_invalidName_throws() async throws {
        do {
            _ = try await manager.createProfile(name: "bad name!")
            XCTFail("应该抛错")
        } catch ProfileError.invalidName {
            // expected
        }
    }

    func test_createProfile_duplicate_throws() async throws {
        _ = try await manager.createProfile(name: "bob")
        do {
            _ = try await manager.createProfile(name: "bob")
            XCTFail("应该抛错")
        } catch ProfileError.alreadyExists {
            // expected
        }
    }

    func test_setActiveProfile_writesFile() async throws {
        _ = try await manager.createProfile(name: "carol")
        try await manager.setActiveProfile("carol")
        let active = try await manager.activeProfile()
        XCTAssertEqual(active, "carol")
        // 文件确实写入
        let path = tempDir.appendingPathComponent("active_profile")
        let content = try String(contentsOf: path, encoding: .utf8)
        XCTAssertEqual(content, "carol")
    }

    func test_setActiveProfile_default_clearsFile() async throws {
        _ = try await manager.createProfile(name: "alice")
        try await manager.setActiveProfile("alice")
        // 验证已 active
        let activeBefore = try await manager.activeProfile()
        XCTAssertEqual(activeBefore, "alice")
        // 切回 default
        try await manager.setActiveProfile("default")
        let activeAfter = try await manager.activeProfile()
        XCTAssertEqual(activeAfter, "default")
        // 文件不存在
        let path = tempDir.appendingPathComponent("active_profile")
        XCTAssertFalse(FileManager.default.fileExists(atPath: path.path))
    }

    func test_setActiveProfile_notFound_throws() async throws {
        do {
            try await manager.setActiveProfile("nonexistent")
            XCTFail("应该抛错")
        } catch ProfileError.notFound {
            // expected
        }
    }

    func test_listProfiles_includesCreatedProfiles() async throws {
        _ = try await manager.createProfile(name: "alice")
        _ = try await manager.createProfile(name: "bob")
        let profiles = try await manager.listProfiles()
        XCTAssertEqual(profiles.count, 3)  // default + alice + bob
        XCTAssertTrue(profiles.contains { $0.id == "alice" })
        XCTAssertTrue(profiles.contains { $0.id == "bob" })
    }

    func test_deleteProfile_removesDirectory() async throws {
        _ = try await manager.createProfile(name: "delete-me")
        try await manager.deleteProfile("delete-me")
        let path = tempDir.appendingPathComponent("profiles/delete-me")
        XCTAssertFalse(FileManager.default.fileExists(atPath: path.path))
    }

    func test_deleteProfile_default_throws() async throws {
        do {
            try await manager.deleteProfile("default")
            XCTFail("应该抛错")
        } catch ProfileError.cannotDeleteDefault {
            // expected
        }
    }

    func test_listProfiles_marksActive() async throws {
        _ = try await manager.createProfile(name: "active-one")
        try await manager.setActiveProfile("active-one")
        let profiles = try await manager.listProfiles()
        let active = profiles.first { $0.isActive }
        XCTAssertEqual(active?.id, "active-one")
    }
}

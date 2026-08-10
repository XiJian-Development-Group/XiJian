import XCTest
@testable import XiJianKit

/// UserProfileSettings 持久化与操作测试
/// 注意：UserProfileSettings 直接读写 UserDefaults.standard，测试间会相互污染，
/// setUp 需清理 `xijian.profile.` 前缀键；不用独立 suite（源码读写 standard）。
@MainActor
final class UserProfileSettingsTests: XCTestCase {

    private var profile: UserProfileSettings!

    override func setUp() {
        super.setUp()
        clearProfileDefaults()
        profile = UserProfileSettings()
    }

    override func tearDown() {
        clearProfileDefaults()
        super.tearDown()
    }

    /// 清理 UserDefaults.standard 中所有 profile 键
    private func clearProfileDefaults() {
        let defaults = UserDefaults.standard
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix("xijian.profile.") {
            defaults.removeObject(forKey: key)
        }
    }

    // MARK: - 默认值

    func testDefaults() {
        XCTAssertEqual(profile.userName, "")
        XCTAssertTrue(profile.aliases.isEmpty)
        XCTAssertEqual(profile.identityDescription, "")
        XCTAssertFalse(profile.onboardingCompleted, "首次启动默认未完成引导")
        XCTAssertEqual(profile.aiSource, .local, "AI 来源默认本地")
        XCTAssertEqual(profile.notificationState, .notDetermined)
        XCTAssertFalse(profile.backgroundActivityEnabled)
    }

    // MARK: - 别称操作

    func testAddAliasTrimsAndIgnoresEmpty() {
        profile.addAlias("  小星  ")
        XCTAssertEqual(profile.aliases, ["小星"])
        profile.addAlias("   ")
        XCTAssertEqual(profile.aliases.count, 1, "空白别称应被忽略")
        profile.addAlias("小星")
        XCTAssertEqual(profile.aliases.count, 1, "重复别称应被忽略")
    }

    func testAppendEmptyAliasAddsSlot() {
        profile.appendEmptyAlias()
        XCTAssertEqual(profile.aliases, [""])
    }

    func testUpdateAlias() {
        profile.appendEmptyAlias()
        profile.updateAlias("阿岚", at: 0)
        XCTAssertEqual(profile.aliases, ["阿岚"])
        // 越界安全
        profile.updateAlias("x", at: 5)
        XCTAssertEqual(profile.aliases, ["阿岚"])
    }

    func testRemoveAlias() {
        profile.addAlias("A")
        profile.addAlias("B")
        profile.removeAlias(at: 0)
        XCTAssertEqual(profile.aliases, ["B"])
        profile.removeAlias(at: 9) // 越界安全
        XCTAssertEqual(profile.aliases, ["B"])
    }

    func testRemoveAliasOffsets() {
        profile.addAlias("A")
        profile.addAlias("B")
        profile.addAlias("C")
        profile.removeAlias(at: IndexSet([0, 2]))
        XCTAssertEqual(profile.aliases, ["B"])
    }

    // MARK: - AI 来源应用到 Core

    func testApplyAISourceRemoteConfiguresCore() {
        CoreManager.shared.resetForTesting()
        profile.aiSource = .remote
        profile.remoteEndpoint = "http://example.com:9000"
        profile.remoteToken = "tok-123"
        profile.applyAISourceToCore()
        XCTAssertTrue(CoreManager.shared.useCustomServer, "远程来源应启用自定义服务器")
        XCTAssertEqual(CoreManager.shared.customBaseURL, "http://example.com:9000")
        XCTAssertEqual(CoreManager.shared.customToken, "tok-123")
        // resetForTesting 不清 custom server 三字段，手动还原避免污染其他测试
        CoreManager.shared.useCustomServer = false
        CoreManager.shared.customBaseURL = ""
        CoreManager.shared.customToken = ""
        CoreManager.shared.resetForTesting()
    }

    func testApplyAISourceLocalDisablesCustomServer() {
        CoreManager.shared.resetForTesting()
        CoreManager.shared.useCustomServer = true
        profile.aiSource = .local
        profile.applyAISourceToCore()
        XCTAssertFalse(CoreManager.shared.useCustomServer, "本地来源应关闭自定义服务器")
        CoreManager.shared.resetForTesting()
    }
}

/// BackgroundSettings 类型推断与清除测试
/// 同样读写 UserDefaults.standard，setUp 清理 `xijian.background.` 前缀键。
@MainActor
final class BackgroundSettingsTests: XCTestCase {

    private var bg: BackgroundSettings!

    override func setUp() {
        super.setUp()
        let defaults = UserDefaults.standard
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix("xijian.background.") {
            defaults.removeObject(forKey: key)
        }
        bg = BackgroundSettings()
    }

    func testDefaultIsNone() {
        XCTAssertEqual(bg.kind, .none)
        XCTAssertNil(bg.filePath)
        XCTAssertFalse(bg.isBlurred)
    }

    func testApplyInfersKindByExtension() {
        bg.apply(fileURL: URL(fileURLWithPath: "/tmp/wall.png"))
        XCTAssertEqual(bg.kind, .image)
        bg.apply(fileURL: URL(fileURLWithPath: "/tmp/anim.gif"))
        XCTAssertEqual(bg.kind, .gif)
        bg.apply(fileURL: URL(fileURLWithPath: "/tmp/clip.mp4"))
        XCTAssertEqual(bg.kind, .video)
        bg.apply(fileURL: URL(fileURLWithPath: "/tmp/clip.mov"))
        XCTAssertEqual(bg.kind, .video)
        bg.apply(fileURL: URL(fileURLWithPath: "/tmp/photo.JPG"))
        XCTAssertEqual(bg.kind, .image, "扩展名应大小写不敏感")
        bg.apply(fileURL: URL(fileURLWithPath: "/tmp/unknown.xyz"))
        XCTAssertEqual(bg.kind, .image, "未知扩展名默认按图片处理")
    }

    func testClearResetsAll() {
        bg.apply(fileURL: URL(fileURLWithPath: "/tmp/a.gif"))
        bg.isBlurred = true
        bg.clear()
        XCTAssertEqual(bg.kind, .none)
        XCTAssertNil(bg.filePath)
        XCTAssertFalse(bg.isBlurred)
    }

    func testFileURLNilWhenPathMissing() {
        bg.filePath = "/nonexistent/xijian-test-file.png"
        XCTAssertNil(bg.fileURL, "文件不存在时 fileURL 应为 nil")
    }
}

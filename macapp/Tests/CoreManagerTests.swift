import XCTest
@testable import XiJianKit

/// CoreManager 路径与状态逻辑测试（不真正启动 Core 进程）
@MainActor
final class CoreManagerTests: XCTestCase {

    override func setUp() {
        super.setUp()
        CoreManager.shared.resetForTesting()
    }

    override func tearDown() {
        CoreManager.shared.resetForTesting()
        super.tearDown()
    }

    // MARK: - 路径

    func testCoreDirectoryResolvesUnderApplicationSupport() {
        let core = CoreManager.shared
        let dir = core.coreDirectory
        XCTAssertNotNil(dir, "coreDirectory 不应为 nil")
        XCTAssertTrue(dir!.path.contains("Library/Application Support/XiJian/Core"),
                      "coreDirectory 应为 ~/Library/Application Support/XiJian/Core，实际：\(dir!.path)")
    }

    func testAppSupportDirectory() {
        let core = CoreManager.shared
        XCTAssertTrue(core.appSupportDirectory.path.contains("XiJian"))
    }

    func testBaseURLUsesConfiguredPort() {
        let core = CoreManager.shared
        core.port = 18500
        core.useCustomServer = false
        XCTAssertEqual(core.baseURL.absoluteString, "http://127.0.0.1:18500")
        XCTAssertEqual(core.effectivePort, 18500)
    }

    func testBaseURLWithCustomServer() {
        let core = CoreManager.shared
        core.useCustomServer = true
        core.customBaseURL = "http://127.0.0.1:19999"
        XCTAssertEqual(core.baseURL.absoluteString, "http://127.0.0.1:19999")
        XCTAssertEqual(core.effectivePort, 19999)
        core.useCustomServer = false
    }

    func testPortClamping() {
        let core = CoreManager.shared
        core.port = 99999
        XCTAssertEqual(core.port, 65535, "端口应被钳制到 65535")
        core.port = 0
        XCTAssertEqual(core.port, 1, "端口应被钳制到 1")
        core.port = 18500
    }

    // MARK: - 状态机

    func testInitialStateIsStopped() {
        XCTAssertEqual(CoreManager.shared.state, .stopped)
    }

    func testMakeClientNilWhenStopped() {
        XCTAssertNil(CoreManager.shared.makeClient(), "未运行时 makeClient 应为 nil")
    }

    func testMakeClientWorksWhenRunning() {
        let core = CoreManager.shared
        core.setRunningForTesting(port: 18500, token: "test-token")
        let client = core.makeClient()
        XCTAssertNotNil(client)
        XCTAssertEqual(client?.baseURL.absoluteString, "http://127.0.0.1:18500")
        XCTAssertEqual(client?.token, "test-token")
    }

    func testMakeClientNilWhenRunningWithoutToken() {
        let core = CoreManager.shared
        core.setRunningForTesting(port: 18500, token: "")
        XCTAssertNil(core.makeClient(), "缺少 token 时不应生成客户端")
    }

    func testMakeClientUsesCustomServerWhenConfigured() {
        let core = CoreManager.shared
        core.useCustomServer = true
        core.customBaseURL = "http://127.0.0.1:21000"
        core.setRunningForTesting(port: 21000, token: "custom-token")
        let client = core.makeClient()
        XCTAssertNotNil(client, "自定义服务器模式应始终生成客户端")
        XCTAssertEqual(client?.baseURL.absoluteString, "http://127.0.0.1:21000")
        core.useCustomServer = false
        core.resetForTesting()
    }

    func testMakeClientUsesCustomTokenInCustomServerMode() {
        let core = CoreManager.shared
        let originalToken = core.customToken
        core.customToken = "custom-secret-token"
        core.useCustomServer = true
        core.customBaseURL = "http://127.0.0.1:21001"
        // 即使本机 token 已存在，自定义服务器模式也应使用 customToken
        core.setRunningForTesting(port: 21001, token: "local-token")
        let client = core.makeClient()
        XCTAssertNotNil(client, "自定义服务器模式应始终生成客户端")
        XCTAssertEqual(client?.token, "custom-secret-token", "自定义服务器模式应使用 customToken 作为 Bearer token")
        core.useCustomServer = false
        core.customToken = originalToken
        core.resetForTesting()
    }

    func testStartCoreSkipsLocalCoreWhenCustomServer() async {
        let core = CoreManager.shared
        // 即使 bundle 资源缺失，useCustomServer 守卫也应先行返回，不进入 error
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-test-custom-flag-\(UUID().uuidString)")
        core.bundleCoreOverride = missing
        core.isolatedCoreDirectoryOverride = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-isolated-custom-flag-\(UUID().uuidString)")

        core.useCustomServer = true
        await core.startCore()

        XCTAssertEqual(core.state, .customServer, "使用自定义服务器时不应启动本机 Core，也不应报错")
        XCTAssertNil(core.pid, "自定义服务器模式下不应产生本机 Core 进程 PID")

        core.useCustomServer = false
        core.bundleCoreOverride = nil
        core.isolatedCoreDirectoryOverride = nil
        core.resetForTesting()
    }

    func testRestartCoreInCustomServerModeStaysCustomServer() async {
        let core = CoreManager.shared
        core.bundleCoreOverride = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-test-custom-restart-\(UUID().uuidString)")
        core.isolatedCoreDirectoryOverride = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-isolated-custom-restart-\(UUID().uuidString)")

        core.useCustomServer = true
        await core.startCore()
        XCTAssertEqual(core.state, .customServer)

        // 自定义服务器模式下重启不应异常，最终应回到 customServer 状态
        await core.restartCore()
        XCTAssertEqual(core.state, .customServer, "自定义服务器模式下 restartCore 后应保持 customServer 状态")

        core.useCustomServer = false
        core.bundleCoreOverride = nil
        core.isolatedCoreDirectoryOverride = nil
        core.resetForTesting()
    }

    // MARK: - 启动失败路径（无 bundle Core 资源）

    func testStartCoreFailsWhenBundleCoreMissing() async {
        let core = CoreManager.shared
        // 测试环境没有嵌入的 Core 资源（也显式覆盖为不存在路径）
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-test-missing-\(UUID().uuidString)")
        core.bundleCoreOverride = missing
        // 隔离应用数据目录，避免污染真实目录
        let isolated = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-isolated-missing-\(UUID().uuidString)")
        core.isolatedCoreDirectoryOverride = isolated

        await core.startCore()

        guard case .error(let message) = core.state else {
            XCTFail("缺少内置 Core 时应进入 error 状态，实际：\(core.state)")
            return
        }
        XCTAssertTrue(message.contains("build-core.sh"), "错误信息应提示先构建 Core，实际：\(message)")
        core.bundleCoreOverride = nil
        core.isolatedCoreDirectoryOverride = nil
        try? FileManager.default.removeItem(at: isolated)
    }

    // MARK: - 复制失败路径

    func testStartCoreFailsWhenBundleCoreIsNotExecutable() async {
        let core = CoreManager.shared
        // 构造一个"伪 Core 目录"，包含不可执行的 xijian-api 文件
        let fakeBundle = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-fake-bundle-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: fakeBundle, withIntermediateDirectories: true)
        let exePath = fakeBundle.appendingPathComponent("xijian-api").path
        try? "not an executable".write(toFile: exePath, atomically: true, encoding: .utf8)

        // 隔离应用数据目录，避免污染真实目录
        let isolated = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-isolated-data-\(UUID().uuidString)")
        core.isolatedCoreDirectoryOverride = isolated

        core.bundleCoreOverride = fakeBundle
        await core.startCore()

        guard case .error(let message) = core.state else {
            XCTFail("不可执行的核心应进入 error 状态，实际：\(core.state)")
            return
        }
        let localizedPrefix = loc("Core 可执行文件不存在或不可执行：%@").replacingOccurrences(of: "%@", with: "")
        XCTAssertTrue(message.contains(localizedPrefix),
                      "错误信息应指向可执行文件问题，实际：\(message)")

        // 清理
        core.bundleCoreOverride = nil
        core.isolatedCoreDirectoryOverride = nil
        try? FileManager.default.removeItem(at: fakeBundle)
        try? FileManager.default.removeItem(at: isolated)
    }

    // MARK: - 重置数据（S1）

    func testResetCoreDataOnlyRemovesCoreSubdirectory() async {
        let core = CoreManager.shared
        // 构造隔离的应用数据目录：XiJian/ 下除 Core 外还有其它子目录（如 DevKit 残留）
        let base = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-reset-\(UUID().uuidString)")
        let appSupport = base.appendingPathComponent("XiJian")
        let coreDir = appSupport.appendingPathComponent("Core")
        let otherDir = appSupport.appendingPathComponent("DevKit")
        try? FileManager.default.createDirectory(at: coreDir, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: otherDir, withIntermediateDirectories: true)
        try? "core-file".write(to: coreDir.appendingPathComponent("xijian-api"), atomically: true, encoding: .utf8)
        try? "precious".write(to: otherDir.appendingPathComponent("devkit_config.json"), atomically: true, encoding: .utf8)
        core.isolatedCoreDirectoryOverride = coreDir

        await core.resetCoreData()

        XCTAssertFalse(FileManager.default.fileExists(atPath: coreDir.path),
                       "重置应删除 Core 子目录")
        XCTAssertTrue(FileManager.default.fileExists(atPath: otherDir.path),
                      "重置不得删除 XiJian/ 下其它子目录（DevKit 数据）")
        XCTAssertTrue(FileManager.default.fileExists(atPath: otherDir.appendingPathComponent("devkit_config.json").path),
                      "DevKit 配置不应被删除")

        core.isolatedCoreDirectoryOverride = nil
        try? FileManager.default.removeItem(at: base)
    }

    func testLogBufferAppendsAndCaps() {
        let core = CoreManager.shared
        core.appendLog("第一行")
        core.appendLog("第二行")
        XCTAssertEqual(core.recentLogs.map(\.message), ["第一行", "第二行"])
        core.appendLog("   ")
        XCTAssertEqual(core.recentLogs.count, 2, "空白行应被忽略")
        // 级别识别：默认进程输出为信息；含关键词行识别为对应级别
        core.appendLog("[XiJian] 已启动 Core 进程")
        core.appendLog("WARNING 磁盘空间不足")
        core.appendLog("ERROR: 请求失败")
        XCTAssertEqual(core.recentLogs.count, 5)
        XCTAssertEqual(core.recentLogs.last?.level, .error)
        XCTAssertTrue(core.recentLogs.contains { $0.level == .warning })
        XCTAssertTrue(core.recentLogs.contains { $0.level == .info })
    }

    func testLogBufferCapsAt1000Entries() {
        let core = CoreManager.shared
        for i in 0..<1010 {
            core.appendLog("第 \(i) 行")
        }
        XCTAssertEqual(core.recentLogs.count, 1000, "环形缓冲最多保留 1000 条")
        XCTAssertEqual(core.recentLogs.first?.message, "第 10 行")
        XCTAssertEqual(core.recentLogs.last?.message, "第 1009 行")
    }

    // MARK: - 端口文件 / 实际生效端口（端口被占用自动换端口）

    func testParsePortFileDataAcceptsValidPort() {
        XCTAssertEqual(CoreManager.parsePortFileData(Data("18500".utf8)), 18500)
        XCTAssertEqual(CoreManager.parsePortFileData(Data(" 18600 \n".utf8)), 18600, "应容忍首尾空白与换行")
        XCTAssertEqual(CoreManager.parsePortFileData(Data("1".utf8)), 1, "端口下限 1 应合法")
        XCTAssertEqual(CoreManager.parsePortFileData(Data("65535".utf8)), 65535, "端口上限 65535 应合法")
    }

    func testParsePortFileDataRejectsInvalidContent() {
        XCTAssertNil(CoreManager.parsePortFileData(Data()), "空内容应解析失败")
        XCTAssertNil(CoreManager.parsePortFileData(Data("  \n".utf8)), "纯空白应解析失败")
        XCTAssertNil(CoreManager.parsePortFileData(Data("abc".utf8)), "非数字应解析失败")
        XCTAssertNil(CoreManager.parsePortFileData(Data("18500.5".utf8)), "小数应解析失败")
        XCTAssertNil(CoreManager.parsePortFileData(Data("0".utf8)), "端口 0 应解析失败")
        XCTAssertNil(CoreManager.parsePortFileData(Data("70000".utf8)), "超上限端口应解析失败")
        XCTAssertNil(CoreManager.parsePortFileData(Data("-1".utf8)), "负数应解析失败")
        XCTAssertNil(CoreManager.parsePortFileData(Data([0xFF, 0xFE])), "非 UTF-8 内容应解析失败")
    }

    func testBaseURLUsesActivePortWhenCoreFellBack() {
        let core = CoreManager.shared
        core.port = 18500
        core.useCustomServer = false
        // 模拟 Core 因端口占用自动换到 18600（setRunningForTesting 会写入 activePort）
        core.setRunningForTesting(port: 18600, token: "test-token")
        XCTAssertEqual(core.activePort, 18600)
        XCTAssertEqual(core.effectivePort, 18600, "实际生效端口应优先于配置端口")
        XCTAssertEqual(core.baseURL.absoluteString, "http://127.0.0.1:18600")
        XCTAssertEqual(core.makeClient()?.baseURL.absoluteString, "http://127.0.0.1:18600",
                       "API 客户端应使用实际生效端口")
        core.resetForTesting()
    }

    func testBaseURLFallsBackToConfiguredPortAfterReset() {
        let core = CoreManager.shared
        core.port = 18500
        core.setRunningForTesting(port: 18600, token: "t")
        core.resetForTesting()
        XCTAssertNil(core.activePort, "重置后 activePort 应为 nil")
        XCTAssertEqual(core.effectivePort, 18500, "未确认实际端口时回落配置端口")
        XCTAssertEqual(core.baseURL.absoluteString, "http://127.0.0.1:18500")
    }
}

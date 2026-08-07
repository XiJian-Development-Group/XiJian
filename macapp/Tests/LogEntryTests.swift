import XCTest
@testable import XiJianKit

/// LogLevel / LogEntry 解析、筛选与 CoreManager 日志文件加载测试
@MainActor
final class LogEntryTests: XCTestCase {

    override func setUp() {
        super.setUp()
        CoreManager.shared.resetForTesting()
    }

    override func tearDown() {
        CoreManager.shared.resetForTesting()
        super.tearDown()
    }

    // MARK: - LogLevel

    func testLogLevelChineseDisplayNames() {
        XCTAssertEqual(LogLevel.debug.displayName, "调试")
        XCTAssertEqual(LogLevel.info.displayName, "信息")
        XCTAssertEqual(LogLevel.warning.displayName, "警告")
        XCTAssertEqual(LogLevel.error.displayName, "错误")
        XCTAssertEqual(LogLevel.critical.displayName, "严重")
        XCTAssertEqual(LogLevel.unknown.displayName, "未知")
    }

    func testLogLevelParseCaseInsensitive() {
        XCTAssertEqual(LogLevel.parse("DEBUG"), .debug)
        XCTAssertEqual(LogLevel.parse("debug"), .debug)
        XCTAssertEqual(LogLevel.parse("INFO"), .info)
        XCTAssertEqual(LogLevel.parse("Info"), .info)
        XCTAssertEqual(LogLevel.parse("WARNING"), .warning)
        XCTAssertEqual(LogLevel.parse("WARN"), .warning)
        XCTAssertEqual(LogLevel.parse("ERROR"), .error)
        XCTAssertEqual(LogLevel.parse("Critical"), .critical)
        XCTAssertEqual(LogLevel.parse("CRIT"), .critical)
    }

    func testLogLevelParseUnknown() {
        XCTAssertEqual(LogLevel.parse("TRACE"), .unknown)
        XCTAssertEqual(LogLevel.parse("BOGUS"), .unknown)
        XCTAssertEqual(LogLevel.parse(""), .unknown)
        XCTAssertEqual(LogLevel.parse("  "), .unknown)
    }

    // MARK: - Core 日志行解析

    func testParseCoreLogLineNormal() throws {
        let line = "[xijian-api] 2026-08-07 09:41:11 INFO    [xijian_api] 日志系统就绪"
        let entry = try XCTUnwrap(LogEntry.parseCoreLogLine(line))
        XCTAssertEqual(entry.level, .info)
        XCTAssertEqual(entry.source, .coreFile)
        XCTAssertEqual(entry.message, "[xijian_api] 日志系统就绪")
        XCTAssertEqual(entry.timestamp, LogEntry.coreTimestampFormatter.date(from: "2026-08-07 09:41:11"))
    }

    func testParseCoreLogLineUppercaseLevel() throws {
        let line = "[xijian-api] 2026-08-07 09:41:11 WARNING [xijian_api] 磁盘空间不足"
        let entry = try XCTUnwrap(LogEntry.parseCoreLogLine(line))
        XCTAssertEqual(entry.level, .warning)
        XCTAssertEqual(entry.message, "[xijian_api] 磁盘空间不足")
    }

    func testParseCoreLogLineUnknownLevelKeepsTimestamp() throws {
        let line = "[xijian-api] 2026-08-07 09:41:11 TRACE    [xijian_api] 未知级别"
        let entry = try XCTUnwrap(LogEntry.parseCoreLogLine(line))
        XCTAssertEqual(entry.level, .unknown)
        XCTAssertEqual(entry.timestamp, LogEntry.coreTimestampFormatter.date(from: "2026-08-07 09:41:11"))
        XCTAssertEqual(entry.message, "[xijian_api] 未知级别")
    }

    func testParseCoreLogLineMalformedHeader() throws {
        // 以 [xijian-api] 开头但没有时间与级别 → 未知级别条目，时间戳为 nil
        let line = "[xijian-api] 一些无法识别的输出"
        let entry = try XCTUnwrap(LogEntry.parseCoreLogLine(line))
        XCTAssertEqual(entry.level, .unknown)
        XCTAssertNil(entry.timestamp)
    }

    func testParseCoreLogLineContinuationReturnsNil() {
        XCTAssertNil(LogEntry.parseCoreLogLine("Traceback (most recent call last):"))
        XCTAssertNil(LogEntry.parseCoreLogLine("  File \"xijian_api/app.py\", line 353, in _build_app_resilient"))
    }

    // MARK: - 多行解析（续行合并）

    func testParseCoreLogLinesMergesTracebackContinuation() {
        let lines = [
            "[xijian-api] 2026-08-07 09:41:11 CRITICAL [xijian_api] 降级启动仍失败",
            "Traceback (most recent call last):",
            "  File \"xijian_api/app.py\", line 353, in _build_app_resilient",
            "",
            "[xijian-api] 2026-08-07 09:51:37 INFO    [xijian_api] 日志系统就绪",
        ]
        let entries = LogEntry.parseCoreLogLines(lines)
        XCTAssertEqual(entries.count, 2)
        let critical = entries[0]
        XCTAssertEqual(critical.level, .critical)
        XCTAssertTrue(critical.message.contains("Traceback (most recent call last):"),
                      "续行应归入上一行条目")
        XCTAssertTrue(critical.message.contains("  File \"xijian_api/app.py\", line 353"),
                      "多行 Traceback 内容应保留在消息中")
        XCTAssertEqual(entries[1].level, .info)
    }

    func testParseCoreLogLinesFirstLineContinuationBecomesUnknown() {
        let entries = LogEntry.parseCoreLogLines([
            "Traceback (most recent call last):",
            "  File \"a.py\", line 1",
        ])
        XCTAssertEqual(entries.count, 1)
        XCTAssertEqual(entries[0].level, .unknown)
        XCTAssertTrue(entries[0].message.contains("File \"a.py\""))
    }

    func testParseCoreLogLinesSkipsBlankLines() {
        let entries = LogEntry.parseCoreLogLines([
            "",
            "   ",
            "[xijian-api] 2026-08-07 09:41:11 INFO    消息",
            "",
        ])
        XCTAssertEqual(entries.count, 1)
        XCTAssertEqual(entries[0].level, .info)
    }

    // MARK: - 按级别筛选

    private func makeEntry(_ level: LogLevel) -> LogEntry {
        LogEntry(level: level, timestamp: nil, message: "m", source: .coreFile)
    }

    func testMatchesLevelFilterAll() {
        XCTAssertTrue(makeEntry(.debug).matches(levelFilter: nil))
        XCTAssertTrue(makeEntry(.critical).matches(levelFilter: nil))
        XCTAssertTrue(makeEntry(.unknown).matches(levelFilter: nil))
    }

    func testMatchesLevelFilterInfoAndAbove() {
        XCTAssertFalse(makeEntry(.debug).matches(levelFilter: .info), "调试应被信息筛选隐藏")
        XCTAssertTrue(makeEntry(.info).matches(levelFilter: .info))
        XCTAssertTrue(makeEntry(.warning).matches(levelFilter: .info))
        XCTAssertTrue(makeEntry(.error).matches(levelFilter: .info))
        XCTAssertTrue(makeEntry(.critical).matches(levelFilter: .info))
        XCTAssertTrue(makeEntry(.unknown).matches(levelFilter: .info), "未知级别行始终显示")
    }

    func testMatchesLevelFilterErrorAndAbove() {
        XCTAssertFalse(makeEntry(.info).matches(levelFilter: .error))
        XCTAssertFalse(makeEntry(.warning).matches(levelFilter: .error))
        XCTAssertTrue(makeEntry(.error).matches(levelFilter: .error))
        XCTAssertTrue(makeEntry(.critical).matches(levelFilter: .error))
        XCTAssertTrue(makeEntry(.unknown).matches(levelFilter: .error))
    }

    func testMatchesLevelFilterCriticalOnly() {
        XCTAssertFalse(makeEntry(.error).matches(levelFilter: .critical))
        XCTAssertTrue(makeEntry(.critical).matches(levelFilter: .critical))
        XCTAssertTrue(makeEntry(.unknown).matches(levelFilter: .critical))
    }

    func testMatchesLevelFilterDebugShowsAll() {
        XCTAssertTrue(makeEntry(.debug).matches(levelFilter: .debug))
        XCTAssertTrue(makeEntry(.info).matches(levelFilter: .debug))
        XCTAssertTrue(makeEntry(.critical).matches(levelFilter: .debug))
    }

    // MARK: - App 进程输出行解析

    func testParseAppLineXiJianPrefixIsInfo() {
        let entry = LogEntry.parseAppLine("[XiJian] 已启动 Core 进程 pid=123 port=18500")
        XCTAssertEqual(entry.level, .info)
        XCTAssertEqual(entry.source, .appProcess)
        XCTAssertNotNil(entry.timestamp, "进程输出行应记录捕获时间")
    }

    func testParseAppLineKeywordLevels() {
        XCTAssertEqual(LogEntry.parseAppLine("WARNING 磁盘空间不足").level, .warning)
        XCTAssertEqual(LogEntry.parseAppLine("ERROR: something broke").level, .error)
        XCTAssertEqual(LogEntry.parseAppLine("CRITICAL 无法继续").level, .critical)
        XCTAssertEqual(LogEntry.parseAppLine("[XiJian] Core 启动失败：ERROR 状态").level, .error,
                       "含关键词的 [XiJian] 行应识别为对应级别")
    }

    func testParseAppLineCoreFormatKeepsLevel() {
        let entry = LogEntry.parseAppLine("[xijian-api] 2026-08-07 09:41:11 WARNING [xijian_api] 应用初始化失败")
        XCTAssertEqual(entry.level, .warning)
        XCTAssertEqual(entry.source, .appProcess)
        XCTAssertNotNil(entry.timestamp)
    }

    // MARK: - loadCoreLogFile（临时文件）

    private func makeIsolatedCoreDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("xijian-log-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: dir.appendingPathComponent("logs"),
            withIntermediateDirectories: true
        )
        return dir
    }

    func testLoadCoreLogFileParsesSample() throws {
        let core = CoreManager.shared
        let dir = try makeIsolatedCoreDir()
        let logURL = dir.appendingPathComponent("logs").appendingPathComponent("xijian-api.log")
        let sample = """
        [xijian-api] 2026-08-07 09:41:11 INFO    [xijian_api] 日志系统就绪
        [xijian-api] 2026-08-07 09:41:11 WARNING [xijian_api] 应用初始化失败
        Traceback (most recent call last):
          File "xijian_api/app.py", line 353, in _build_app_resilient
        [xijian-api] 2026-08-07 09:51:37 ERROR    [xijian_api] 请求失败
        """
        try sample.write(to: logURL, atomically: true, encoding: .utf8)
        core.isolatedCoreDirectoryOverride = dir

        let entries = core.loadCoreLogFile()
        XCTAssertEqual(entries.count, 3)
        XCTAssertEqual(entries[0].level, .info)
        XCTAssertEqual(entries[1].level, .warning)
        XCTAssertTrue(entries[1].message.contains("Traceback"), "Traceback 续行应归入上一行")
        XCTAssertEqual(entries[2].level, .error)
        XCTAssertTrue(core.logFileExists)
        XCTAssertNil(core.logFileLoadError)

        core.isolatedCoreDirectoryOverride = nil
        try? FileManager.default.removeItem(at: dir)
    }

    func testLoadCoreLogFileMissingFile() throws {
        let core = CoreManager.shared
        let dir = try makeIsolatedCoreDir()
        core.isolatedCoreDirectoryOverride = dir

        let entries = core.loadCoreLogFile()
        XCTAssertTrue(entries.isEmpty, "日志文件不存在时应返回空数组")
        XCTAssertFalse(core.logFileExists)
        XCTAssertNil(core.logFileLoadError)

        core.isolatedCoreDirectoryOverride = nil
        try? FileManager.default.removeItem(at: dir)
    }

    func testLoadCoreLogFileLimitsLines() throws {
        let core = CoreManager.shared
        let dir = try makeIsolatedCoreDir()
        let logURL = dir.appendingPathComponent("logs").appendingPathComponent("xijian-api.log")
        var content = ""
        for i in 0..<100 {
            content += "[xijian-api] 2026-08-07 09:41:11 INFO    [xijian_api] 第 \(i) 行\n"
        }
        try content.write(to: logURL, atomically: true, encoding: .utf8)
        core.isolatedCoreDirectoryOverride = dir

        let entries = core.loadCoreLogFile(maxLines: 10)
        XCTAssertEqual(entries.count, 10, "应限制为最近 10 行")
        XCTAssertTrue(entries.first?.message.contains("第 90 行") == true, "应保留最近 10 行")
        XCTAssertTrue(entries.last?.message.contains("第 99 行") == true)

        core.isolatedCoreDirectoryOverride = nil
        try? FileManager.default.removeItem(at: dir)
    }

    // MARK: - refreshLogs 合并

    func testRefreshLogsMergesBufferAndFileWithDedup() throws {
        let core = CoreManager.shared
        let dir = try makeIsolatedCoreDir()
        let logURL = dir.appendingPathComponent("logs").appendingPathComponent("xijian-api.log")
        try "[xijian-api] 2026-08-07 09:41:11 INFO    [xijian_api] 文件里的日志\n"
            .write(to: logURL, atomically: true, encoding: .utf8)
        core.isolatedCoreDirectoryOverride = dir

        // 与文件内容相同的进程输出行应被去重（Core 会把 stdout 镜像到文件）
        core.appendLog("[xijian-api] 2026-08-07 09:41:11 INFO    [xijian_api] 文件里的日志")
        core.appendLog("[XiJian] 应用自身的日志")

        core.refreshLogs()

        XCTAssertEqual(core.logEntries.count, 2, "重复行应去重，保留文件行与应用自身行")
        XCTAssertEqual(core.logEntries[0].source, .coreFile)
        XCTAssertEqual(core.logEntries[1].source, .appProcess)
        XCTAssertEqual(core.logEntries[1].message, "[XiJian] 应用自身的日志")

        core.isolatedCoreDirectoryOverride = nil
        try? FileManager.default.removeItem(at: dir)
    }

    func testRefreshLogsWithoutFileKeepsBuffer() throws {
        let core = CoreManager.shared
        let dir = try makeIsolatedCoreDir()
        core.isolatedCoreDirectoryOverride = dir
        core.appendLog("[XiJian] 无文件时的进程输出")

        core.refreshLogs()

        XCTAssertFalse(core.logFileExists)
        XCTAssertEqual(core.logEntries.count, 1, "日志文件不存在时应保留全部进程输出")
        XCTAssertEqual(core.logEntries[0].source, .appProcess)

        core.isolatedCoreDirectoryOverride = nil
        try? FileManager.default.removeItem(at: dir)
    }
}

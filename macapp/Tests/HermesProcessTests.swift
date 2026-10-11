import XCTest
@testable import XiJianManagerKit

final class HermesProcessTests: XCTestCase {

    private var tempFiles: [URL] = []

    override func tearDown() {
        for url in tempFiles {
            try? FileManager.default.removeItem(at: url)
        }
        tempFiles.removeAll()
        super.tearDown()
    }

    private func tempFile(_ prefix: String) -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("\(prefix)-\(UUID().uuidString).log")
        tempFiles.append(url)
        return url
    }

    func test_startAndStop_localEcho() async throws {
        let logFile = tempFile("hermes-test")

        let config = HermesProcess.Config(
            command: "/bin/echo",
            arguments: ["hello"],
            logFile: logFile
        )
        let proc = HermesProcess(config: config)
        try await proc.start()
        let stateAfterStart = await proc.state
        XCTAssertEqual(stateAfterStart, .running)
        try await proc.stop(timeout: 2.0)
        try await Task.sleep(nanoseconds: 300_000_000)
        let stateAfterStop = await proc.state
        XCTAssertEqual(stateAfterStop, .stopped)
    }

    func test_healthCheck_usesInjectedCheck() async throws {
        let controller = HealthController()
        let logFile = tempFile("h")
        let config = HermesProcess.Config(
            command: "/bin/sleep",
            arguments: ["10"],
            logFile: logFile,
            healthCheck: { await controller.isHealthy() }
        )
        let proc = HermesProcess(config: config)
        try await proc.start()
        let stopTask = Task { try? await proc.stop(timeout: 2.0) }

        await controller.setHealthy(false)
        let result1 = await proc.isHealthy()
        XCTAssertFalse(result1)
        await controller.setHealthy(true)
        let result2 = await proc.isHealthy()
        XCTAssertTrue(result2)
        await stopTask.value
    }

    func test_restart_preservesProcessIdentity() async throws {
        let logFile = tempFile("hermes-test")
        let config = HermesProcess.Config(
            command: "/bin/sleep",
            arguments: ["30"],
            logFile: logFile
        )
        let proc = HermesProcess(config: config)
        try await proc.start()
        let id1 = await proc.id
        try await proc.restart(timeout: 2.0)
        let id2 = await proc.id
        XCTAssertEqual(id1, id2)
        let stateAfter = await proc.state
        XCTAssertEqual(stateAfter, .running)
        try await proc.stop(timeout: 2.0)
    }

    func test_logTailer_emitsExistingAndNewLines() async throws {
        let logFile = tempFile("tail-test")
        try "line 1\n".write(to: logFile, atomically: true, encoding: .utf8)

        let tailer = LogTailer()
        let collector = LineCollector()
        try await tailer.start(file: logFile) { line in
            Task { await collector.append(line) }
        }

        // 等待 poll 循环启动
        try await Task.sleep(nanoseconds: 200_000_000)
        // 追加新行
        let handle = try FileHandle(forWritingTo: logFile)
        try handle.seekToEnd()
        try handle.write(contentsOf: Data("line 2\n".utf8))
        try handle.close()
        // 多次 sleep 给 poll 触发机会
        for _ in 0..<10 {
            try await Task.sleep(nanoseconds: 100_000_000)
            if await collector.snapshot().contains("line 2") { break }
        }
        await tailer.stop()

        let lines = await collector.snapshot()
        XCTAssertTrue(lines.contains("line 1"), "应读到已有行 line 1，实际 \(lines)")
        XCTAssertTrue(lines.contains("line 2"), "应读到新追加行 line 2，实际 \(lines)")
    }
}

actor HealthController {
    private var healthy = false
    func setHealthy(_ value: Bool) { healthy = value }
    func isHealthy() -> Bool { healthy }
}

actor LineCollector {
    private var lines: [String] = []
    func append(_ line: String) { lines.append(line) }
    func snapshot() -> [String] { lines }
}

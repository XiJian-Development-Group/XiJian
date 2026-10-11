import XCTest
@testable import XiJianManagerKit

final class PTYTerminalTests: XCTestCase {

    func test_ptySpawn_echoCommand() async throws {
        let pty = PTYTerminal(config: .init(
            command: "/bin/echo",
            arguments: ["hello"]
        ))
        try await pty.spawn()
        let proc = await pty.process
        XCTAssertNotNil(proc)
        await pty.waitForExit()
    }

    func test_ptyWrite_readBackFromCat() async throws {
        let pty = PTYTerminal(config: .init(
            command: "/bin/cat",
            arguments: []
        ))
        try await pty.spawn()

        let collector = DataCollector()
        await pty.startReading { data in
            Task { await collector.append(data) }
        }

        // 等 cat 启动
        try await Task.sleep(nanoseconds: 300_000_000)
        try await pty.write("test_input\n")

        var found = false
        for _ in 0..<20 {
            try await Task.sleep(nanoseconds: 100_000_000)
            if await collector.contains("test_input") {
                found = true
                break
            }
        }
        XCTAssertTrue(found, "cat 应回显 test_input")

        await pty.terminate()
    }

    func test_ptyWrite_exit() async throws {
        let pty = PTYTerminal(config: .init(
            command: "/bin/cat",
            arguments: []
        ))
        try await pty.spawn()
        try await pty.write("first\n")
        try await Task.sleep(nanoseconds: 200_000_000)
        await pty.terminate()
        await pty.waitForExit()
    }
}

actor DataCollector {
    private var data = Data()
    func append(_ chunk: Data) { data.append(chunk) }
    func snapshot() -> Data { data }
    func contains(_ s: String) -> Bool {
        data.range(of: Data(s.utf8)) != nil
    }
}

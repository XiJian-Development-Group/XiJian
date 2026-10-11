import XCTest
@testable import XiJianManagerKit

final class HermesCommandRunnerTests: XCTestCase {

    func test_run_returnsCommandResult() async throws {
        let runner = HermesCommandRunner(hermesPath: URL(fileURLWithPath: "/bin/echo"))
        let result = try await runner.run(.gatewayStart, extraArguments: ["hello"])
        XCTAssertEqual(result.exitCode, 0)
        XCTAssertTrue(result.stdout.contains("hello"), "stdout 应包含 hello，实际：\(result.stdout)")
    }

    func test_run_nonZeroExit_returnsNonZero() async throws {
        let runner = HermesCommandRunner(hermesPath: URL(fileURLWithPath: "/usr/bin/false"))
        let result = try await runner.run(.gatewayStart)
        XCTAssertNotEqual(result.exitCode, 0)
    }

    func test_dashboardURL_returnsLocalhostURL() async {
        let runner = HermesCommandRunner(hermesPath: URL(fileURLWithPath: "/bin/echo"))
        let url = await runner.dashboardURL(port: 9119)
        XCTAssertEqual(url?.absoluteString, "http://127.0.0.1:9119/")
    }

    func test_checkDashboardReachable_returnsFalseWhenNoServer() async {
        let runner = HermesCommandRunner(hermesPath: URL(fileURLWithPath: "/bin/echo"))
        let reachable = await runner.checkDashboardReachable(port: 65535)
        XCTAssertFalse(reachable)
    }
}
